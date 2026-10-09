"""Run agent-generated code inside Docker (in WSL). Never on the Windows host, never with credentials.

Each run gets: a fresh copy of the files at /work, read-only root fs, no capabilities, memory/pid limits,
and a network that can only reach the allowlisting egress proxy — which only lets through the hosts the
artifact declared in its manifest.
"""
import json
import shutil
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from . import config
from .events import emit

IMAGE = "factory-sandbox:1"
NET = "factory_internal"
PROXY = "factory-egress"
ALLOW_DIR = config.DATA / "allow"
ALLOW_DIR.mkdir(exist_ok=True)


def wsl_path(p: Path) -> str:
    p = Path(p).resolve()
    drive = p.drive.rstrip(":").lower()
    rest = p.as_posix().split(":", 1)[1]
    return f"/mnt/{drive}{rest}"


# WSL appends the whole Windows PATH, and the docker CLI scans it (over /mnt/c) on every call: ~1.7 s per command.
# A clean Linux PATH, and --exec instead of a login shell, bring a sandbox start down to well under a second.
_LINUX_PATH = "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"


_bridges: dict[str, subprocess.Popen] = {}
_bridge_locks = {"main": threading.Lock(), "bg": threading.Lock()}


def _via_bridge(args, timeout, input, lane: str = "main") -> subprocess.CompletedProcess:
    """Send one docker command to a long-lived WSL helper (sandbox/bridge.py), starting it if needed.
    Two lanes, so background work (preparing spare containers) never delays a foreground run."""
    with _bridge_locks[lane]:
        b = _bridges.get(lane)
        if b is None or b.poll() is not None:
            b = _bridges[lane] = subprocess.Popen(
                ["wsl", "-d", config.WSL_DISTRO, "--exec", "python3", wsl_path(config.ROOT / "sandbox" / "bridge.py")],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                text=True, encoding="utf-8", errors="replace", bufsize=1)
        b.stdin.write(json.dumps({"args": list(args), "timeout": timeout, "input": input}) + "\n")
        b.stdin.flush()
        line = b.stdout.readline()
    if not line:
        raise OSError("sandbox bridge closed")
    resp = json.loads(line)
    if resp.get("timeout"):
        raise subprocess.TimeoutExpired(["docker", *args], timeout)
    return subprocess.CompletedProcess(["docker", *args], resp["code"], resp["out"], resp["err"])


def docker(*args: str, timeout: float | None = None, input: str | None = None, lane: str = "main") -> subprocess.CompletedProcess:
    try:
        return _via_bridge(args, timeout, input, lane)
    except (OSError, ValueError):
        pass  # bridge unavailable: fall back to one wsl.exe call per command
    return subprocess.run(["wsl", "-d", config.WSL_DISTRO, "--exec", "env", _LINUX_PATH, "docker", *args],
                          capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=timeout, input=input)


def ensure_infra() -> None:
    """Build sandbox image, internal network and egress proxy if missing. Idempotent."""
    root = config.ROOT / "sandbox"
    if docker("image", "inspect", IMAGE).returncode != 0:
        emit("log", msg="building sandbox image (one-time)")
        r = docker("build", "-t", IMAGE, wsl_path(root), timeout=900)
        if r.returncode != 0:
            raise RuntimeError("sandbox image build failed:\n" + r.stderr[-2000:])
    if docker("network", "inspect", NET).returncode != 0:
        docker("network", "create", "--internal", NET)
    st = docker("inspect", "-f", "{{.State.Running}}", PROXY)
    if st.stdout.strip() != "true":
        docker("rm", "-f", PROXY)
        r = docker("run", "-d", "--name", PROXY, "--restart", "unless-stopped",
                   "-v", f"{wsl_path(root / 'egress_proxy.py')}:/proxy.py:ro",
                   "-v", f"{wsl_path(ALLOW_DIR)}:/allow:ro",
                   "python:3.12-slim", "python", "-u", "/proxy.py")
        if r.returncode != 0:
            raise RuntimeError("egress proxy failed: " + r.stderr)
        docker("network", "connect", NET, PROXY)


@dataclass
class SandboxResult:
    exit_code: int
    stdout: str
    stderr: str
    ms: int
    run_id: str
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out


