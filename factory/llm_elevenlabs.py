"""LLM backend over ElevenLabs Agents (text-only chat mode).

ElevenLabs has no plain completions endpoint, but its agents run real LLMs (Claude, Gemini, GPT). Each call opens
a text-only conversation with one agent, overrides its system prompt + model, sends one user message and reads
the streamed reply. We read the raw stream deltas: the final `agent_response` is normalised for speech and
strips characters like `*`, which would corrupt code.
"""
import asyncio
import json
import os
import threading
import time

import httpx
import websockets

from . import config

API = "https://api.elevenlabs.io/v1"
STATE = config.DATA / "elevenlabs_agent.json"
_lock = threading.Lock()


def key() -> str:
    k = os.getenv("ELEVENLABS_API_KEY")
    if not k:
        raise RuntimeError("ELEVENLABS_API_KEY missing in .env")
    return k


def agent_id() -> str:
    with _lock:
        if STATE.exists():
            aid = json.loads(STATE.read_text())["agent_id"]
            r = httpx.get(f"{API}/convai/agents/{aid}", headers={"xi-api-key": key()}, timeout=20)
            if r.status_code == 200:
                return aid
        body = {
            "name": "frankenstein-factory-brain",
            "conversation_config": {
                "agent": {"first_message": "", "prompt": {"prompt": "You are precise.", "llm": config.BUILD_MODEL,
                                                          "temperature": 0,
                                                          # never silently swap in another vendor's model
                                                          "backup_llm_config": {"preference": "disabled"},
                                                          # ElevenLabs aborts if the first token takes >15 s;
                                                          # low reasoning effort keeps Opus well under that
                                                          "cascade_timeout_seconds": 15, "reasoning_effort": "low"}},
                "conversation": {"text_only": True, "max_duration_seconds": 900},
            },
            "platform_settings": {"overrides": {"conversation_config_override": {
                "agent": {"prompt": {"prompt": True, "llm": True}, "first_message": True},
                "conversation": {"text_only": True}}}},
        }
        r = httpx.post(f"{API}/convai/agents/create", headers={"xi-api-key": key()}, json=body, timeout=30)
        r.raise_for_status()
        aid = r.json()["agent_id"]
        STATE.write_text(json.dumps({"agent_id": aid}))
        return aid


async def _ask(aid: str, system: str, user: str, llm: str, timeout: float) -> tuple[str, str]:
    url = f"wss://api.elevenlabs.io/v1/convai/conversation?agent_id={aid}"
    parts: list[str] = []
    final = None
    conv_id = ""
    async with websockets.connect(url, additional_headers={"xi-api-key": key()}, max_size=2**25,
                                  open_timeout=30) as ws:
        await ws.send(json.dumps({"type": "conversation_initiation_client_data",
                                  "conversation_config_override": {
                                      "agent": {"prompt": {"prompt": system, "llm": llm}, "first_message": ""},
                                      "conversation": {"text_only": True}}}))
        deadline = time.time() + timeout
        while time.time() < deadline:
            msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=max(5, deadline - time.time())))
            t = msg.get("type")
            if t == "conversation_initiation_metadata":
                conv_id = msg["conversation_initiation_metadata_event"]["conversation_id"]
                await ws.send(json.dumps({"type": "user_message", "text": user}))
            elif t == "ping":
                await ws.send(json.dumps({"type": "pong", "event_id": msg["ping_event"]["event_id"]}))
            elif t == "agent_chat_response_part":
                p = msg.get("text_response_part", {})
                if p.get("type") == "delta":
                    parts.append(p.get("text", ""))
            elif t == "agent_response":
                final = msg["agent_response_event"]["agent_response"]
                break
    if final is None and not parts:
        raise TimeoutError("no reply from ElevenLabs agent")
    return ("".join(parts) or final or ""), conv_id


def ask(system: str, user: str, llm: str, timeout: float = 300, retries: int = 2) -> tuple[str, str, float]:
    """Returns (raw_text, conversation_id, seconds)."""
    aid = agent_id()
    last = None
    for attempt in range(retries + 1):
        t0 = time.time()
        try:
            text, cid = asyncio.run(_ask(aid, system, user, llm, timeout))
            return text, cid, time.time() - t0
        except Exception as e:  # network hiccups, socket closed by server
            last = e
            time.sleep(2 + attempt * 3)
    raise RuntimeError(f"ElevenLabs LLM call failed: {last}")


def usage(conversation_id: str) -> dict | None:
    """Actual tokens/$ and credits for a finished conversation (available a few seconds after it ends)."""
    r = httpx.get(f"{API}/convai/conversations/{conversation_id}", headers={"xi-api-key": key()}, timeout=30)
    if r.status_code != 200:
        return None
    md = r.json().get("metadata", {})
    mu = (md.get("charging") or {}).get("llm_usage", {}).get("irreversible_generation", {}).get("model_usage", {})
    inp = sum(v.get("input", {}).get("tokens", 0) + v.get("input_cache_read", {}).get("tokens", 0) for v in mu.values())
    out = sum(v.get("output_total", {}).get("tokens", 0) for v in mu.values())
    usd = sum(v.get("input", {}).get("price", 0) + v.get("input_cache_read", {}).get("price", 0)
              + v.get("input_cache_write", {}).get("price", 0) + v.get("output_total", {}).get("price", 0)
              for v in mu.values())
    return {"input_tokens": inp, "output_tokens": out, "usd": usd, "credits": md.get("cost"), "models": list(mu)}
