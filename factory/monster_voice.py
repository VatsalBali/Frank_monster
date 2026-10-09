"""Every monster gets a voice of its own. Five archetypes are designed once with ElevenLabs Voice Design, saved to
the account and cached in data/monster_voices.json; a stock voice stands in until (or if) a design succeeds.
A bot's spoken verdict is its answer headline (filled by code, 0 LLM tokens), read in its archetype's voice."""
import json
import os
import threading
import zlib

import httpx

from . import config
from .events import emit

API = "https://api.elevenlabs.io/v1"
STORE = config.DATA / "monster_voices.json"
_lock = threading.Lock()

ARCHETYPES = {
    "brute": {"label": "Brute", "fits": "blunt checks, verdicts, anything that should sound strong and no-nonsense",
              "design": "A huge friendly swamp ogre stitched together in a laboratory, very deep gravelly rumbling bass "
                        "voice, slow and gruff but kind, slightly hoarse",
              "fallback": "pNInz6obpgDQGcFmaJgB"},  # Adam
    "gremlin": {"label": "Gremlin", "fits": "quick lookups, prices, currencies, anything fast or playful",
                "design": "A tiny mischievous goblin, high-pitched squeaky nasal voice, fast and impish, giggly",
                "fallback": "N2lVS1w4EtoT3dr4eOWO"},  # Callum
    "ghoul": {"label": "Ghoul", "fits": "audits, reconciliation, risk, debts, anything about money going missing",
              "design": "An old undead bookkeeper ghoul, dry raspy whispering voice, slow and precise, creepy but polite",
              "fallback": "pqHfZKP75CvOlQylNhV4"},  # Bill
    "golem": {"label": "Golem", "fits": "data crunching, forecasts, reports, steady analytical work",
              "design": "A heavy stone golem, low hollow resonant voice with a slight echo, slow, calm and mechanical",
              "fallback": "onwK4e9ZLuTAKqWW03F9"},  # Daniel
    "witch": {"label": "Witch", "fits": "research, knowledge questions, advice, explanations, anything wise",
              "design": "An old crackly swamp witch, raspy cackling female voice, theatrical and knowing, medium pitch",
              "fallback": "pFZP5JQG7iQjIQuC4Bku"},  # Lily
}
# Voice Design needs 100-1000 characters of sample text
PREVIEW = ("Hah. I checked all thirteen invoices for you, one by one. Two problems. One invoice was paid twice: "
           "sixteen thousand crowns to get back. The rest is in the report.")


def _key() -> str | None:
    return os.getenv("ELEVENLABS_API_KEY")


def _load() -> dict:
    try:
        return json.loads(STORE.read_text())
    except (OSError, ValueError):
        return {}


def pick(name: str, hint: str | None = None) -> str:
    """The archetype for a bot: the one chosen at build time, else a stable pick from its name."""
    if hint in ARCHETYPES:
        return hint
    return list(ARCHETYPES)[zlib.crc32(name.encode()) % len(ARCHETYPES)]


def design(arch: str) -> str | None:
    """Design and save the archetype's voice once. Returns its voice id (None: use the stock fallback)."""
    with _lock:
        have = _load()
        if have.get(arch):
            return have[arch]
        k = _key()
        if not k:
            return None
        a = ARCHETYPES[arch]
        try:
            r = httpx.post(f"{API}/text-to-voice/design", headers={"xi-api-key": k}, timeout=120,
                           json={"voice_description": a["design"], "model_id": "eleven_ttv_v3", "text": PREVIEW})
            r.raise_for_status()
            gid = r.json()["previews"][0]["generated_voice_id"]
            r = httpx.post(f"{API}/text-to-voice", headers={"xi-api-key": k}, timeout=60,
                           json={"voice_name": f"Frankenstein Lab · {a['label']}", "voice_description": a["design"],
                                 "generated_voice_id": gid})
            r.raise_for_status()
            vid = r.json()["voice_id"]
        except Exception as e:
            emit("log", msg=f"voice design for {arch} failed, using a stock voice: {str(e)[:160]}")
            return None
        have[arch] = vid
        STORE.write_text(json.dumps(have, indent=1))
        emit("log", msg=f"designed a {a['label'].lower()} voice for the monsters ({vid})")
        return vid


def voice_id(arch: str) -> str:
    return _load().get(arch) or ARCHETYPES[arch]["fallback"]


def prepare(arch: str) -> None:
    """Design in the background so the first answer doesn't wait ~15 s for it."""
    if not _load().get(arch):
        threading.Thread(target=design, args=(arch,), daemon=True).start()
