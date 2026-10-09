"""Long-lived helper inside WSL: reads one JSON request per line, runs `docker <args>`, answers with one JSON line.
Saves starting wsl.exe for every sandbox command (~1 s each). It only ever runs the docker CLI."""
import json
import os
import subprocess
import sys

ENV = {"PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin", "HOME": os.environ.get("HOME", "/root")}

for line in sys.stdin:
    req = json.loads(line)
    try:
        r = subprocess.run(["docker", *req["args"]], input=req.get("input"), capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=req.get("timeout"), env=ENV)
        resp = {"code": r.returncode, "out": r.stdout, "err": r.stderr}
    except subprocess.TimeoutExpired:
        resp = {"timeout": True}
    except Exception as e:
        resp = {"code": 125, "out": "", "err": f"bridge: {e}"}
    sys.stdout.write(json.dumps(resp) + "\n")
    sys.stdout.flush()
