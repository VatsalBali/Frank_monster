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


_bridge: subprocess.Popen | None = None
_bridge_lock = threading.Lock()


def _via_bridge(args, timeout, input) -> subprocess.CompletedProcess:
    """Send one docker command to the long-lived WSL helper (sandbox/bridge.py), starting it if needed."""
    global _bridge
    with _bridge_lock:
        if _bridge is None or _bridge.poll() is not None:
            _bridge = subprocess.Popen(
                ["wsl", "-d", config.WSL_DISTRO, "--exec", "python3", wsl_path(config.ROOT / "sandbox" / "bridge.py")],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                text=True, encoding="utf-8", errors="replace", bufsize=1)
        _bridge.stdin.write(json.dumps({"args": list(args), "timeout": timeout, "input": input}) + "\n")
        _bridge.stdin.flush()
        line = _bridge.stdout.readline()
    if not line:
        raise OSError("sandbox bridge closed")
    resp = json.loads(line)
    if resp.get("timeout"):
        raise subprocess.TimeoutExpired(["docker", *args], timeout)
    return subprocess.CompletedProcess(["docker", *args], resp["code"], resp["out"], resp["err"])


def docker(*args: str, timeout: float | None = None, input: str | None = None) -> subprocess.CompletedProcess:
    try:
        return _via_bridge(args, timeout, input)
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


def run(files: dict[str, str] | Path, command: list[str], *, net: list[str] | None = None,
        stdin: str | None = None, timeout: int = config.SANDBOX_TIMEOUT_S, label: str = "") -> SandboxResult:
    """files: either a dict of name->content or a directory to copy. command runs in /work."""
    run_id = "r" + uuid.uuid4().hex[:12]
    work = config.RUNS_DIR / run_id
    work.mkdir(parents=True)
    if isinstance(files, dict):
        for name, content in files.items():
            (work / name).write_text(content, encoding="utf-8")
    else:
        for f in Path(files).iterdir():
            if f.is_file():
                shutil.copy(f, work / f.name)
    allow_file = ALLOW_DIR / f"{run_id}.json"
    allow_file.write_text(json.dumps({"net": net or []}))
    proxy = f"http://{run_id}:x@{PROXY}:8888"
    args = ["run", "--rm", "-i", "--name", f"sbx-{run_id}",
            "--network", NET, "--read-only", "--tmpfs", "/tmp:size=64m",
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "--memory", config.SANDBOX_MEMORY, "--pids-limit", "128", "--cpus", "1",
            "-e", f"HTTP_PROXY={proxy}", "-e", f"HTTPS_PROXY={proxy}",
            "-e", f"http_proxy={proxy}", "-e", f"https_proxy={proxy}",
            "-e", "PYTHONDONTWRITEBYTECODE=1", "-e", "HOME=/tmp",
            "-v", f"{wsl_path(work)}:/work", "-w", "/work", IMAGE, *command]
    t0 = time.time()
    timed_out = False
    try:
        r = docker(*args, timeout=timeout + 15, input=stdin)
        code, out, err = r.returncode, r.stdout, r.stderr
    except subprocess.TimeoutExpired:
        docker("rm", "-f", f"sbx-{run_id}")
        code, out, err, timed_out = 124, "", f"timeout after {timeout}s", True
    finally:
        allow_file.unlink(missing_ok=True)
    ms = int((time.time() - t0) * 1000)
    return SandboxResult(code, out, err, ms, run_id, timed_out)
