"""Distillation: turn token-burning LLM steps into deterministic code, using the step's own recorded history.

Gap detection here is economic, not functional: "I keep paying tokens for this step." When an LLM capability has
enough recorded runs, the factory writes code that reproduces them. The harness (not the agent) generates the
equivalence tests from the recorded input/output pairs. The distilled code may answer {"__fallback__": true}
for inputs it is unsure about; at run time those fall back to the LLM version, so cost drops without losing coverage.
"""
import json
import re
from pathlib import Path
from typing import Optional

from pydantic import BaseModel

from . import config, gate, gateway, registry, sandbox
from .events import emit

MIN_SAMPLES = 3

SYSTEM = """You distill an LLM step into deterministic Python. You get the step's prompt and its recorded runs.
Write impl.py with `run(inp: dict) -> dict` that reproduces the recorded outputs exactly, using rules, lookups and
parsing — no network, no LLM. Generalise sensibly beyond the samples (normalise case, whitespace, synonyms).
If an input is outside what you can handle confidently, return {"__fallback__": true}; the runtime then uses the LLM.
Reply with JSON: {"impl_py": "<file>", "notes": "<one line: how it works>"}"""


class Distilled(BaseModel):
    impl_py: str
    notes: Optional[str] = ""


def samples(cap: dict) -> list[dict]:
    f = Path(cap["path"], "io.jsonl")
    if not f.exists():
        return []
    rows = [json.loads(l) for l in f.read_text(encoding="utf-8").splitlines() if l.strip()]
    seen, out = set(), []
    for r in rows:
        k = json.dumps(r["input"], sort_keys=True, ensure_ascii=False)
        if k not in seen:
            seen.add(k)
            out.append(r)
    return out


def candidates() -> list[dict]:
    return [c for c in registry.list_artifacts("capability")
            if c["manifest"].get("impl") == "llm" and len(samples(c)) >= MIN_SAMPLES]


def decision_keys(rows: list[dict]) -> list[str]:
    """Output fields that carry the decision: everything except long free text (rationales, explanations),
    which no deterministic code can or should reproduce word for word."""
    keys = []
    for k in rows[0]["output"]:
        vals = [r["output"].get(k) for r in rows]
        if all(isinstance(v, str) for v in vals) and max(len(v) for v in vals) > 40:
            continue
        keys.append(k)
    return keys


def replay_test(rows: list[dict], keys: list[str]) -> str:
    """Harness-generated equivalence test: every recorded run must be reproduced (on its decision fields)
    or explicitly deferred, and at least 70% must be reproduced."""
    return f'''import json
from impl import run
ROWS = json.loads({json.dumps(json.dumps(rows, ensure_ascii=False))})
KEYS = {keys!r}

def test_reproduces_recorded_runs():
    hits, wrong = 0, []
    for r in ROWS:
        out = run(r["input"])
        if out == {{"__fallback__": True}}:
            continue
        if all(out.get(k) == r["output"].get(k) for k in KEYS):
            hits += 1
        else:
            wrong.append((r["input"], out, r["output"]))
    assert not wrong, f"mismatches: {{wrong[:3]}}"
    assert hits >= 0.7 * len(ROWS), f"only {{hits}}/{{len(ROWS)}} reproduced"

def test_unknown_input_defers_or_answers():
    out = run({{k: "zzz-unseen-value" for k in ROWS[0]["input"]}})
    assert isinstance(out, dict)
'''


def distill(cap: dict, budget: gateway.Budget) -> bool:
    rows = samples(cap)
    name = cap["name"]
    tok = round(cap["tokens"] / cap["runs"]) if cap["runs"] else 0
    emit("stage", stage="distill", capability=name, msg=f"distilling {name}: {len(rows)} recorded runs, ~{tok} tok/run")
    emit("say", text=f"I keep paying tokens for {name.replace('_', ' ')}. Let me turn it into plain code.")
    prompt_txt = Path(cap["path"], "prompt.txt").read_text(encoding="utf-8")
    base = (f"Step: {name}\nPrompt the LLM was given:\n{prompt_txt}\n\nOutput schema: {cap['manifest']['signature']['out_schema']}"
            f"\n\nRecorded runs (input → output):\n" +
            "\n".join(json.dumps(r, ensure_ascii=False) for r in rows[:60]))
    keys = decision_keys(rows)
    base += (f"\n\nOnly these output fields must match the recordings exactly: {keys}. Other fields may be short "
             f"generic text.")
    feedback = ""
    for attempt in range(1, config.MAX_REPAIR_ATTEMPTS + 1):
        d: Distilled = gateway.complete_json(budget, f"distill:{name}", system=SYSTEM, schema=Distilled,
                                             prompt=base + feedback)
        files = {"impl.py": d.impl_py, "test_replay.py": replay_test(rows, keys)}
        emit("stage", stage="test", capability=name, msg=f"equivalence test vs {len(rows)} recorded runs (attempt {attempt})")
        r = sandbox.run(files, ["python", "-m", "pytest", "-q", "-p", "no:cacheprovider", "--tb=short"], net=[],
                        timeout=60, label=name)
        out = (r.stdout + "\n" + r.stderr).strip()
        for line in out.splitlines()[-12:]:
            emit("test", msg=line, capability=name)
        if r.ok:
            break
        feedback = f"\n\nYour previous attempt failed the equivalence test:\n{out[-2500:]}\nFix it."
    else:
        emit("say", text=f"I couldn't distill {name}. It stays an LLM step.")
        return False

    manifest = {**cap["manifest"], "impl": "code", "description": cap["description"] + " (distilled to code)",
                "permissions": {"net": [], "fs": "none", "llm": False}, "budget": {"max_tokens_per_run": 0},
                "fallback": {"name": name, "version": cap["version"]},
                "lineage": {"created_by": "distillation", "parent": f"{name}@v{cap['version']}",
                            "samples": len(rows), "notes": d.notes}}
    manifest.pop("version", None)
    version = registry.save_candidate(manifest, files)
    summary = re.findall(r"\d+ passed[^\n]*", out)
    rep = {"passed": True, "summary": (summary[-1] if summary else "passed") + f" · equivalent on {len(rows)} recorded runs",
           "output": out[-2000:]}
    if not gate.ask("install", f"replace LLM step {name} v{cap['version']} (~{tok} tok/run) with code v{version} (0 tok/run)",
                    {"recorded runs": len(rows), "tests": rep["summary"], "how": d.notes,
                     "unsure inputs": f"fall back to LLM v{cap['version']}"}):
        registry.set_status(name, version, "archived")
        return False
    registry.install(name, version, rep)
    emit("flask", name=name, version=version, msg=f"distilled {name} → code v{version}")
    emit("say", text=f"Done. {name.replace('_', ' ')} now costs zero tokens.")
    return True


def optimize_all() -> int:
    budget = gateway.Budget(scope="task:optimize", max_usd=config.MAX_USD_PER_TASK)
    n = 0
    found = candidates()
    for cap in found:
        if distill(cap, budget):
            n += 1
    if not found:
        emit("log", msg="optimize: nothing to distill yet (needs an LLM step with ≥3 recorded runs)")
    return n
