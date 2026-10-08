"""Wipe agent-built artifacts so a demo starts from an EMPTY registry (ledger and voice cache are kept)."""
import shutil
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from factory import config  # noqa: E402

import os, stat


def _force(func, path, _exc):  # git objects are read-only on Windows
    os.chmod(path, stat.S_IWRITE)
    func(path)


shutil.rmtree(config.REGISTRY_DIR, onexc=_force)
config.REGISTRY_DIR.mkdir(parents=True)
con = sqlite3.connect(config.DB_PATH)
con.execute("DROP TABLE IF EXISTS artifacts")
con.commit()
(config.DATA / "events.jsonl").write_text("")
print("registry is empty")
