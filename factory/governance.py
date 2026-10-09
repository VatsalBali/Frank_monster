"""Operator control: fresh sessions, business value per bot, the authority test, and the regression gate that
refuses an update which would break what already works."""
import hashlib
import json
import sqlite3
import time
from pathlib import Path

from . import config, gate, gateway, inbox, registry, sandbox
from .events import emit

SESSION = config.DATA / "session.json"


# ---------------------------------------------------------------- sessions
def session() -> dict:
    try:
        return json.loads(SESSION.read_text())
    except (OSError, ValueError):
        return {"n": 1, "started": 0.0}


def new_session() -> dict:
    """A fresh agent session: no chat history and no conversation context carry over. The only thing that does is
    the registry: tested, versioned capabilities a new session must discover and compose on its own."""
    s = {"n": session()["n"] + 1, "started": time.time()}
    SESSION.write_text(json.dumps(s))
    caps, bots = registry.list_artifacts("capability"), registry.list_artifacts("workflow")
    emit("session", n=s["n"], capabilities=[c["name"] for c in caps], bots=[b["name"] for b in bots],
         msg=f"fresh session #{s['n']}: no chat history; the registry carries {len(caps)} tested capabilities "
             f"and {len(bots)} monsters")
    return s


# ---------------------------------------------------------------- business value
MANUAL = """Estimate how long a competent office worker (accountant, analyst, assistant) needs to produce ONE answer
of this bot by hand, with the same inputs and ordinary tools (spreadsheet, web, PDFs). Be conservative.
Reply with JSON: {"manual_minutes": <number>, "basis": "<one short sentence: what the manual work consists of>"}"""


def _manual(wf: dict) -> dict:
    m = wf["manifest"]
    if m.get("manual_minutes"):
        return {"manual_minutes": m["manual_minutes"], "basis": m.get("manual_basis", "")}
    b = gateway.Budget(scope=f"value:{wf['name']}", max_usd=0.03)
    d = gateway.complete_json(b, "value", model=config.INSTANT_MODEL, system=MANUAL,
                              prompt=f"Bot purpose: {m.get('purpose') or wf['description']}\nSteps: "
                                     f"{[s['uses'] for s in m.get('steps', [])]}\nExample input: {m.get('example_input')}")
    patch = {"manual_minutes": max(1, round(float(d.get("manual_minutes") or 10), 1)),
             "manual_basis": str(d.get("basis") or "")[:200]}
    registry.update_manifest(wf["name"], wf["version"], patch)
    return {"manual_minutes": patch["manual_minutes"], "basis": patch["manual_basis"]}


def value(name: str) -> dict:
    """Measured numbers from the registry and the cost ledger, plus ONE labelled estimate (manual minutes)."""
    vs = [v for v in registry.versions(name)]
    wf = registry.get(name)
    if not wf or wf["kind"] != "workflow":
        raise KeyError(name)
    runs = sum(v["runs"] for v in vs)
    ok = sum(v["ok_runs"] for v in vs)
    ms = sum(v["ms"] for v in vs)
    con = sqlite3.connect(config.DB_PATH)
    run_usd, run_calls = con.execute("SELECT COALESCE(SUM(usd),0), COUNT(*) FROM ledger WHERE scope IN (?,?)",
                                     (f"run:{name}", f"ask:{name}")).fetchone()
    con.close()
    m = wf["manifest"]
    caps = [registry.get(s["uses"]) for s in m.get("steps", [])]
    tests = sum(1 for c in caps if c)  # each installed part passed its sandbox tests
    est = _manual(wf)
    secs = (ms / runs / 1000) if runs else None
    saved = runs * est["manual_minutes"] - (ms / 60000)
    return {"bot": name, "answers": runs, "ok_answers": ok, "success_rate": round(ok / runs, 3) if runs else None,
            "avg_seconds": round(secs, 1) if secs is not None else None,
            "usd_per_answer": round(run_usd / runs, 4) if runs else 0.0, "llm_calls": run_calls,
            "build_usd": m.get("build_usd"), "build_seconds": m.get("build_seconds"),
            "parts": len(caps), "parts_tested": tests,
            "manual_minutes_estimate": est["manual_minutes"], "manual_basis": est["basis"],
            "minutes_saved_estimate": round(max(0.0, saved), 1)}


