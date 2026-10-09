"""Text-to-speech for the scientist and the monsters (ElevenLabs), cached by voice+text so rehearsals don't burn
credits."""
import hashlib
import os

import httpx

from . import config

CACHE = config.DATA / "voice"
CACHE.mkdir(exist_ok=True)
VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "JBFqnCBsd6RMkjVDRZzb")  # the scientist: premade "George", theatrical
MODEL = os.getenv("ELEVENLABS_TTS_MODEL", "eleven_flash_v2_5")


def speak(text: str, voice_id: str | None = None) -> bytes | None:
    text = text.strip()[:400]
    vid = voice_id or VOICE_ID
    if not text:
        return None
    f = CACHE / (hashlib.sha1(f"{vid}|{MODEL}|{text}".encode()).hexdigest() + ".mp3")
    if f.exists():
        return f.read_bytes()
    key = os.getenv("ELEVENLABS_API_KEY")
    if not key:
        return None
    settings = ({"stability": 0.4, "similarity_boost": 0.8, "style": 0.5} if vid == VOICE_ID
                else {"stability": 0.35, "similarity_boost": 0.85, "style": 0.6})
    r = httpx.post(f"https://api.elevenlabs.io/v1/text-to-speech/{vid}", headers={"xi-api-key": key}, timeout=60,
                   json={"text": text, "model_id": MODEL, "voice_settings": settings})
    if r.status_code != 200:
        return None  # out of quota etc. — the text still shows
    f.write_bytes(r.content)
    return r.content
