"""LLM gateway: the ONLY place an API key is used. Meters every call into a ledger and enforces spend caps.

Backend: Claude models served through ElevenLabs Agents (see llm_elevenlabs.py). Generated code never sees
the key. Each call is recorded immediately with a conservative token estimate (so caps are enforced before
the next call), then reconciled in the background with the exact usage ElevenLabs reports.
"""
import json
import re
import sqlite3
import threading
import time
from dataclasses import dataclass, field

from pydantic import BaseModel, ValidationError

from . import config, llm_elevenlabs
from .events import emit

_db_lock = threading.Lock()


class BudgetExceeded(RuntimeError):
    pass


def _db() -> sqlite3.Connection:
    con = sqlite3.connect(config.DB_PATH)
    con.execute(
        """CREATE TABLE IF NOT EXISTS ledger(
            id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, scope TEXT, purpose TEXT, model TEXT,
            input_tokens INT, output_tokens INT, usd REAL, credits REAL, seconds REAL,
            conversation_id TEXT, exact INT DEFAULT 0)"""
    )
    return con


def price(model: str, inp: int, out: int) -> float:
    pin, pout = config.PRICES.get(model, (4.0, 20.0))
    return (inp * pin + out * pout) / 1_000_000


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 3)  # deliberately conservative


@dataclass
class Budget:
    """A spend scope, e.g. one factory task or one workflow run. Hard-stops when exceeded."""
    scope: str
    max_usd: float
    max_tokens: int | None = None
    spent_usd: float = 0.0
    tokens: int = 0
    calls: int = 0
    created: float = field(default_factory=time.time)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def check(self) -> None:
        if self.calls >= config.MAX_LLM_CALLS_PER_TASK:
            raise BudgetExceeded(f"{self.scope}: call cap {config.MAX_LLM_CALLS_PER_TASK} reached")
        if self.spent_usd >= self.max_usd:
            raise BudgetExceeded(f"{self.scope}: spend cap ${self.max_usd:.2f} reached")
        if self.max_tokens is not None and self.tokens >= self.max_tokens:
            raise BudgetExceeded(f"{self.scope}: token cap {self.max_tokens} reached")

    def record(self, model: str, purpose: str, inp: int, out: int, seconds: float, conv_id: str) -> None:
        usd = price(model, inp, out)
        with self._lock:
            self.spent_usd += usd
            self.tokens += inp + out
            self.calls += 1
        with _db_lock:
            con = _db()
            cur = con.execute(
                "INSERT INTO ledger(ts,scope,purpose,model,input_tokens,output_tokens,usd,seconds,conversation_id) "
                "VALUES (?,?,?,?,?,?,?,?,?)", (time.time(), self.scope, purpose, model, inp, out, usd, seconds, conv_id))
            row_id = cur.lastrowid
            con.commit()
            con.close()
        emit("cost", scope=self.scope, purpose=purpose, tokens=inp + out, usd=round(usd, 5),
             total_usd=round(self.spent_usd, 4), total_tokens=self.tokens, seconds=round(seconds, 1))
        if conv_id:
            threading.Thread(target=_reconcile, args=(row_id, conv_id, model), daemon=True).start()


def _reconcile(row_id: int, conv_id: str, requested: str = "") -> None:
    for wait in (8, 15, 30):
        time.sleep(wait)
        try:
            u = llm_elevenlabs.usage(conv_id)
        except Exception:
            u = None
        if u and (u["input_tokens"] or u["output_tokens"]):
            if requested and u["models"] and requested not in u["models"]:
                emit("error", msg=f"model substitution: requested {requested}, provider served {u['models']} ({conv_id})")
            with _db_lock:
                con = _db()
                con.execute("UPDATE ledger SET input_tokens=?, output_tokens=?, usd=?, credits=?, exact=1, model=? WHERE id=?",
                            (u["input_tokens"], u["output_tokens"], u["usd"], u["credits"], ",".join(u["models"]) or requested, row_id))
                con.commit()
                con.close()
            return


def complete(budget: Budget, purpose: str, *, system: str, prompt: str, model: str | None = None,
             timeout: float = 300, until_json: bool = False) -> str:
    """One metered model call. Returns raw text. until_json: stop reading as soon as a full JSON object arrived."""
    budget.check()
    model = model or config.BUILD_MODEL
    text, conv_id, secs = llm_elevenlabs.ask(system, prompt, model, timeout=timeout, until_json=until_json)
    budget.record(model, purpose, estimate_tokens(system + prompt), estimate_tokens(text), secs, conv_id)
    return text


def extract_json(text: str):
    """Pull the first top-level JSON object out of a reply (tolerates ``` fences and stray prose)."""
    t = text.strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", t, re.S)
    if m:
        t = m.group(1).strip()
    start = t.find("{")
    if start < 0:
        raise ValueError("no JSON object in reply")
    obj, _ = json.JSONDecoder().raw_decode(t[start:])
    return obj


JSON_RULES = ("\n\nRespond with ONE JSON object only — no prose, no markdown fences. "
              "Escape newlines inside strings as \\n.")


def complete_json(budget: Budget, purpose: str, *, system: str, prompt: str, schema: type[BaseModel] | None = None,
                  model: str | None = None, retries: int = 2):
    """Model call that must return JSON (validated against `schema` when given). Retries with the error."""
    sys_prompt = system + JSON_RULES
    if schema is not None:
        sys_prompt += "\nJSON Schema of the required object:\n" + json.dumps(schema.model_json_schema())
    p = prompt
    for attempt in range(retries + 1):
        text = complete(budget, purpose, system=sys_prompt, prompt=p, model=model, until_json=True)
        try:
            obj = extract_json(text)
            return schema.model_validate(obj) if schema is not None else obj
        except (ValueError, ValidationError, json.JSONDecodeError) as e:
            if attempt == retries:
                raise RuntimeError(f"{purpose}: invalid JSON after {retries + 1} tries: {e}") from e
            cut = isinstance(e, json.JSONDecodeError) and ("Unterminated" in e.msg or e.pos >= len(e.doc.rstrip()) - 2)
            p = (f"{prompt}\n\nYour previous reply was cut off after {len(text)} characters, so the JSON never closed. "
                 "Reply again with a much shorter JSON object: every string under 300 characters, lists of at most 5 "
                 "items, no long quotes." if cut else
                 f"{prompt}\n\nYour previous reply was invalid ({str(e)[:400]}). Reply again with valid JSON only.")


def ledger_summary(scope_prefix: str = "") -> dict:
    with _db_lock:
        con = _db()
        row = con.execute(
            "SELECT COUNT(*), COALESCE(SUM(input_tokens+output_tokens),0), COALESCE(SUM(usd),0), "
            "COALESCE(SUM(credits),0) FROM ledger WHERE scope LIKE ?", (scope_prefix + "%",)).fetchone()
        con.close()
    return {"calls": row[0], "tokens": row[1], "usd": round(row[2], 4), "credits": row[3]}