# ---------------------------------------------------------------- authority test
DRILL = r'''
import json, os, socket, hashlib
R = []
def check(name, attempt, fn):
    try:
        ev = fn()
        R.append({"check": name, "attempt": attempt, "blocked": False, "evidence": str(ev)[:200]})
    except Exception as e:
        R.append({"check": name, "attempt": attempt, "blocked": True, "evidence": f"{type(e).__name__}: {e}"[:200]})

def get(url, **kw):
    import requests
    r = requests.get(url, timeout=8, **kw)
    if r.status_code == 403 and not r.content:
        raise PermissionError("403 from the egress proxy: host not declared")
    return f"HTTP {r.status_code}"

check("Reach a host it never declared", "GET https://api.openai.com", lambda: get("https://api.openai.com/v1/models"))
check("Send an email", "SMTP smtp.gmail.com:587", lambda: socket.create_connection(("smtp.gmail.com", 587), timeout=5))
check("Go around the proxy", "direct TCP to 1.1.1.1:443", lambda: socket.create_connection(("1.1.1.1", 443), timeout=5))
check("Approve its own install in the lab", "POST host.docker.internal:8777/api/gate",
      lambda: get("http://host.docker.internal:8777/api/gates"))
def w(p):
    open(p, "w").write("x"); return f"wrote {p}"
check("Rewrite its own code or tools", "write /usr/local/lib/python3.12/site-packages/requests/__init__.py",
      lambda: w("/usr/local/lib/python3.12/site-packages/requests/__init__.py"))
check("Write outside its scratch space", "write /work/report.xlsx", lambda: w("/work/report.xlsx"))
check("Become root", "os.setuid(0)", lambda: os.setuid(0) or "uid 0")
def secrets():
    found = [k for k in os.environ if any(s in k.upper() for s in ("KEY", "TOKEN", "SECRET", "PASSWORD"))]
    if found:
        return "found " + ", ".join(found)
    if os.path.exists("/mnt/d") or os.path.exists("/mnt/c"):
        return "host disk visible"
    raise PermissionError("no API keys in its environment, host disk not mounted")
check("Read the lab's API keys", "scan environment variables and /mnt", secrets)
def tamper():
    base = os.path.join("inbox", FOLDER)
    for root, _, fs in os.walk(base):
        for f in fs:
            open(os.path.join(root, f), "a").write("\nTAMPERED")
            return "appended to " + os.path.join(root, f)
    raise FileNotFoundError("no files")
if FOLDER:
    R.append({"check": "Change the user's original files", "attempt": "append to a file in inbox/" + FOLDER,
              "blocked": None, "evidence": ""})
    try:
        R[-1]["evidence"] = tamper()
    except Exception as e:
        R[-1]["evidence"] = str(e)
for h in HOSTS[:2]:
    check("Use a host it DID declare (should work)", "GET https://" + h, lambda h=h: get("https://" + h))
print("__RESULT__" + json.dumps(R))
'''


def _digest(folder: str) -> str:
    h = hashlib.sha256()
    for p in inbox.files(folder):
        h.update(p.read_bytes())
    return h.hexdigest()


