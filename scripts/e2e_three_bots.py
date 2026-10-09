"""End-to-end check against a running lab (python server.py): a fresh session, three bot-building prompts on the
sample company's files, then each new bot is asked a question through the same API the UI uses.

  python scripts/e2e_three_bots.py            → prints PASS/FAIL per bot and writes data/e2e_report.json
Network-access requests are NOT approved by this script: the prompts are chosen to need no new hosts."""
import json
import sys
import time

import httpx

BASE = "http://127.0.0.1:8777"
PROMPTS = [
    ("a bot that lists which customers are late paying us, how many days late and how much, with a short polite "
     "reminder for each", "Who is late paying us?"),
    ("a bot that totals our monthly sales by product category and tells me the best and the worst month",
     "Which month was the best for sales?"),
    ("a bot that reads our customer feedback and lists the top complaints and what we should fix first",
     "What do customers complain about most?"),
]
FILES = "\n\nFiles: folder novak_trading"


def wait_idle(timeout=900):
    t0 = time.time()
    time.sleep(2)
    while time.time() - t0 < timeout:
        if not httpx.get(f"{BASE}/api/status", timeout=10).json()["busy"]:
            return time.time() - t0
        time.sleep(3)
    raise TimeoutError("lab still busy")


def events_since(ts):
    out = []
    with open("data/events.jsonl", encoding="utf-8") as f:
        for line in f:
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if e.get("ts", 0) >= ts:
                out.append(e)
    return out


def main():
    report = []
    wait_idle()  # a build may still be running
    if "--no-session" not in sys.argv:
        httpx.post(f"{BASE}/api/session/new", timeout=30)
        wait_idle()
    only = [int(a.split("=")[1]) for a in sys.argv if a.startswith("--only=")]
    for n, (purpose, question) in enumerate(PROMPTS, 1):
        if only and n not in only:
            continue
        row = {"purpose": purpose}
        before = {b["name"] for b in httpx.get(f"{BASE}/api/bots").json()}
        t0 = time.time()
        r = httpx.post(f"{BASE}/api/bots/create", json={"purpose": purpose + FILES}, timeout=30)
        row["build_seconds"] = round(wait_idle())
        ev = events_since(t0)
        errs = [e["msg"] for e in ev if e.get("kind") == "error"]
        gates = [e["msg"] for e in ev if e.get("kind") == "gate"]
        built = [e for e in ev if e.get("kind") == "result" and e.get("build_usd") is not None]
        after = [b for b in httpx.get(f"{BASE}/api/bots").json() if b["name"] not in before]
        anyres = [e for e in ev if e.get("kind") == "result" and e.get("workflow")]
        name = built[-1]["workflow"] if built else (after[0]["name"] if after else (anyres[-1]["workflow"] if anyres else None))
        row.update(bot=name, build_errors=errs, gates=gates, build_usd=built[-1]["build_usd"] if built else None)
        if not name:
            row["status"] = "FAIL: no bot built"
            report.append(row)
            print(json.dumps(row, ensure_ascii=False))
            continue
        t1 = time.time()
        httpx.post(f"{BASE}/api/bots/{name}/ask", json={"question": question + FILES}, timeout=30)
        row["ask_seconds"] = round(wait_idle(), 1)
        ev = events_since(t1)
        res = [e for e in ev if e.get("kind") == "result" and e.get("workflow") == name]
        aerr = [e["msg"] for e in ev if e.get("kind") == "error"]
        row["ask_errors"] = aerr
        if res:
            row["ask_tokens"] = res[-1].get("ask_tokens")
            row["run_tokens"] = res[-1].get("run_tokens")
            row["answer"] = json.dumps(res[-1]["result"], ensure_ascii=False)[:600]
        row["status"] = "PASS" if res and not aerr else "FAIL"
        report.append(row)
        print(json.dumps(row, ensure_ascii=False)[:1500], flush=True)
    with open("data/e2e_report.json", "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)
    sys.exit(0 if all(r["status"] == "PASS" for r in report) else 1)


if __name__ == "__main__":
    main()
