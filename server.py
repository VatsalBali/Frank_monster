"""Lab console server: serves the UI, streams factory events (SSE), exposes operator controls.

  .venv/Scripts/python server.py   →  http://localhost:8777
"""
import asyncio
import os
import json
import threading
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response, StreamingResponse
from pydantic import BaseModel

from factory import config, gate, gateway, registry, sandbox, voice
from factory.events import emit

app = FastAPI()
UI = Path(__file__).parent / "ui"
EVENTS = config.DATA / "events.jsonl"
_busy = threading.Lock()


@app.get("/")
def index():
    return FileResponse(UI / "index.html")


@app.get("/events")
async def events(replay: int = 300):
    """Tail data/events.jsonl, so events from any process (CLI, MCP server, UI runs) show up."""
    async def gen():
        EVENTS.touch()
        with EVENTS.open("r", encoding="utf-8") as f:
            lines = f.readlines()
            for line in lines[-replay:]:
                yield f"data: {line.strip()}\n\n"
            buf = ""
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
    return [{k: a[k] for k in ("name", "version", "kind", "status", "description", "runs", "ok_runs", "tokens", "ms")}
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


class Task(BaseModel):
    task: str


@app.post("/api/solve")
def api_solve(t: Task):
    if not _busy.acquire(blocking=False):
        raise HTTPException(409, "the scientist is busy")

    def work():
        from factory.orchestrator import solve
        try:
            sandbox.ensure_infra()
            solve(t.task)
        except Exception as e:
            emit("error", msg=f"task failed: {e}")
            emit("say", text="That experiment failed.")
        finally:
            _busy.release()
    threading.Thread(target=work, daemon=True).start()
    return {"started": True}


@app.get("/api/ledger")
def api_ledger():
    return gateway.ledger_summary()


@app.get("/api/voice")
def api_voice(text: str):
    audio = voice.speak(text)
    if not audio:
        raise HTTPException(404)
    return Response(audio, media_type="audio/mpeg")


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=int(os.getenv("LAB_PORT", "8777")), log_level="warning")
