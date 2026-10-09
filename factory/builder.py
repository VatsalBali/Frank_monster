"""Builds one missing capability from a gap spec: explore (LEARN, traced) → write code + tests → run tests in the
sandbox → repair until green or the cap is hit. Nothing here installs; it returns a tested candidate."""
import json
import re
import time
from typing import Literal, Optional

from pydantic import BaseModel

from . import config, gateway, registry, sandbox
from .events import emit
from .models import GapSpec

SYSTEM = """You are the builder inside a self-extending workflow factory. You write ONE reusable capability.

Contract:
- impl.py defines `run(inp: dict) -> dict`. Pure function of its input plus the network hosts you were granted.
  Available: Python 3.12 stdlib, requests, httpx, beautifulsoup4, lxml, pydantic, openpyxl, python-dateutil.
- test_impl.py: pytest tests importing `from impl import run`. Cover the example input, an edge case and bad input.
  Tests may call the granted hosts. Keep them fast (<30s total). Don't assert on values you haven't observed.
- Generic and reusable, not tailored to one task. Validate input; raise ValueError with a clear message on bad input.
- Prefer official structured endpoints (JSON/REST/CSV) over scraping HTML. Return clean, well-named fields.
- Explore before writing: use a `probe` action to inspect real APIs/pages (formats, field names, errors). Don't guess.
- Only the granted hosts are reachable; the sandbox blocks everything else. Use https URLs.

Each turn reply with exactly one action as JSON:
  {"action": "probe", "code": "<python snippet; print what you need to see>"}
  {"action": "submit", "impl_py": "<file>", "test_py": "<file>", "summary": "<one line: what it does>"}
  {"action": "request_access", "hosts": ["<hostname>"], "reason": "<why>"}   (operator must approve; use sparingly)"""


class Action(BaseModel):
    action: Literal["probe", "submit", "request_access"]
    code: Optional[str] = None
    hosts: Optional[list[str]] = None
    reason: Optional[str] = None
    impl_py: Optional[str] = None
    test_py: Optional[str] = None
    summary: Optional[str] = None


def run_tests(files: dict[str, str], net: list[str], label: str) -> dict:
    r = sandbox.run(files, ["python", "-m", "pytest", "-q", "-p", "no:cacheprovider", "--tb=short"],
                    net=net, timeout=90, label=label)
    out = (r.stdout + "\n" + r.stderr).strip()
    summary = next((l for l in reversed(out.splitlines()) if "passed" in l or "failed" in l or "error" in l), out[-200:])
    for line in out.splitlines()[-25:]:
        emit("test", msg=line, capability=label)
    return {"passed": r.ok, "summary": summary.strip(), "output": out[-4000:], "ms": r.ms, "run_id": r.run_id}


def _transcript(history: list[dict]) -> str:
    """Older probe outputs are shortened to keep each call small."""
    out = []
    for i, h in enumerate(history):
        recent = i >= len(history) - 3
        if h["action"] == "probe":
            res = h["result"] if recent else h["result"][:600] + ("…" if len(h["result"]) > 600 else "")
            out.append(f"### probe\n```python\n{h['code']}\n```\nOUTPUT:\n{res}")
        else:
            body = (f"impl.py:\n```python\n{h['impl_py']}\n```\ntest_impl.py:\n```python\n{h['test_py']}\n```\n"
                    if recent else "(earlier submission omitted)\n")
            out.append(f"### submit #{h['n']}\n{body}TEST RESULT:\n{h['result']}")
    return "\n\n".join(out)


