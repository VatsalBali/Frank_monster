"""Central configuration. All hard caps live here and are enforced in code, not prompts."""
from pathlib import Path
import os

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

DATA = ROOT / "data"
REGISTRY_DIR = DATA / "registry"      # agent-built artifacts (git-tracked separately)
TRACES_DIR = DATA / "traces"
RUNS_DIR = DATA / "runs"
DB_PATH = DATA / "factory.db"

# Models: big model builds, small model runs LLM steps inside workflows.
BUILD_MODEL = os.getenv("FACTORY_BUILD_MODEL", "claude-opus-5-5")
RUNTIME_MODEL = os.getenv("FACTORY_RUNTIME_MODEL", "claude-haiku-4-5")  # served via ElevenLabs Agents

# Hard caps (brief: "self-iterations and spend per run are capped in code").
MAX_FACTORY_ITERATIONS = int(os.getenv("MAX_FACTORY_ITERATIONS", "12"))
MAX_REPAIR_ATTEMPTS = int(os.getenv("MAX_REPAIR_ATTEMPTS", "3"))
MAX_REPLANS = int(os.getenv("MAX_REPLANS", "2"))
MIN_TESTS = 3  # harness rejects capabilities with fewer passing tests
MAX_USD_PER_TASK = float(os.getenv("MAX_USD_PER_TASK", "2.00"))
MAX_LLM_CALLS_PER_TASK = int(os.getenv("MAX_LLM_CALLS_PER_TASK", "40"))

# USD per million tokens (input, output). Used by the gateway ledger.
PRICES = {
    "claude-opus-5-5": (4.0, 20.0),
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-haiku-4-5": (1.0, 5.0),
}

# Sandbox: Docker inside WSL. Generated code never runs on the Windows host.
WSL_DISTRO = os.getenv("WSL_DISTRO", "Ubuntu-24.04")
SANDBOX_IMAGE = os.getenv("SANDBOX_IMAGE", "python:3.12-slim")
SANDBOX_TIMEOUT_S = 60
SANDBOX_MEMORY = "512m"

for d in (DATA, REGISTRY_DIR, TRACES_DIR, RUNS_DIR):
    d.mkdir(parents=True, exist_ok=True)
