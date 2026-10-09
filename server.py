"""Lab console server: serves the UI, streams factory events (SSE), exposes operator controls.

  .venv/Scripts/python server.py   →  http://localhost:8777
"""
import asyncio
import os
import json
import threading
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response, StreamingResponse
from pydantic import BaseModel

from factory import config, gate, gateway, governance, inbox, monster_voice, registry, sandbox, voice
from factory.events import emit

app = FastAPI()
UI = Path(__file__).parent / "ui"
EVENTS = config.DATA / "events.jsonl"
_busy = threading.Lock()


@app.get("/")
def index():
    return FileResponse(UI / "index.html")


@app.get("/events")
async def events(replay: int = 1500):
    """Tail data/events.jsonl, so events from any process (CLI, MCP server, UI runs) show up."""
    async def gen():
        EVENTS.touch()
        with EVENTS.open("r", encoding="utf-8") as f:
            lines = f.readlines()
            buf = ""
            if lines and not lines[-1].endswith("\n"):  # a writer is mid-line: finish it in the tail loop
                buf = lines.pop()
            for line in lines[-replay:]:
                if line.strip():
                    yield f"data: {line.strip()}\n\n"
            while True:
                chunk = f.readline()
                if chunk:
                    buf += chunk
                    if buf.endswith("\n"):  # only complete lines; a writer may be mid-line
                        yield f"data: {buf.strip()}\n\n"
                        buf = ""
                else:
                    await asyncio.sleep(0.25)
    return StreamingResponse(gen(), media_type="text/event-stream")


@app.get("/api/registry")
def api_registry():
    return [{k: a[k] for k in ("name", "version", "kind", "status", "description", "runs", "ok_runs", "tokens", "ms", "created")}
            | {"manifest": a["manifest"]} for a in registry.list_artifacts(status=None)]


@app.get("/api/artifact/{name}")
def api_artifact(name: str):
    vs = registry.versions(name)
    if not vs:
        raise HTTPException(404)
    for v in vs:
        d = Path(v["path"])
        v["files"] = {f.name: f.read_text(encoding="utf-8")[:20000] for f in d.iterdir()
                      if f.is_file() and f.suffix in (".py", ".txt", ".json", ".jsonl")}
    return vs


class Decision(BaseModel):
    approved: bool


@app.get("/api/gates")
def api_gates():
    return gate.pending()


@app.post("/api/gate/{gid}")
def api_gate(gid: str, d: Decision):
    if not gate.decide(gid, d.approved):
        raise HTTPException(404, "no such pending gate")
    return {"ok": True}


@app.post("/api/rollback/{name}")
def api_rollback(name: str):
    try:
        return {"active": registry.rollback(name)}
    except (KeyError, ValueError) as e:
        raise HTTPException(400, str(e))


@app.post("/api/revoke/{name}")
def api_revoke(name: str):
    a = registry.get(name)
    if not a:
        raise HTTPException(404)
    registry.set_status(name, a["version"], "revoked")
    return {"revoked": a["version"]}


@app.get("/api/voices")
def api_voices():
    return [{"id": k, "label": v["label"], "fits": v["fits"]} for k, v in monster_voice.ARCHETYPES.items()]


class VoiceReq(BaseModel):
    voice: str


@app.post("/api/bots/{name}/voice")
def api_bot_voice(name: str, req: VoiceReq):
    """Give a monster a different voice. Presentation only: the bot's code and tests are untouched."""
    a = registry.get(name)
    if not a or a["kind"] != "workflow":
        raise HTTPException(404, "no such bot")
    if req.voice not in monster_voice.ARCHETYPES:
        raise HTTPException(400, "unknown voice")
    registry.update_manifest(name, a["version"], {"voice": req.voice})
    monster_voice.prepare(req.voice)
    emit("log", msg=f"{name} now speaks as the {monster_voice.ARCHETYPES[req.voice]['label']}")
    return {"voice": req.voice}


@app.post("/api/bots/{name}/kill")
def api_bot_kill(name: str):
    """Kill a monster: the bot (workflow) is retired, every skill it was stitched from stays installed for reuse."""
    a = registry.get(name)
    if not a or a["kind"] != "workflow":
        raise HTTPException(404, "no such bot")
    if not _busy.acquire(blocking=False):
        raise HTTPException(409, "the scientist is busy")
    try:
        for v in registry.versions(name):
            if v["status"] in ("active", "archived", "candidate"):
                registry.set_status(name, v["version"], "revoked")
        skills = sorted({s["uses"] for s in a["manifest"].get("steps", [])})
        others = {s["uses"] for w in registry.list_artifacts("workflow") for s in w["manifest"].get("steps", [])}
        spare = [s for s in skills if s not in others]
        emit("killed", bot=name, skills=skills, spare=spare,
             msg=f"killed {name}; kept its {len(skills)} skill(s) for future monsters: {', '.join(skills)}")
        emit("say", text="Rest in pieces. I'll keep the parts.")
        return {"killed": name, "kept_skills": skills, "now_spare": spare}
    finally:
        _busy.release()


