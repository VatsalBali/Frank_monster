"""Persistent, versioned registry of agent-built artifacts: capabilities, workflows and passes.

Layout: data/registry/<name>/v<N>/{manifest.json, impl.py, test_impl.py, ...}
Metadata lives in SQLite; artifact files are committed to a git repo inside data/registry so every
version has a diff and rollback is just re-activating an older version.
"""
import json
import re
import sqlite3
import shutil
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


def update_manifest(name: str, version: int, patch: dict) -> None:
    """Add presentation metadata (headline template, example questions) to an artifact. Never touches code."""
    a = get(name, version)
    m = {**a["manifest"], **patch}
    con = _db()
    con.execute("UPDATE artifacts SET manifest=? WHERE name=? AND version=?", (json.dumps(m), name, version))
    con.commit()
    con.close()
    (artifact_dir(name, version) / "manifest.json").write_text(json.dumps(m, indent=2), encoding="utf-8")


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


def _host_match(a: str, b: str) -> bool:
    return a == b or a.endswith("." + b) or b.endswith("." + a)


_STOP = {"the", "and", "for", "from", "with", "into", "that", "this", "each", "given", "using", "return", "returns"}


def _stem(w: str) -> str:
    for suf, rep in (("ies", "y"), ("ing", ""), ("es", ""), ("s", "")):
        if w.endswith(suf) and len(w) - len(suf) >= 3:
            return w[: len(w) - len(suf)] + rep
    return w


def _words(text: str) -> set[str]:
    return {_stem(w) for w in re.split(r"[^a-z0-9]+", text.lower()) if len(w) > 2 and w not in _STOP}


def similar_code(text: str, exclude: str = "", min_score: float = 0.4) -> dict | None:
    """The installed Python part most similar to `text`: the share of the new part's words (name + description)
    found in an existing part's name + description, with at least 2 words in common."""
    want, best = _words(text), (0.0, None)
    for a in list_artifacts("capability"):
        if a["name"] == exclude or a["manifest"].get("impl") != "code":
            continue
        have = _words(a["name"].replace("_", " ") + " " + a["description"])
        common = len(want & have)
        score = common / max(1, len(want)) if common >= 2 else 0.0
        if score > best[0]:
            best = (score, a)
    return best[1] if best[0] >= min_score else None


def knowhow(hosts: list[str], exclude: str = "", max_refs: int = 2, like: str = "") -> str:
    """What earlier builds learned about these hosts: their notes plus the working code of up to `max_refs`
    installed capabilities that use them. Lets a new build skip exploration it has already paid for."""
    want = {h.lower().lstrip("*.") for h in hosts}
    notes, refs = [], []
    sim = similar_code(like, exclude) if like else None
    if sim:
        refs.append(f"# a similar installed part, {sim['name']}: adapt it instead of starting from scratch\n"
                    + (artifact_dir(sim["name"], sim["version"]) / "impl.py").read_text(encoding="utf-8")[:2500])
    if not want:
        return "\n".join(refs)
    for a in list_artifacts("capability"):
        m = a["manifest"]
        mine = {h.lower().lstrip("*.") for h in m.get("permissions", {}).get("net", [])}
        if a["name"] == exclude or not any(_host_match(w, h) for w in want for h in mine):
            continue
        if m.get("notes"):
            notes.append(f"- {a['name']}: {m['notes']}")
        impl = artifact_dir(a["name"], a["version"]) / "impl.py"
        if impl.exists() and len(refs) < max_refs and not (sim and a["name"] == sim["name"]):
            refs.append(f"# working code of installed capability {a['name']} (hosts {sorted(mine)})\n"
                        + impl.read_text(encoding="utf-8")[:2500])
    if LESSONS.exists():
        for x in json.loads(LESSONS.read_text(encoding="utf-8")):
            hs = {h.lower().lstrip("*.") for h in x["hosts"]}
            if x["name"] != exclude and any(_host_match(w, h) for w in want for h in hs):
                notes.append(f"- {x['name']} (an earlier attempt, since removed): {x['notes']}")
    out = ("Notes:\n" + "\n".join(notes) + "\n") if notes else ""
    return out + ("\n".join(refs) if refs else "")


LESSONS = config.DATA / "knowhow.json"


def _save_lesson(m: dict) -> None:
    """A dropped capability's code goes, but what it learned about its hosts stays."""
    if not m.get("notes") or not m.get("permissions", {}).get("net"):
        return
    lessons = json.loads(LESSONS.read_text(encoding="utf-8")) if LESSONS.exists() else []
    lessons = [x for x in lessons if x["name"] != m["name"]][-40:]
    lessons.append({"name": m["name"], "hosts": m["permissions"]["net"], "notes": m["notes"]})
    LESSONS.write_text(json.dumps(lessons, indent=1), encoding="utf-8")


def prune(drop_unused_since: float | None = None) -> dict:
    """Keep only what is useful for the next build: active versions, plus formerly installed versions (rollback).
    Drops versions that never passed install (failed attempts) with their files, sandbox scratch dirs and traces no
    kept version points to. With `drop_unused_since`, also drops capabilities created since then that no active
    workflow uses: the leftovers of a failed build."""
    con = _db()
    rows = [dict(r) for r in con.execute("SELECT name, version, kind, status, created, manifest FROM artifacts")]
    used = set()
    for r in rows:
        if r["kind"] == "workflow" and r["status"] == "active":
            used |= {s["uses"] for s in json.loads(r["manifest"]).get("steps", [])}
    dropped = []
    for r in rows:
        d = artifact_dir(r["name"], r["version"])
        never_installed = r["status"] == "candidate" or (r["status"] == "archived" and not (d / "test_report.json").exists())
        orphan = (drop_unused_since is not None and r["kind"] == "capability" and r["created"] >= drop_unused_since
                  and r["name"] not in used)
        if orphan and r["status"] == "active":
            _save_lesson(json.loads(r["manifest"]))
        if never_installed or orphan:
            con.execute("DELETE FROM artifacts WHERE name=? AND version=?", (r["name"], r["version"]))
            shutil.rmtree(d, ignore_errors=True)
            dropped.append(f"{r['name']} v{r['version']}")
            if not any(d.parent.iterdir()):
                d.parent.rmdir()
    con.commit()
    keep_traces = {json.loads(m[0]).get("lineage", {}).get("trace") for m in con.execute("SELECT manifest FROM artifacts")}
    con.close()
    for t in config.TRACES_DIR.glob("*.json"):
        if t.name not in keep_traces:
            t.unlink(missing_ok=True)
    for run in config.RUNS_DIR.glob("r*"):
        shutil.rmtree(run, ignore_errors=True)
    if dropped:
        _git("add", "-A")
        _git("commit", "-q", "-m", f"prune {len(dropped)} versions")
    return {"dropped": dropped}


def catalog_text(kind: str | None = None) -> str:
    """Compact, token-cheap listing for prompts. This is the ONLY discovery the team ships;
    anything smarter (search, dedup, ranking) has to be built by the factory itself."""
    lines = []
    for a in list_artifacts(kind):
        m = a["manifest"]
        sig = m.get("signature", {})
        net = m.get("permissions", {}).get("net") or []
        lines.append(f"- {a['name']} v{a['version']} [{a['kind']}] in={sig.get('in')} out={sig.get('out')}"
                     + (f" hosts={','.join(net)}" if net else "") + f" :: {a['description']}")
    return "\n".join(lines) or "(registry is empty)"


def _row(r: sqlite3.Row) -> dict:
    d = dict(r)
    d["manifest"] = json.loads(d["manifest"])
    d["path"] = str(artifact_dir(d["name"], d["version"]))
    return d