def build_capability(gap: GapSpec, budget: gateway.Budget, extra_tests: dict[str, str] | None = None) -> dict | None:
    """Returns {'name','version','test_report'} for a tested candidate, or None if it couldn't be built."""
    if gap.kind == "llm":
        return build_llm_capability(gap, budget)
    emit("stage", stage="learn", capability=gap.name,
         msg=f"building {gap.name} (network: {', '.join(gap.net_hosts) or 'none'}): {gap.why_missing}")
    emit("say", text=f"Rebuilding {gap.name.replace('_', ' ')}." if "FAILED in production" in gap.why_missing
         else f"I'm missing a capability: {gap.name.replace('_', ' ')}. Let me build it.")
    net = list(gap.net_hosts)
    spec = (f"Capability to build: {gap.name}\nDescription: {gap.description}\nWhy it is missing: {gap.why_missing}\n"
            f"Input schema: {gap.input_schema_json}\nOutput schema: {gap.output_schema_json}\n"
            f"Example input: {gap.example_input_json}\nGranted network hosts: {net or 'none'}")
    history: list[dict] = []
    trace = {"gap": gap.model_dump(), "events": []}
    submits = 0
    done = None
    for it in range(config.MAX_FACTORY_ITERATIONS):
        prompt = spec + ("\n\nSo far:\n" + _transcript(history) if history else "") + "\n\nNext action?"
        act: Action = gateway.complete_json(budget, f"build:{gap.name}", system=SYSTEM, prompt=prompt, schema=Action)
        if act.action == "probe" and act.code:
            lines = [l for l in act.code.strip().splitlines() if l.strip() and not l.startswith(("import ", "from "))]
            first = (lines[0] if lines else "")[:100]
            emit("log", msg=f"probe ({gap.name}): {first}")
            r = sandbox.run({"probe.py": act.code}, ["python", "probe.py"], net=net, timeout=45, label="probe")
            res = (r.stdout[-3000:] + ("\nSTDERR:\n" + r.stderr[-1500:] if r.stderr.strip() else "")) or "(no output)"
            history.append({"action": "probe", "code": act.code, "result": res})
            trace["events"].append({"probe": act.code, "out": res, "ms": r.ms})
        elif act.action == "request_access" and act.hosts:
            from . import gate
            new = sorted(set(h.lower().strip() for h in act.hosts) - set(net))
            emit("say", text=f"I need access to {', '.join(new)}. Requesting permission.")
            ok = bool(new) and gate.ask("authority", f"{gap.name} requests network access: {', '.join(new)}",
                                        {"reason": act.reason or "", "currently granted": net or "none"})
            if ok:
                net.extend(new)
            history.append({"action": "probe", "code": f"# request_access {new}",
                            "result": f"{'GRANTED' if ok else 'DENIED'} by operator. Granted hosts now: {net or 'none'}"})
        elif act.action == "submit" and act.impl_py and act.test_py:
            submits += 1
            emit("stage", stage="test", capability=gap.name, msg=f"testing {gap.name} (attempt {submits})")
            files = {"impl.py": act.impl_py, "test_impl.py": act.test_py, **(extra_tests or {})}
            emit("code", capability=gap.name, attempt=submits, impl=act.impl_py[:12000], tests=act.test_py[:8000],
                 msg=f"{gap.name}: wrote impl.py ({len(act.impl_py.splitlines())} lines) + tests (attempt {submits})")
            rep = run_tests(files, net, gap.name)
            n = re.search(r"(\d+) passed", rep["summary"])
            if rep["passed"] and (not n or int(n.group(1)) < config.MIN_TESTS):
                rep["passed"] = False
                rep["output"] += f"\nREJECTED by harness: at least {config.MIN_TESTS} tests are required (example, edge case, bad input)."
                emit("test", msg=f"rejected: fewer than {config.MIN_TESTS} tests", capability=gap.name)
            trace["events"].append({"submit": submits, "passed": rep["passed"], "summary": rep["summary"]})
            if rep["passed"]:
                done = (files, act.summary or gap.description, rep)
                emit("say", text="All tests pass.")
                break
            history.append({"action": "submit", "n": submits, "impl_py": act.impl_py, "test_py": act.test_py,
                            "result": rep["output"]})
            if submits >= config.MAX_REPAIR_ATTEMPTS:
                break
            emit("say", text="Tests failed. Fixing it.")
        else:
            history.append({"action": "probe", "code": "# invalid action", "result": "Invalid action: send probe with code, or submit with impl_py and test_py."})
    trace_path = config.TRACES_DIR / f"{gap.name}_{int(time.time())}.json"
    trace_path.write_text(json.dumps(trace, indent=2, default=str), encoding="utf-8")
    if not done:
        emit("say", text=f"I couldn't build {gap.name} within my limits.")
        emit("log", msg=f"FAILED to build {gap.name} after {submits} attempts")
        return None
    files, summary, rep = done
    manifest = {
        "name": gap.name, "kind": "capability", "impl": "code", "description": summary,
        "signature": {"in": _short(gap.input_schema_json), "out": _short(gap.output_schema_json),
                      "in_schema": gap.input_schema_json, "out_schema": gap.output_schema_json},
        "permissions": {"net": net, "fs": "none", "llm": False},
        "budget": {"max_tokens_per_run": 0},
        "lineage": {"created_by": "factory", "trace": trace_path.name, "attempts": submits},
        "example_input": gap.example_input_json,
    }
    version = registry.save_candidate(manifest, files)
    return {"name": gap.name, "version": version, "test_report": rep}


