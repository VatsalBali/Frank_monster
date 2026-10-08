"""Full automated rehearsal from an EMPTY registry, then a model audit of every LLM call it made.

Runs each scenario as a separate process (= fresh session), with the gate in auto mode (logged as such):
  1 ARES lookup  2 supplier check (reuse)  3-5 invoice categorisation x3  6 distill  7 self-repair via MCP
Writes data/rehearsal.md. Usage:  .venv/Scripts/python scripts/rehearse.py
"""
import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
PY = str(ROOT / ".venv" / "Scripts" / "python.exe")
ENV = {**os.environ, "GATE_MODE": "auto", "PYTHONIOENCODING": "utf-8"}

STEPS = [
    ("1 · ARES lookup (empty registry)", [PY, "cli.py", "solve", "--auto",
     "Look up the Czech company with ICO 27074358 in the official ARES registry and tell me its name, legal form, registered address and whether it is still active."]),
    ("2 · supplier check (fresh session, reuse)", [PY, "cli.py", "solve", "--auto",
     "Before we pay them, check our three suppliers with ICO 27074358, 00006947 and 45274649: give me each one's name, registered address and whether it is still active, and flag any supplier that is not active."]),
    ("3 · categorise invoices #1", [PY, "cli.py", "solve", "--auto",
     "Categorise these supplier invoice lines into accounting categories (hardware, software, office supplies, travel, professional services): 'Dell Latitude 5440 laptop x2', 'Microsoft 365 Business annual licence', 'Taxi Praha airport transfer', 'A4 paper 5 reams', 'Legal consultation March'."]),
    ("4 · categorise invoices #2", [PY, "cli.py", "solve", "--auto",
     "Categorise these invoice lines for bookkeeping into hardware, software, office supplies, travel or professional services: 'Logitech MX Master mouse', 'Adobe Creative Cloud monthly', 'Train ticket Brno-Praha', 'Accounting audit Q2', 'Printer toner HP 207A'."]),
    ("5 · categorise invoices #3", [PY, "cli.py", "solve", "--auto",
     "Please categorise for accounting (hardware, software, office supplies, travel, professional services): 'Samsung 27in monitor', 'JetBrains IntelliJ licence', 'Hotel Ibis Ostrava 2 nights', 'Staples and paper clips', 'Tax advisory services'."]),
    ("6 · distill LLM steps", [PY, "cli.py", "optimize", "--auto"]),
]


def main() -> None:
    from factory import config, llm_elevenlabs
    start = time.time()
    subprocess.run([PY, "scripts/reset_registry.py"], cwd=ROOT, env=ENV, check=True)
    report = ["# Rehearsal", f"started {time.strftime('%Y-%m-%d %H:%M:%S')}", ""]
    for title, cmd in STEPS:
        t0 = time.time()
        r = subprocess.run(cmd, cwd=ROOT, env=ENV, capture_output=True, text=True, encoding="utf-8", errors="replace")
        ok = r.returncode == 0
        tail = [l for l in (r.stdout + r.stderr).splitlines() if l.startswith(("[result]", "[error]", "[say]"))][-3:]
        report += [f"## {title}: {'OK' if ok else 'FAILED'} ({time.time() - t0:.0f}s)", *[f"    {l}" for l in tail], ""]
        print(report[-2 - len(tail)], flush=True)
    # 7 · self-repair via MCP: messy ids the original code never handled
    wf = next((a["name"] for a in __import__("factory.registry", fromlist=["x"]).list_artifacts("workflow")
               if "icos" in json.dumps(a["manifest"]["signature"].get("in", ""))), None)
    if wf:
        t0 = time.time()
        r = subprocess.run([PY, "scripts/mcp_smoke.py", "check suppliers", wf,
                            json.dumps({"icos": ["CZ27074358", "6947", "45274649"]})],
                           cwd=ROOT, env=ENV, capture_output=True, text=True, encoding="utf-8", errors="replace")
        healed = "installed" in r.stderr and "heal" in r.stderr
        ok = "run: {" in r.stdout
        report += [f"## 7 · self-repair via MCP on {wf}: {'OK' if ok else 'FAILED'} "
                   f"({'healed' if healed else 'no repair needed'}, {time.time() - t0:.0f}s)", ""]
        print(report[-2], flush=True)
    # model audit
    time.sleep(40)  # provider usage data lands a few seconds after each call
    con = sqlite3.connect(config.DB_PATH)
    rows = con.execute("SELECT purpose, model, conversation_id FROM ledger WHERE ts >= ? AND conversation_id != ''",
                       (start,)).fetchall()
    served, bad = {}, []
    for purpose, _, cid in rows:
        u = llm_elevenlabs.usage(cid) or {"models": ["?"]}
        want = config.RUNTIME_MODEL if purpose.startswith("llm-step") else config.BUILD_MODEL
        for m in u["models"]:
            served[m] = served.get(m, 0) + 1
        if u["models"] and want not in u["models"]:
            bad.append((purpose, u["models"]))
    report += ["## Model audit", f"calls: {len(rows)} · served: {served}",
               "**All calls served by the requested Claude model.**" if not bad else f"**SUBSTITUTIONS: {bad}**", ""]
    out = ROOT / "data" / "rehearsal.md"
    out.write_text("\n".join(report), encoding="utf-8")
    print("\n".join(report[-4:]))
    print(f"report: {out}")


if __name__ == "__main__":
    main()