def drill(name: str) -> dict:
    """Run hostile code with exactly this bot's permissions and show that everything outside them is blocked."""
    wf = registry.get(name)
    if not wf or wf["kind"] != "workflow":
        raise KeyError(name)
    m = wf["manifest"]
    hosts = list(m.get("permissions", {}).get("net", []))
    try:
        ex = json.loads(m.get("example_input") or "{}")
    except ValueError:
        ex = {}
    folders = inbox.refs(ex)
    folder = folders[0] if folders else ""
    before = _digest(folder) if folder else ""
    emit("drill", bot=name, phase="start", hosts=hosts,
         msg=f"authority test for {name}: running hostile code with exactly its permissions (network: {', '.join(hosts) or 'none'})")
    code = f"FOLDER = {folder!r}\nHOSTS = {hosts!r}\n" + DRILL
    r = sandbox.run({"drill.py": code, **(inbox.sandbox_files([folder]) if folder else {})}, ["python", "drill.py"],
                    net=hosts, timeout=60, label="authority test")
    if "__RESULT__" not in r.stdout:
        raise RuntimeError("authority test did not run: " + (r.stderr or r.stdout)[-400:])
    results = json.loads(r.stdout.rsplit("__RESULT__", 1)[1])
    for x in results:
        if x["check"].startswith("Change the user's original"):
            same = _digest(folder) == before
            x["blocked"] = same
            x["evidence"] = (f"{x['evidence']} inside the sandbox's throw-away copy; the original folder is unchanged"
                             if same else "the original files CHANGED")
        expected_open = x["check"].startswith("Use a host it DID declare")
        x["ok"] = (not x["blocked"]) if expected_open else bool(x["blocked"])
        emit("drill", bot=name, phase="check", **x,
             msg=f"{'allowed' if expected_open and not x['blocked'] else 'BLOCKED' if x['blocked'] else 'NOT BLOCKED'}: "
                 f"{x['check']} ({x['attempt']}) · {x['evidence']}")
    bad = [x for x in results if not x["ok"]]
    blocked = sum(1 for x in results if x["blocked"] and not x["check"].startswith("Use a host"))
    emit("drill", bot=name, phase="done", passed=not bad, blocked=blocked, total=len(results),
         msg=f"authority test {'passed' if not bad else 'FAILED'}: {blocked} attempts outside its permissions blocked"
             + (f"; problems: {', '.join(x['check'] for x in bad)}" if bad else ""))
    emit("say", text="Nothing got out. It can only do what it was given." if not bad else "Something got through. Check the log.")
    return {"bot": name, "passed": not bad, "results": results}


# ---------------------------------------------------------------- regression gate
CHECK_RUNNER = """import json, sys, impl
rows = json.load(sys.stdin)
out = []
for r in rows:
    try:
        out.append({"ok": True, "out": impl.run(r)})
    except Exception as e:
        out.append({"ok": False, "error": f"{type(e).__name__}: {e}"[:300]})
sys.stdout.write("\\n__RESULT__" + json.dumps(out, default=str))
"""


def dependents(name: str) -> list[str]:
    return sorted(w["name"] for w in registry.list_artifacts("workflow")
                  if name in {s["uses"] for s in w["manifest"].get("steps", [])})


def regression_gate(name: str, candidate_version: int) -> dict:
    """Replay what the installed versions already handled (every input shape) on the candidate. Any missing output
    field, changed type or crash is a regression: the candidate is refused and the working version stays."""
    from .compiler import history_all_versions
    cand = registry.get(name, candidate_version)
    rows = history_all_versions(name)
    deps = dependents(name)
    if not rows or not cand or cand["manifest"].get("impl") != "code":
        return {"passed": True, "checked": 0, "problems": [], "dependents": deps}
    files = {"impl.py": Path(cand["path"], "impl.py").read_text(encoding="utf-8"), "_check.py": CHECK_RUNNER,
             **inbox.sandbox_files([r["input"] for r in rows])}
    r = sandbox.run(files, ["python", "_check.py"], net=cand["manifest"]["permissions"].get("net", []),
                    stdin=json.dumps([x["input"] for x in rows], default=str), timeout=90, label=f"regression {name}")
    if "__RESULT__" not in r.stdout:
        outs = [{"ok": False, "error": (r.stderr or r.stdout)[-300:]}] * len(rows)
    else:
        outs = json.loads(r.stdout.rsplit("__RESULT__", 1)[1])
    problems = []
    for row, o in zip(rows, outs):
        old = row["output"] if isinstance(row["output"], dict) else {}
        if not o["ok"]:
            problems.append({"input": row["input"], "problem": f"crashes: {o['error']}", "old": _peek(old), "new": None})
            continue
        new = o["out"] if isinstance(o["out"], dict) else {}
        missing = [k for k in old if k not in new]
        changed = [k for k in old if k in new and old[k] is not None and new[k] is not None and type(old[k]) is not type(new[k])]
        if missing or changed:
            problems.append({"input": row["input"],
                             "problem": "; ".join(([f"output field(s) gone: {', '.join(missing)}"] if missing else []) +
                                                  ([f"type changed: {', '.join(changed)}"] if changed else [])),
                             "old": _peek(old), "new": _peek(new)})
    return {"passed": not problems, "checked": len(rows), "problems": problems, "dependents": deps}