def build_llm_capability(gap: GapSpec, budget: gateway.Budget) -> dict | None:
    """Judgment steps: a prompt + output schema run on the small runtime model. Tested on the example for
    schema-valid output; a failed test goes back to the build model with the error, like code repairs do.
    These are the steps the compiler later tries to distill into code."""
    emit("stage", stage="learn", capability=gap.name, msg=f"building LLM step {gap.name}")
    system = ("Write a concise system prompt for a small model that performs this step. "
              "It receives the input as JSON and must reply with one JSON object matching the output schema. "
              "The reply must stay compact (well under 2,500 characters): short strings, short lists, no long quotes. "
              "Output only the prompt text.")
    prompt = gateway.complete(budget, f"build-llm:{gap.name}", system=system, prompt=gap.model_dump_json()).strip()
    manifest = {
        "name": gap.name, "kind": "capability", "impl": "llm", "description": gap.description,
        "signature": {"in": _short(gap.input_schema_json), "out": _short(gap.output_schema_json),
                      "in_schema": gap.input_schema_json, "out_schema": gap.output_schema_json},
        "permissions": {"net": [], "fs": "none", "llm": True},
        "budget": {"max_tokens_per_run": 6000},
        "lineage": {"created_by": "factory"},
        "example_input": gap.example_input_json,
    }
    from .executor import run_capability
    for attempt in range(1, config.MAX_REPAIR_ATTEMPTS + 1):
        version = registry.save_candidate(manifest, {"prompt.txt": prompt})
        try:
            cap = registry.get(gap.name, version)
            out, _ = run_capability(cap, json.loads(gap.example_input_json), budget)
            required = json.loads(gap.output_schema_json).get("required", [])
            missing = [k for k in required if k not in out]
            rep = {"passed": not missing, "summary": f"example → schema-valid output (missing fields: {missing or 'none'})",
                   "output": json.dumps(out)[:2000]}
        except Exception as e:
            rep = {"passed": False, "summary": f"example run failed: {str(e)[:300]}", "output": ""}
        emit("test", msg=f"{rep['summary']} (attempt {attempt})", capability=gap.name)
        if rep["passed"]:
            return {"name": gap.name, "version": version, "test_report": rep}
        registry.set_status(gap.name, version, "archived")
        if attempt == config.MAX_REPAIR_ATTEMPTS:
            break
        emit("say", text="That step's answer didn't hold up. Rewriting its instructions.")
        prompt = gateway.complete(
            budget, f"build-llm:{gap.name}", system=system,
            prompt=gap.model_dump_json() + "\n\nThe previous prompt failed its test on the example input:\n"
                   + rep["summary"] + "\n\nPrevious prompt:\n" + prompt + "\n\nWrite a fixed prompt.").strip()
    return None


def _short(schema_json: str) -> str:
    try:
        s = json.loads(schema_json)
        return "{" + ", ".join(s.get("properties", {}).keys()) + "}"
    except Exception:
        return schema_json[:80]
