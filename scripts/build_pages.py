"""Build the GitHub Pages replay into docs/: the lab UI plus a snapshot of real runs (no keys, no server).

  python server.py                    # the live lab must be running: the snapshot is read through its API
  python scripts/build_pages.py       # writes docs/  →  GitHub: Settings → Pages → main /docs

Privacy: bots built on uploaded files (folders upload_*) are left out, and e-mail addresses and phone numbers are
scrubbed from everything that is published."""
import json
import re
import shutil
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs"
SNAP = OUT / "snap"
BASE = "http://127.0.0.1:8777"
MAX_EVENTS = 2500
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
PHONE = re.compile(r"\+\d[\d ]{8,15}\d|\b\d{3} \d{3} \d{3}\b")


def _clean(v):
    if isinstance(v, str):
        return PHONE.sub("[phone]", EMAIL.sub("[email]", v))
    if isinstance(v, list):
        return [_clean(x) for x in v]
    if isinstance(v, dict):
        return {k: _clean(x) for k, x in v.items()}
    return v


def scrub(text: str) -> str:
    """Scrub e-mail addresses and phone numbers inside JSON string values only (numbers stay intact)."""
    return json.dumps(_clean(json.loads(text)), ensure_ascii=False)


def private(text: str) -> bool:
    return "upload_" in text or "=== " in text  # an uploaded folder, or file text pasted into an input


def get(path: str):
    r = httpx.get(BASE + path, timeout=60)
    r.raise_for_status()
    return r.json()


def put(name: str, obj) -> None:
    f = SNAP / "api" / (name.replace("/", "__") + ".json")
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(scrub(json.dumps(obj, ensure_ascii=False)), encoding="utf-8")


def main():
    if OUT.exists():
        shutil.rmtree(OUT)
    SNAP.mkdir(parents=True)
    # bots built on someone's uploaded files stay private
    events = []
    with (ROOT / "data" / "events.jsonl").open(encoding="utf-8") as f:
        for line in f:
            try:
                events.append(json.loads(line))
            except ValueError:
                pass
    hidden = {e.get("workflow") or e.get("bot") for e in events if private(json.dumps(e, ensure_ascii=False))} - {None}
    bots = [b for b in get("/api/bots") if b["name"] not in hidden]
    registry = get("/api/registry")
    wf_steps = {a["name"]: [s["uses"] for s in a["manifest"].get("steps", [])] for a in registry if a["kind"] == "workflow"}
    hidden_caps = {s for w in hidden for s in wf_steps.get(w, []) if not any(s in st for n, st in wf_steps.items() if n not in hidden)}
    gone = hidden | hidden_caps
    registry = [a for a in registry if a["name"] not in gone]
    put("bots", bots)
    put("registry", registry)
    put("voices", get("/api/voices"))
    put("ledger", get("/api/ledger"))
    put("session", get("/api/session"))
    put("gates", [])
    put("status", {**get("/api/status"), "busy": False, "replay": True})
    for name in sorted({a["name"] for a in registry}):
        put(f"artifact/{name}", get(f"/api/artifact/{name}"))
        if any(a["name"] == name and a["kind"] == "capability" for a in registry):
            put(f"capabilities/{name}/dependents", get(f"/api/capabilities/{name}/dependents"))
    for b in bots:
        try:
            put(f"bots/{b['name']}/value", get(f"/api/bots/{b['name']}/value"))
        except httpx.HTTPError:
            pass

    keep = [e for e in events if not private(json.dumps(e, ensure_ascii=False))
            and (e.get("workflow") or e.get("bot")) not in gone and e.get("kind") != "gate"]
    keep = keep[-MAX_EVENTS:]
    keep.append({"ts": keep[-1]["ts"] + 0.001 if keep else 0, "kind": "idle", "msg": "recording ends here"})
    (SNAP / "events.json").write_text(scrub(json.dumps(keep, ensure_ascii=False)), encoding="utf-8")

    answers: dict = {}
    for e in events:
        if e.get("kind") == "result" and e.get("workflow") in {b["name"] for b in bots} and not private(json.dumps(e)):
            answers.setdefault(e["workflow"], []).append({k: e.get(k) for k in ("question", "input", "result", "ask_tokens", "run_tokens")})
    answers = {k: v[-12:] for k, v in answers.items()}
    (SNAP / "answers.json").write_text(scrub(json.dumps(answers, ensure_ascii=False)), encoding="utf-8")

    html = (ROOT / "ui" / "index.html").read_text(encoding="utf-8")
    assert "<head>" in html
    html = html.replace("<head>", '<head>\n<script src="replay.js"></script>', 1)
    (OUT / "index.html").write_text(html, encoding="utf-8")
    shutil.copy(ROOT / "pages" / "replay.js", OUT / "replay.js")
    (OUT / ".nojekyll").write_text("")
    size = sum(p.stat().st_size for p in OUT.rglob("*") if p.is_file())
    print(f"docs/: {len(bots)} bots, {len(registry)} registry entries, {len(keep)} events, "
          f"{sum(len(v) for v in answers.values())} recorded answers, {size / 1e6:.1f} MB; left out: {sorted(gone)}")


if __name__ == "__main__":
    main()
