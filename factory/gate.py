"""Operator approval gate. Detection, creation and testing stay with the agent; the human approves installs
and any widening of authority.

Modes: 'console' (decided in the lab UI; works across processes via files in data/gates),
'cli' (terminal prompt), 'auto' (unattended dev runs — every auto-approval is logged as such)."""
import json
import os
import time
import uuid

from . import config
from .events import emit

MODE = os.getenv("GATE_MODE", "console")
GATES = config.DATA / "gates"
GATES.mkdir(exist_ok=True)


def ask(kind: str, title: str, detail: dict, timeout: float = 1800) -> bool:
    gid = uuid.uuid4().hex[:8]
    emit("gate", id=gid, gate=kind, title=title, detail=detail, msg=f"approval needed: {title}")
    if MODE == "auto":
        ok, by = True, "auto-mode"
    elif MODE == "cli":
        ok, by = input(f"\n[GATE] {title}\n  approve? [y/N] ").strip().lower() in ("y", "yes"), "operator"
    else:
        (GATES / f"{gid}.pending").write_text(json.dumps({"title": title, "detail": detail}), encoding="utf-8")
        decision = GATES / f"{gid}.decision"
        deadline = time.time() + timeout
        while not decision.exists() and time.time() < deadline:
            time.sleep(0.5)
        ok = decision.exists() and decision.read_text().strip() == "approve"
        by = "operator" if decision.exists() else "timeout"
        (GATES / f"{gid}.pending").unlink(missing_ok=True)
    emit("gate_result", id=gid, approved=ok, by=by, msg=f"{'approved' if ok else 'rejected'} ({by}): {title}")
    return ok


def decide(gid: str, approved: bool) -> bool:
    if not (GATES / f"{gid}.pending").exists():
        return False
    (GATES / f"{gid}.decision").write_text("approve" if approved else "reject")
    return True


def pending() -> list[dict]:
    return [{"id": p.stem, **json.loads(p.read_text(encoding="utf-8"))} for p in GATES.glob("*.pending")]
