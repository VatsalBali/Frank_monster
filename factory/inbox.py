"""Files the user hands to the lab (invoices, exports, statements). Stored in data/inbox/<folder>/.

A capability that reads files takes the folder NAME as input (field `folder`), never the contents. When it runs,
the harness copies that folder into the sandbox at ./inbox/<folder>/ (a throw-away, in-memory copy: generated code
can read the user's files but never change the originals). LLM steps get the text of the files in their prompt.
"""
import base64
import re
import shutil
import time
from pathlib import Path

from . import config

INBOX = config.DATA / "inbox"
INBOX.mkdir(exist_ok=True)
SAMPLES = config.ROOT / "samples"
MAX_BYTES = 8_000_000          # per folder sent into the sandbox
TEXT_EXT = {".txt", ".csv", ".tsv", ".json", ".md", ".xml", ".html", ".htm", ".eml", ".log", ".ini", ".yaml", ".yml"}


def _safe(name: str) -> str:
    parts = [re.sub(r"[^\w.\- ()]", "_", p).strip() for p in re.split(r"[\\/]+", name) if p not in ("", ".", "..")]
    return "/".join(p for p in parts if p)[:180]


def folders() -> list[str]:
    return sorted(p.name for p in INBOX.iterdir() if p.is_dir())


def files(folder: str) -> list[Path]:
    root = INBOX / _safe(folder)
    return sorted(p for p in root.rglob("*") if p.is_file()) if root.is_dir() else []


def save(folder: str | None, uploads: list[dict]) -> str:
    """uploads: [{"name": "invoices/a.txt", "b64": "..."}]. Returns the folder name."""
    folder = _safe(folder or "") or time.strftime("upload_%m%d_%H%M%S")
    root = INBOX / folder
    total = 0
    for u in uploads:
        data = base64.b64decode(u["b64"])
        total += len(data)
        if total > MAX_BYTES:
            raise ValueError(f"files are larger than {MAX_BYTES // 1_000_000} MB")
        p = root / _safe(u["name"])
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    return folder


USED = INBOX / ".used.json"


def mark_used(folders_: list[str]) -> None:
    import json
    try:
        used = set(json.loads(USED.read_text()))
    except (OSError, ValueError):
        used = set()
    USED.write_text(json.dumps(sorted(used | set(folders_))))


def fresh_upload(max_age_s: float = 900) -> str | None:
    """The newest folder uploaded in the last few minutes that no task has used yet (the user attached files and the
    reference got lost on the way)."""
    import json
    try:
        used = set(json.loads(USED.read_text()))
    except (OSError, ValueError):
        used = set()
    cands = [p for p in INBOX.iterdir() if p.is_dir() and p.name not in used and time.time() - p.stat().st_mtime < max_age_s]
    return max(cands, key=lambda p: p.stat().st_mtime).name if cands else None


def load_sample(name: str) -> str:
    src = SAMPLES / _safe(name)
    if not src.is_dir():
        raise KeyError(name)
    dst = INBOX / src.name
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)
    return src.name


def refs(obj) -> list[str]:
    """Inbox folders an input refers to (any string value that is a folder name, or inbox/<folder>)."""
    have, found = set(folders()), []

    def walk(v):
        if isinstance(v, str):
            s = v.strip().removeprefix("./").removeprefix("inbox/").strip("/")
            if s in have and s not in found:
                found.append(s)
        elif isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, list):
            for x in v[:200]:
                walk(x)
    walk(obj)
    return found


def mentioned(text: str) -> list[str]:
    return [f for f in folders() if re.search(rf"(?<![\w-]){re.escape(f)}(?![\w-])", text or "")]


def sandbox_files(obj) -> dict:
    """Files to place in the sandbox for an input: {"inbox/<folder>/<path>": str | {"b64": ...}}."""
    out, total = {}, 0
    for f in refs(obj):
        root = INBOX / f
        for p in files(f):
            data = p.read_bytes()
            total += len(data)
            if total > MAX_BYTES:
                raise ValueError(f"folder {f} is larger than {MAX_BYTES // 1_000_000} MB")
            key = f"inbox/{f}/{p.relative_to(root).as_posix()}"
            if p.suffix.lower() in TEXT_EXT:
                try:
                    out[key] = data.decode("utf-8")
                    continue
                except UnicodeDecodeError:
                    pass
            out[key] = {"b64": base64.b64encode(data).decode()}
    return out


def describe(folder: str, head: int = 4) -> str:
    """Short listing for prompts: every file with its size, and the first lines of text files (one per kind)."""
    root = INBOX / folder
    lines, shown = [f"Folder `{folder}` (read it at ./inbox/{folder}/):"], set()
    for p in files(folder)[:80]:
        rel = p.relative_to(root).as_posix()
        lines.append(f"  - {rel} ({p.stat().st_size:,} bytes)")
        # every table has its own columns; documents in one sub-folder usually share a few layouts
        kind = p if p.suffix.lower() in (".csv", ".tsv", ".json") else (p.parent, p.suffix.lower())
        if p.suffix.lower() in TEXT_EXT and kind not in shown:
            shown.add(kind)
            try:
                txt = p.read_text(encoding="utf-8").splitlines()[:head]
                lines += [f"      | {l[:160]}" for l in txt]
            except UnicodeDecodeError:
                pass
    if len(files(folder)) > 80:
        lines.append(f"  … and {len(files(folder)) - 80} more files")
    return "\n".join(lines)


def texts(folder: str, limit: int = 30000) -> str:
    """The text content of a folder, for LLM steps (binary files are listed by name only)."""
    root, parts, n = INBOX / folder, [], 0
    for p in files(folder):
        rel = p.relative_to(root).as_posix()
        try:
            t = p.read_text(encoding="utf-8") if p.suffix.lower() in TEXT_EXT else f"(binary file, {p.stat().st_size} bytes)"
        except UnicodeDecodeError:
            t = "(binary file)"
        chunk = f"=== {rel} ===\n{t}\n"
        if n + len(chunk) > limit:
            parts.append(f"… (more files omitted: the folder is larger than {limit} characters)")
            break
        parts.append(chunk)
        n += len(chunk)
    return "\n".join(parts)