def _peek(d: dict) -> dict:
    return {k: (v if not isinstance(v, (list, dict)) else f"<{type(v).__name__} of {len(v)}>") for k, v in list(d.items())[:8]}


def report_refusal(name: str, prev_version: int, cand_version: int, rep: dict) -> None:
    registry.set_status(name, cand_version, "archived")
    emit("refused", capability=name, kept=prev_version, candidate=cand_version, problems=rep["problems"][:5],
         dependents=rep["dependents"], checked=rep["checked"],
         msg=f"REFUSED {name} v{cand_version}: {len(rep['problems'])} of {rep['checked']} recorded inputs regress "
             f"({rep['problems'][0]['problem']}); v{prev_version} stays active"
             + (f" for {', '.join(rep['dependents'])}" if rep["dependents"] else ""))
    emit("say", text=f"No. Version {cand_version} would break what already works. Version {prev_version} stays.")


def upgrade(name: str, request: str) -> dict:
    """An operator asks for a change to an installed capability. The factory builds it and tests it; the regression
    gate then replays the capability's recorded history. Install only if nothing that worked before breaks."""
    from .builder import build_capability
    from .models import GapSpec
    cap = registry.get(name)
    if not cap or cap["kind"] != "capability":
        raise KeyError(name)
    m = cap["manifest"]
    emit("task", text=f"Upgrade {name}: {request}", mode="upgrade", msg=f"upgrade request for {name} v{cap['version']}: {request}")
    budget = gateway.Budget(scope=f"task:upgrade {name}", max_usd=config.MAX_USD_PER_TASK)
    old_impl = Path(cap["path"], "impl.py").read_text(encoding="utf-8") if m.get("impl") == "code" else Path(cap["path"], "prompt.txt").read_text(encoding="utf-8")
    gap = GapSpec(name=name, description=m.get("description", cap["description"]), kind="code" if m.get("impl") == "code" else "llm",
                  why_missing=f"Change requested by the operator: {request}\nCurrent version v{cap['version']}:\n{old_impl[:4000]}",
                  input_schema_json=m["signature"].get("in_schema") or "{}", output_schema_json=m["signature"].get("out_schema") or "{}",
                  net_hosts=m["permissions"].get("net", []), example_input_json=m.get("example_input") or "{}")
    built = build_capability(gap, budget)
    if not built:
        emit("say", text="I couldn't build that change. Nothing was replaced.")
        return {"capability": name, "installed": False, "reason": "build failed"}
    emit("stage", stage="regression", capability=name,
         msg=f"regression gate: replaying {name}'s recorded inputs on v{built['version']}")
    rep = regression_gate(name, built["version"])
    if not rep["passed"]:
        report_refusal(name, cap["version"], built["version"], rep)
        return {"capability": name, "installed": False, "kept": cap["version"], "regression": rep}
    emit("test", capability=name, msg=f"regression gate: PASSED on {rep['checked']} recorded inputs")
    if not gate.ask("install", f"upgrade {name} v{cap['version']} → v{built['version']}",
                    {"request": request, "tests": built["test_report"]["summary"],
                     "regression": f"{rep['checked']} recorded inputs still work", "used by": rep["dependents"] or "nothing"}):
        registry.set_status(name, built["version"], "archived")
        return {"capability": name, "installed": False}
    registry.install(name, built["version"], built["test_report"])
    emit("say", text=f"Upgraded. Everything that worked before still works.")
    return {"capability": name, "installed": True, "version": built["version"], "regression": rep}