@app.post("/api/session/new")
def api_session_new():
    if not _busy.acquire(blocking=False):
        raise HTTPException(409, "the scientist is busy")
    try:
        return governance.new_session()
    finally:
        _busy.release()


@app.get("/api/session")
def api_session():
    return governance.session()


@app.get("/api/bots/{name}/value")
def api_bot_value(name: str):
    try:
        return governance.value(name)
    except KeyError:
        raise HTTPException(404, "no such bot")


@app.post("/api/bots/{name}/drill")
def api_bot_drill(name: str):
    if not registry.get(name):
        raise HTTPException(404, "no such bot")
    return _background(f"authority test {name}", lambda: governance.drill(name))


class Change(BaseModel):
    request: str


@app.post("/api/capabilities/{name}/upgrade")
def api_cap_upgrade(name: str, c: Change):
    a = registry.get(name)
    if not a or a["kind"] != "capability":
        raise HTTPException(404, "no such capability")
    if not c.request.strip():
        raise HTTPException(400, "describe the change")
    return _background(f"upgrade {name}", lambda: governance.upgrade(name, c.request.strip()))


@app.get("/api/capabilities/{name}/dependents")
def api_cap_dependents(name: str):
    return {"capability": name, "used_by": governance.dependents(name)}


class Upload(BaseModel):
    folder: str = ""
    files: list[dict]  # [{"name": "invoices/a.txt", "b64": "..."}]


@app.get("/api/inbox")
def api_inbox():
    return [{"folder": f, "files": [p.relative_to(inbox.INBOX / f).as_posix() for p in inbox.files(f)]}
            for f in inbox.folders()]


@app.post("/api/inbox")
def api_inbox_upload(u: Upload):
    if not u.files:
        raise HTTPException(400, "no files")
    try:
        folder = inbox.save(u.folder, u.files)
    except (ValueError, KeyError) as e:
        raise HTTPException(400, str(e))
    names = [p.relative_to(inbox.INBOX / folder).as_posix() for p in inbox.files(folder)]
    emit("files", folder=folder, files=names, msg=f"received {len(names)} file(s) in folder {folder}")
    return {"folder": folder, "files": names}


@app.post("/api/inbox/sample/{name}")
def api_inbox_sample(name: str):
    try:
        folder = inbox.load_sample(name)
    except KeyError:
        raise HTTPException(404, "no such sample")
    names = [p.relative_to(inbox.INBOX / folder).as_posix() for p in inbox.files(folder)]
    emit("files", folder=folder, files=names, msg=f"loaded the sample company files into folder {folder} ({len(names)} files)")
    return {"folder": folder, "files": names}


@app.get("/api/inbox/{folder}/{path:path}")
def api_inbox_file(folder: str, path: str):
    root = (inbox.INBOX / folder).resolve()
    f = (root / path).resolve()
    if root not in f.parents or not f.is_file():
        raise HTTPException(404)
    return FileResponse(f)


class Task(BaseModel):
    task: str


def _background(label: str, fn) -> dict:
    """Run one lab job at a time in a thread; failures are reported as events, never swallowed."""
    if not _busy.acquire(blocking=False):
        raise HTTPException(409, "the scientist is busy")

    def work():
        try:
            sandbox.ensure_infra()
            fn()
        except Exception as e:
            emit("error", msg=f"{label} failed: {e}")
            emit("say", text="That experiment failed.")
        finally:
            _busy.release()
            emit("idle", msg=f"{label} finished")
    threading.Thread(target=work, daemon=True).start()
    return {"started": True}


@app.post("/api/solve")
def api_solve(t: Task):
    from factory.orchestrator import solve
    return _background("task", lambda: solve(t.task))


class Purpose(BaseModel):
    purpose: str


@app.post("/api/bots/create")
def api_bot_create(b: Purpose):
    from factory.orchestrator import create_bot
    return _background("create bot", lambda: create_bot(b.purpose))


@app.get("/api/bots")
def api_bots():
    out = []
    for a in registry.list_artifacts("workflow"):
        m = a["manifest"]
        out.append({"name": a["name"], "version": a["version"], "description": a["description"],
                    "purpose": m.get("purpose") or a["description"], "input_schema": m["signature"].get("in"),
                    "example_input": m.get("example_input"), "steps": [s["uses"] for s in m.get("steps", [])],
                    "runs": a["runs"], "avg_tokens": round(a["tokens"] / a["runs"]) if a["runs"] else 0,
                    "llm": bool(m.get("permissions", {}).get("llm")),
                    "headline": m.get("headline") or "", "examples": m.get("examples") or [],
                    "voice": monster_voice.pick(a["name"], m.get("voice"))})
    return out


class Ask(BaseModel):
    question: str = ""
    input_json: str = ""


@app.post("/api/bots/{name}/sabotage")
def api_bot_sabotage(name: str):
    from factory.orchestrator import sabotage
    if not registry.get(name):
        raise HTTPException(404, "no such bot")
    return _background(f"Igor vs {name}", lambda: sabotage(name))


