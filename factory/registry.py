"""Persistent, versioned registry of agent-built artifacts: capabilities, workflows and passes.

Layout: data/registry/<name>/v<N>/{manifest.json, impl.py, test_impl.py, ...}
Metadata lives in SQLite; artifact files are committed to a git repo inside data/registry so every
version has a diff and rollback is just re-activating an older version.
"""
import json
import re
import sqlite3
import subprocess
import time
from pathlib import Path

from . import config
from .events import emit

KINDS = ("capability", "workflow", "pass")
STATUSES = ("candidate", "active", "archived", "revoked")


def _db() -> sqlite3.Connection:
    con = sqlite3.connect(config.DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute(
        """CREATE TABLE IF NOT EXISTS artifacts(
            name TEXT, version INT, kind TEXT, status TEXT, description TEXT,
            manifest TEXT, created REAL,
            runs INT DEFAULT 0, ok_runs INT DEFAULT 0, tokens INT DEFAULT 0, ms INT DEFAULT 0,
            PRIMARY KEY(name, version))"""
    )
    return con


def _git(*args: str) -> None:
    repo = config.REGISTRY_DIR
    if not (repo / ".git").exists():
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", *args], cwd=repo, check=False, capture_output=True)


def artifact_dir(name: str, version: int) -> Path:
    return config.REGISTRY_DIR / name / f"v{version}"


def next_version(name: str) -> int:
    con = _db()
    row = con.execute("SELECT MAX(version) FROM artifacts WHERE name=?", (name,)).fetchone()
    con.close()
    return (row[0] or 0) + 1


def save_candidate(manifest: dict, files: dict[str, str]) -> int:
    """Write a candidate version to disk. It is NOT usable until install() passes the gate."""
    name = manifest["name"]
    if not re.fullmatch(r"[a-z][a-z0-9_]{1,48}", name):
        raise ValueError(f"bad artifact name: {name!r}")
    if manifest.get("kind") not in KINDS:
        raise ValueError(f"bad kind: {manifest.get('kind')!r}")
    version = next_version(name)
    manifest = {**manifest, "version": version}
    d = artifact_dir(name, version)
    d.mkdir(parents=True, exist_ok=True)
    for fname, content in files.items():
        if "/" in fname or "\\" in fname or fname.startswith("."):
            raise ValueError(f"bad file name: {fname!r}")
        (d / fname).write_text(content, encoding="utf-8")
    (d / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    con = _db()
    con.execute("INSERT INTO artifacts(name,version,kind,status,description,manifest,created) VALUES(?,?,?,?,?,?,?)",
                (name, version, manifest["kind"], "candidate", manifest.get("description", ""),
                 json.dumps(manifest), time.time()))
    con.commit()
    con.close()
    return version


def install(name: str, version: int, test_report: dict) -> None:
    """Gate-only entry point: activates a version. Refuses unless the attached test report passed."""
    if not test_report.get("passed"):
        raise PermissionError(f"refusing to install {name} v{version}: tests did not pass")
    con = _db()
    con.execute("UPDATE artifacts SET status='archived' WHERE name=? AND status='active'", (name,))
    con.execute("UPDATE artifacts SET status='active' WHERE name=? AND version=?", (name, version))
    con.commit()
    con.close()
    d = artifact_dir(name, version)
    (d / "test_report.json").write_text(json.dumps(test_report, indent=2), encoding="utf-8")
    _git("add", "-A")
    _git("commit", "-q", "-m", f"install {name} v{version}")
    m = get(name, version)
    emit("tile", name=name, version=version, artifact_kind=m["kind"], status="active",
         description=m.get("description", ""), msg=f"installed {name} v{version}")


def set_status(name: str, version: int, status: str) -> None:
    assert status in STATUSES
    con = _db()
    con.execute("UPDATE artifacts SET status=? WHERE name=? AND version=?", (status, name, version))
    con.commit()
    con.close()
    emit("tile", name=name, version=version, status=status, msg=f"{name} v{version} → {status}")


def rollback(name: str) -> int:
    """Re-activate the newest archived version below the active one."""
    cur = get(name)
    if not cur:
        raise KeyError(name)
    con = _db()
    row = con.execute("SELECT version FROM artifacts WHERE name=? AND status='archived' AND version<? "
                      "ORDER BY version DESC LIMIT 1", (name, cur["version"])).fetchone()
    if not row:
        con.close()
        raise ValueError(f"{name}: nothing to roll back to")
    con.execute("UPDATE artifacts SET status='archived' WHERE name=? AND version=?", (name, cur["version"]))
    con.execute("UPDATE artifacts SET status='active' WHERE name=? AND version=?", (name, row[0]))
    con.commit()
    con.close()
    emit("tile", name=name, version=row[0], status="active", msg=f"rolled back {name} to v{row[0]}")
    return row[0]


def get(name: str, version: int | None = None) -> dict | None:
    con = _db()
    if version is None:
        row = con.execute("SELECT * FROM artifacts WHERE name=? AND status='active'", (name,)).fetchone()
    else:
        row = con.execute("SELECT * FROM artifacts WHERE name=? AND version=?", (name, version)).fetchone()
    con.close()
    return _row(row) if row else None


def list_artifacts(kind: str | None = None, status: str | None = "active") -> list[dict]:
    q, args = "SELECT * FROM artifacts WHERE 1=1", []
    if kind:
        q += " AND kind=?"; args.append(kind)
    if status:
        q += " AND status=?"; args.append(status)
    con = _db()
    rows = con.execute(q + " ORDER BY name, version", args).fetchall()
    con.close()
    return [_row(r) for r in rows]


def versions(name: str) -> list[dict]:
    con = _db()
    rows = con.execute("SELECT * FROM artifacts WHERE name=? ORDER BY version", (name,)).fetchall()
    con.close()
    return [_row(r) for r in rows]


def record_run(name: str, version: int, ok: bool, tokens: int, ms: int) -> None:
    con = _db()
    con.execute("UPDATE artifacts SET runs=runs+1, ok_runs=ok_runs+?, tokens=tokens+?, ms=ms+? "
                "WHERE name=? AND version=?", (int(ok), tokens, ms, name, version))
    con.commit()
    con.close()


def catalog_text(kind: str | None = None) -> str:
    """Compact, token-cheap listing for prompts. This is the ONLY discovery the team ships;
    anything smarter (search, dedup, ranking) has to be built by the factory itself."""
    lines = []
    for a in list_artifacts(kind):
        m = a["manifest"]
        sig = m.get("signature", {})
        lines.append(f"- {a['name']} v{a['version']} [{a['kind']}] in={sig.get('in')} out={sig.get('out')} :: {a['description']}")
    return "\n".join(lines) or "(registry is empty)"


def _row(r: sqlite3.Row) -> dict:
    d = dict(r)
    d["manifest"] = json.loads(d["manifest"])
    d["path"] = str(artifact_dir(d["name"], d["version"]))
    return d
