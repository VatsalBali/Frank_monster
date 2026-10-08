"""Spike: can an ElevenLabs text-only agent act as the factory's LLM backend?
Creates (once) an agent 'frankenstein-factory-brain', sends one code-writing prompt, measures reply/latency."""
import asyncio
import json
import os
import sys
import time
from pathlib import Path

import httpx
import websockets
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
KEY = os.environ["ELEVENLABS_API_KEY"]
API = "https://api.elevenlabs.io/v1"
STATE = ROOT / "data" / "elevenlabs_agent.json"
LLM = sys.argv[1] if len(sys.argv) > 1 else "claude-opus-5-5"


def ensure_agent() -> str:
    if STATE.exists():
        return json.loads(STATE.read_text())["agent_id"]
    body = {
        "name": "frankenstein-factory-brain",
        "conversation_config": {
            "agent": {"first_message": "", "prompt": {"prompt": "You are a precise assistant.", "llm": LLM, "temperature": 0}},
            "conversation": {"text_only": True},
        },
        "platform_settings": {"overrides": {"conversation_config_override": {
            "agent": {"prompt": {"prompt": True, "llm": True}, "first_message": True},
            "conversation": {"text_only": True}}}},
    }
    r = httpx.post(f"{API}/convai/agents/create", headers={"xi-api-key": KEY}, json=body, timeout=30)
    print("create:", r.status_code, r.text[:500])
    r.raise_for_status()
    aid = r.json()["agent_id"]
    STATE.parent.mkdir(exist_ok=True)
    STATE.write_text(json.dumps({"agent_id": aid}))
    return aid


async def ask(agent_id: str, system: str, user: str, llm: str) -> tuple[str, dict]:
    url = f"wss://api.elevenlabs.io/v1/convai/conversation?agent_id={agent_id}"
    meta = {}
    async with websockets.connect(url, additional_headers={"xi-api-key": KEY}, max_size=2**24) as ws:
        await ws.send(json.dumps({"type": "conversation_initiation_client_data",
                                  "conversation_config_override": {
                                      "agent": {"prompt": {"prompt": system, "llm": llm}, "first_message": ""},
                                      "conversation": {"text_only": True}}}))
        sent = False
        parts = []
        t0 = time.time()
        while time.time() - t0 < 300:
            msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=240))
            t = msg.get("type")
            if t == "conversation_initiation_metadata":
                meta["conversation_id"] = msg["conversation_initiation_metadata_event"]["conversation_id"]
                await ws.send(json.dumps({"type": "user_message", "text": user}))
                sent = True
            elif t == "ping":
                await ws.send(json.dumps({"type": "pong", "event_id": msg["ping_event"]["event_id"]}))
            elif t == "agent_response":
                parts.append(msg["agent_response_event"]["agent_response"])
                break
            elif t in ("agent_chat_response_part",):
                pass
            else:
                print("event:", t, json.dumps(msg)[:200])
        meta["seconds"] = round(time.time() - t0, 1)
    return "".join(parts), meta


if __name__ == "__main__":
    aid = ensure_agent()
    system = "You write Python. Reply with ONLY a JSON object {\"impl_py\": str, \"test_py\": str}. No prose."
    user = ("Write impl.py with run(inp: dict) -> dict that takes {'ico': str} (Czech company id), validates the 8-digit "
            "checksum (mod 11 algorithm), and returns {'ico': str, 'valid': bool}. Also write pytest tests in test_py "
            "importing `from impl import run`, covering valid ids 27074358 and 00006947, an invalid checksum, and non-digit input.")
    text, meta = asyncio.run(ask(aid, system, user, LLM))
    print(meta, "chars:", len(text))
    print(text[:3000])