@app.post("/api/bots/{name}/ask")
def api_bot_ask(name: str, q: Ask):
    from factory.orchestrator import ask_bot
    if not registry.get(name):
        raise HTTPException(404, "no such bot")
    inp = None
    if q.input_json.strip():
        try:
            inp = json.loads(q.input_json)
        except json.JSONDecodeError as e:
            raise HTTPException(400, f"input is not valid JSON: {e}")
    elif not q.question.strip():
        raise HTTPException(400, "ask a question or fill in the form")
    return _background(f"ask {name}", lambda: ask_bot(name, q.question.strip(), inp))


@app.post("/api/optimize")
def api_optimize():
    from factory.compiler import optimize_all
    return _background("optimize", optimize_all)


class RunReq(BaseModel):
    input_json: str


@app.post("/api/run/{name}")
def api_run(name: str, req: RunReq):
    """Operator runs an installed workflow directly (with self-repair), like an external agent would."""
    a = registry.get(name)
    if not a or a["kind"] != "workflow":
        raise HTTPException(404, "no such active workflow")
    try:
        inp = json.loads(req.input_json)
    except json.JSONDecodeError as e:
        raise HTTPException(400, f"input is not valid JSON: {e}")

    def go():
        from factory.orchestrator import run_with_heal
        m = a["manifest"]
        out = run_with_heal(a, inp, gateway.Budget(scope=f"run:{name}", max_usd=0.5,
                                                   max_tokens=m.get("budget", {}).get("max_tokens_per_run") or None),
                            gateway.Budget(scope=f"task:heal {name}", max_usd=config.MAX_USD_PER_TASK))
        emit("result", workflow=name, result=out, input=inp, via="operator (lab)", msg=f"ran {name} from the lab")
    return _background(f"run {name}", go)


@app.post("/api/reset")
def api_reset():
    """Empty the registry for a clean demo. Refused while a job is running."""
    if not _busy.acquire(blocking=False):
        raise HTTPException(409, "the scientist is busy")
    try:
        import subprocess, sys
        subprocess.run([sys.executable, str(Path(__file__).parent / "scripts" / "reset_registry.py")], check=True)
        emit("reset", msg="lab reset: registry is empty")
    finally:
        _busy.release()
    return {"reset": True}


@app.get("/api/status")
def api_status():
    return {"busy": _busy.locked(), "gate_mode": gate.MODE, "install_mode": gate.INSTALL_MODE, "build_model": config.BUILD_MODEL,
            "runtime_model": config.RUNTIME_MODEL, "caps": {"usd_per_task": config.MAX_USD_PER_TASK,
            "iterations": config.MAX_FACTORY_ITERATIONS, "repairs": config.MAX_REPAIR_ATTEMPTS,
            "replans": config.MAX_REPLANS, "llm_calls": config.MAX_LLM_CALLS_PER_TASK}}


@app.get("/api/ledger")
def api_ledger():
    import sqlite3
    since = float((config.DATA / "reset_ts").read_text()) if (config.DATA / "reset_ts").exists() else 0.0
    con = sqlite3.connect(config.DB_PATH)
    by_model = con.execute("SELECT model, COUNT(*), COALESCE(SUM(input_tokens+output_tokens),0), COALESCE(SUM(usd),0) "
                           "FROM ledger WHERE exact=1 AND ts >= ? GROUP BY model ORDER BY 2 DESC", (since,)).fetchall()
    tot = con.execute("SELECT COUNT(*), COALESCE(SUM(input_tokens+output_tokens),0), COALESCE(SUM(usd),0) "
                      "FROM ledger WHERE ts >= ?", (since,)).fetchone()
    con.close()
    return {"calls": tot[0], "tokens": tot[1], "usd": round(tot[2], 4), "since": since,
            "by_model": [{"model": m, "calls": c, "tokens": t, "usd": round(u, 4)} for m, c, t, u in by_model]}


@app.get("/api/voice")
def api_voice(text: str, bot: str = ""):
    vid = None
    if bot:
        a = registry.get(bot)
        arch = monster_voice.pick(bot, a["manifest"].get("voice") if a else None)
        vid = monster_voice.voice_id(arch)
        monster_voice.prepare(arch)  # designed in the background; a stock voice speaks until then
    audio = voice.speak(text, vid)
    if not audio:
        raise HTTPException(404)
    return Response(audio, media_type="audio/mpeg")


@app.post("/api/stt")
async def api_stt(request: Request):
    """Voice in: the browser's recording → ElevenLabs Scribe → text. The text then goes the same way a typed message does."""
    audio = await request.body()
    if len(audio) < 2000:
        raise HTTPException(400, "didn't catch that: the recording was empty")
    try:
        text = await asyncio.to_thread(voice.transcribe, audio, request.headers.get("content-type", "audio/webm"))
    except Exception as e:
        raise HTTPException(502, f"speech-to-text failed: {str(e)[:200]}")
    emit("log", msg=f"🎙 heard: {text[:160]}")
    return {"text": text}


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=int(os.getenv("LAB_PORT", "8777")), log_level="warning")