# Runs inside the container: unpack the files into tmpfs, then run the command there with the real stdin payload.
_BOOT = ("import json,os,subprocess,sys\n"
         "e=json.load(sys.stdin)\n"
         "os.makedirs('/tmp/w',exist_ok=True)\n"
         "for n,c in e['files'].items():\n"
         "    open(os.path.join('/tmp/w',os.path.basename(n)),'w',encoding='utf-8').write(c)\n"
         "os.chdir('/tmp/w')\n"
         "r=subprocess.run(e['cmd'],input=e.get('stdin'),text=True,env={**os.environ,'PYTHONPATH':'/tmp/w'})\n"
         "sys.exit(r.returncode)\n")


def _container_args(run_id: str) -> list[str]:
    proxy = f"http://{run_id}:x@{PROXY}:8888"
    return ["--name", f"sbx-{run_id}", "--network", NET, "--read-only", "--tmpfs", "/tmp:size=64m",
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "--memory", config.SANDBOX_MEMORY, "--pids-limit", "128", "--cpus", "1",
            "-e", f"HTTP_PROXY={proxy}", "-e", f"HTTPS_PROXY={proxy}",
            "-e", f"http_proxy={proxy}", "-e", f"https_proxy={proxy}",
            "-e", "PYTHONDONTWRITEBYTECODE=1", "-e", "HOME=/tmp", "-w", "/tmp"]


# Spare containers: started ahead of time with the same isolation, each used for exactly ONE run and then
# removed. Creating a container costs ~0.7-1.5 s; executing in a ready one ~0.2 s. They expire on their own.
SPARES = 2
_spares: list[str] = []
_spares_lock = threading.Lock()
_spawning = threading.Event()


def _top_up() -> None:
    if _spawning.is_set():
        return
    _spawning.set()

    def work():
        try:
            while True:
                with _spares_lock:
                    if len(_spares) >= SPARES:
                        return
                rid = "r" + uuid.uuid4().hex[:12]
                r = docker("run", "-d", "--rm", *_container_args(rid), IMAGE, "sleep", "900", timeout=60, lane="bg")
                if r.returncode != 0:
                    return
                with _spares_lock:
                    _spares.append(rid)
        except Exception:
            pass
        finally:
            _spawning.clear()
    threading.Thread(target=work, daemon=True).start()


def _discard(run_id: str) -> None:
    threading.Thread(target=lambda: docker("rm", "-f", f"sbx-{run_id}", timeout=30, lane="bg"), daemon=True).start()


def run(files: dict[str, str] | Path, command: list[str], *, net: list[str] | None = None,
        stdin: str | None = None, timeout: int = config.SANDBOX_TIMEOUT_S, label: str = "") -> SandboxResult:
    """files: either a dict of name->content or a directory to copy. command runs in a fresh in-memory /tmp/w of a
    container nobody used before. The files travel over stdin (no bind mount of the Windows disk)."""
    if not isinstance(files, dict):
        files = {f.name: f.read_text(encoding="utf-8", errors="replace") for f in Path(files).iterdir() if f.is_file()}
    envelope = json.dumps({"files": files, "cmd": command, "stdin": stdin})
    with _spares_lock:
        spare = _spares.pop(0) if _spares else None
    _top_up()
    run_id = spare or "r" + uuid.uuid4().hex[:12]
    allow_file = ALLOW_DIR / f"{run_id}.json"
    allow_file.write_text(json.dumps({"net": net or []}))
    t0 = time.time()
    timed_out = False
    try:
        if spare:
            r = docker("exec", "-i", f"sbx-{run_id}", "python", "-c", _BOOT, timeout=timeout + 5, input=envelope)
            if r.returncode in (125, 126, 127, 137) and "No such container" in r.stderr + r.stdout:
                spare = None  # it expired: start one the normal way
        if not spare:
            r = docker("run", "--rm", "-i", *_container_args(run_id), IMAGE, "python", "-c", _BOOT,
                       timeout=timeout + 5, input=envelope)
        code, out, err = r.returncode, r.stdout, r.stderr
    except subprocess.TimeoutExpired:
        docker("rm", "-f", f"sbx-{run_id}")
        code, out, err, timed_out = 124, "", f"timeout after {timeout}s", True
    finally:
        allow_file.unlink(missing_ok=True)
        if spare:
            _discard(run_id)
    ms = int((time.time() - t0) * 1000)
    return SandboxResult(code, out, err, ms, run_id, timed_out)
