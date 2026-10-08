"""In-process event bus. Every factory action is an event; the lab UI is just a view of this stream."""
import json
import sys
import queue
import threading
import time
from typing import Any

from . import config

_subscribers: list[queue.Queue] = []
_lock = threading.Lock()
_LOG = config.DATA / "events.jsonl"


def emit(kind: str, **data: Any) -> dict:
    """kind examples: stage, log, test, say, tile, flask, cost, gate, knock."""
    ev = {"ts": time.time(), "kind": kind, **data}
    with _lock:
        with _LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(ev, default=str) + "\n")
        for q in list(_subscribers):
            q.put(ev)
    line = data.get("msg") or data.get("text") or data.get("stage") or ""
    print(f"[{kind}] {line}", file=sys.stderr, flush=True)  # stderr: stdout belongs to MCP stdio
    return ev


def subscribe() -> queue.Queue:
    q: queue.Queue = queue.Queue()
    with _lock:
        _subscribers.append(q)
    return q


def unsubscribe(q: queue.Queue) -> None:
    with _lock:
        if q in _subscribers:
            _subscribers.remove(q)
