"""Allowlisting egress proxy. The only route out of the sandbox network.

Each sandbox run authenticates as its run id (Proxy-Authorization basic user). Allowed hosts for that run are
read from /allow/<run_id>.json, written by the executor from the artifact's declared permissions.
Anything else gets 403 and is logged — so "declared permissions" are enforced, not decorative.
"""
import asyncio
import base64
import json
import re
import sys
from pathlib import Path

ALLOW_DIR = Path("/allow")


def allowed(run_id: str, host: str) -> bool:
    if not re.fullmatch(r"[a-zA-Z0-9_-]{4,64}", run_id or ""):
        return False
    f = ALLOW_DIR / f"{run_id}.json"
    if not f.exists():
        return False
    hosts = json.loads(f.read_text()).get("net", [])
    host = host.lower()
    return any(host == h or host.endswith("." + h) for h in hosts)


def run_id_from(headers: dict) -> str:
    auth = headers.get("proxy-authorization", "")
    if auth.lower().startswith("basic "):
        try:
            return base64.b64decode(auth[6:]).decode().split(":", 1)[0]
        except Exception:
            return ""
    return ""


async def pipe(r, w):
    try:
        while data := await r.read(65536):
            w.write(data)
            await w.drain()
    except Exception:
        pass
    finally:
        w.close()


async def handle(cr, cw):
    try:
        head = await cr.readuntil(b"\r\n\r\n")
    except Exception:
        cw.close(); return
    lines = head.decode("latin1").split("\r\n")
    method, target, _ = lines[0].split(" ", 2)
    headers = {k.lower(): v.strip() for k, v in (l.split(":", 1) for l in lines[1:] if ":" in l)}
    rid = run_id_from(headers)
    if method == "CONNECT":
        host, port = target.rsplit(":", 1)
    else:
        m = re.match(r"https?://([^/:]+)(?::(\d+))?", target)
        if not m:
            cw.write(b"HTTP/1.1 400 Bad Request\r\n\r\n"); cw.close(); return
        host, port = m.group(1), m.group(2) or "80"
    if not allowed(rid, host):
        print(f"DENY run={rid} host={host}", flush=True)
        cw.write(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n"); await cw.drain(); cw.close(); return
    print(f"ALLOW run={rid} host={host}", flush=True)
    try:
        ur, uw = await asyncio.open_connection(host, int(port))
    except Exception:
        cw.write(b"HTTP/1.1 502 Bad Gateway\r\n\r\n"); cw.close(); return
    if method == "CONNECT":
        cw.write(b"HTTP/1.1 200 Connection Established\r\n\r\n"); await cw.drain()
    else:
        kept = [l for l in lines if not l.lower().startswith("proxy-")]
        uw.write(("\r\n".join(kept)).encode("latin1")); await uw.drain()
    await asyncio.gather(pipe(cr, uw), pipe(ur, cw))


async def main():
    server = await asyncio.start_server(handle, "0.0.0.0", 8888)
    print("egress proxy on :8888", flush=True)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
