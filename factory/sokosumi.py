"""Sokosumi agent marketplace: paid AI agents the factory can hire as capabilities.

Hiring costs credits and takes minutes, so it is authority, not just ability:
- installing a Sokosumi capability needs the operator's approval (it can spend money);
- every job above SOKOSUMI_CONFIRM_ABOVE credits asks the operator before it is created;
- a daily credit cap (SOKOSUMI_DAILY_CAP) is enforced in code.
Spec: https://api.sokosumi.com/v1/openapi.json (GET /agents, GET /agents/{id}/input-schema, POST /agents/{id}/jobs,
GET /jobs/{id})."""
import json
import os
import time

import httpx

from . import config
from .events import emit

API = "https://api.sokosumi.com/v1"
CONFIRM_ABOVE = float(os.getenv("SOKOSUMI_CONFIRM_ABOVE", "0"))   # 0: every paid job is confirmed
DAILY_CAP = float(os.getenv("SOKOSUMI_DAILY_CAP", "500"))
SPEND = config.DATA / "sokosumi_spend.json"
TERMINAL = {"completed", "failed", "input_required", "payment_failed", "refund_resolved", "dispute_resolved"}
_cache: dict = {}


def available() -> bool:
    return bool(os.getenv("SOKOSUMI_API_KEY"))


def _h() -> dict:
    return {"Authorization": f"Bearer {os.environ['SOKOSUMI_API_KEY']}"}


def agents(limit: int = 100) -> list[dict]:
    """Marketplace agents with their price in credits (cached for 10 minutes)."""
    if not available():
        return []
    if _cache.get("t", 0) > time.time() - 600:
        return _cache["agents"]
    out, cursor = [], None
    for _ in range(5):
        r = httpx.get(f"{API}/agents", headers=_h(), timeout=30,
                      params={"limit": limit, **({"cursor": cursor} if cursor else {})})
        r.raise_for_status()
        j = r.json()
        out += [{"id": a["id"], "name": a.get("name", ""), "credits": a.get("credits"),
                 "summary": (a.get("summary") or a.get("description") or "")[:200]} for a in j.get("data", [])]
        pg = (j.get("meta") or {}).get("pagination") or {}
        cursor = pg.get("nextCursor") or pg.get("cursor")
        if not cursor or not j.get("data"):
            break
    _cache.update(t=time.time(), agents=out)
    return out


def catalog_text(max_items: int = 40) -> str:
    try:
        ags = agents()
    except Exception as e:
        return f"(Sokosumi marketplace unavailable: {str(e)[:120]})"
    return "\n".join(f"- {a['id']} · {a['name']} · {a['credits']} credits · {a['summary']}" for a in ags[:max_items])


def input_schema(agent_id: str) -> dict:
    r = httpx.get(f"{API}/agents/{agent_id}/input-schema", headers=_h(), timeout=30)
    r.raise_for_status()
    j = r.json()
    return j.get("data", j)


def to_json_schema(soko: dict) -> dict:
    """Sokosumi input fields → a JSON Schema the planner and the input checker understand."""
    props, req = {}, []
    for f in soko.get("input_data") or soko.get("inputData") or []:
        t = f.get("type", "string")
        jt = {"number": "number", "boolean": "boolean", "option": "array"}.get(t, "string")
        props[f["id"]] = {"type": jt, "description": ((f.get("data") or {}).get("description") or f.get("name") or "")[:160]}
        optional = any(v.get("validation") == "optional" and str(v.get("value")) == "true" for v in f.get("validations") or [])
        if not optional and t != "none":
            req.append(f["id"])
    return {"type": "object", "properties": props, "required": req}


def _spent_today() -> float:
    try:
        d = json.loads(SPEND.read_text())
    except (OSError, ValueError):
        d = {}
    return float(d.get(time.strftime("%Y-%m-%d"), 0))


def _add_spend(credits: float) -> None:
    try:
        d = json.loads(SPEND.read_text())
    except (OSError, ValueError):
        d = {}
    day = time.strftime("%Y-%m-%d")
    d[day] = float(d.get(day, 0)) + credits
    SPEND.write_text(json.dumps(d))


def hire(cap_name: str, agent_id: str, schema: dict, data: dict, credits: float, timeout_s: int = 900) -> dict:
    """Create a job, wait for it, return its result. Asks the operator first; respects the daily cap."""
    from . import gate
    if _spent_today() + credits > DAILY_CAP:
        raise RuntimeError(f"Sokosumi daily cap reached ({_spent_today():.0f}/{DAILY_CAP:.0f} credits today)")
    if credits > CONFIRM_ABOVE and not gate.ask("spend", f"hire Sokosumi agent for {cap_name}: up to {credits:g} credits",
                                                {"agent": agent_id, "input": json.dumps(data, ensure_ascii=False)[:400],
                                                 "credits today": f"{_spent_today():.0f} of {DAILY_CAP:.0f}"}):
        raise RuntimeError("operator declined the paid Sokosumi job")
    clean = {k: v for k, v in data.items() if v not in (None, "")}
    r = httpx.post(f"{API}/agents/{agent_id}/jobs", headers=_h(), timeout=60,
                   json={"inputSchema": schema, "inputData": clean, "maxCredits": credits, "name": f"Frankenstein Lab · {cap_name}"})
    if r.status_code >= 400:
        raise RuntimeError(f"Sokosumi refused the job: {r.status_code} {r.text[:300]}")
    job = r.json().get("data", r.json())
    jid = job["id"]
    _add_spend(credits)
    emit("log", msg=f"hired Sokosumi agent for {cap_name}: job {jid} (up to {credits:g} credits); waiting for the result")
    t0, last = time.time(), ""
    while time.time() - t0 < timeout_s:
        time.sleep(6)
        j = httpx.get(f"{API}/jobs/{jid}", headers=_h(), timeout=30).json().get("data", {})
        st = j.get("status", "")
        if st != last:
            emit("log", msg=f"Sokosumi job {jid}: {st} ({int(time.time() - t0)} s)")
            last = st
        if st in TERMINAL:
            if st != "completed":
                raise RuntimeError(f"Sokosumi job {jid} ended as {st}")
            return {"result": j.get("result") or "", "job_id": jid, "credits": j.get("credits", credits)}
    raise TimeoutError(f"Sokosumi job {jid} still running after {timeout_s} s")
