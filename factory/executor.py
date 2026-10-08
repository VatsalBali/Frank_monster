"""Runs capabilities and workflows. Code runs in the sandbox; LLM steps run through the metered gateway."""
import json
import time
from pathlib import Path

from . import config, gateway, registry, sandbox
from .events import emit

RUNNER = """import json, sys, impl
data = json.load(sys.stdin)
out = [impl.run(x) for x in data["batch"]] if isinstance(data, dict) and "batch" in data else impl.run(data)
sys.stdout.write("\\n__RESULT__" + json.dumps(out, default=str))
"""


class StepFailed(RuntimeError):
    def __init__(self, step: str, capability: str, detail: str):
        super().__init__(f"step {step} ({capability}) failed: {detail}")
        self.step, self.capability, self.detail = step, capability, detail


FALLBACK = {"__fallback__": True}


def _log_io(cap: dict, inp, out, via: str = "") -> None:
    """Per-capability I/O history: the raw material for distillation and regression tests.
    via='fallback' marks inputs the distilled code deferred to the LLM: the next distillation learns them."""
    row = {"input": inp, "output": out, **({"via": via} if via else {})}
    with Path(cap["path"], "io.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")


def _sandbox_call(cap: dict, payload, timeout: int = config.SANDBOX_TIMEOUT_S):
    files = {"impl.py": Path(cap["path"], "impl.py").read_text(encoding="utf-8"), "_runner.py": RUNNER}
    r = sandbox.run(files, ["python", "_runner.py"], net=cap["manifest"].get("permissions", {}).get("net", []),
                    stdin=json.dumps(payload), label=cap["name"], timeout=timeout)
    if not r.ok or "__RESULT__" not in r.stdout:
        raise RuntimeError((r.stderr or r.stdout)[-1500:])
    return json.loads(r.stdout.rsplit("__RESULT__", 1)[1])


def _fallback(cap: dict, inp: dict, budget: gateway.Budget) -> tuple[dict, int]:
    fb = cap["manifest"].get("fallback")
    if not fb:
        raise RuntimeError(f"{cap['name']} deferred but has no fallback")
    llm_cap = registry.get(fb["name"], fb["version"])
    emit("log", msg=f"    {cap['name']}: unseen input → LLM fallback v{fb['version']}")
    return _run_llm(llm_cap, inp, budget)


def run_capability(cap: dict, inp: dict, budget: gateway.Budget) -> tuple[dict, int]:
    """Returns (output, tokens_used)."""
    if cap["manifest"].get("impl") == "llm":
        out, tok = _run_llm(cap, inp, budget)
    else:
        out, tok = _sandbox_call(cap, inp), 0
        if out == FALLBACK:
            out, tok = _fallback(cap, inp, budget)
            _log_io(cap, inp, out, "fallback")
            return out, tok
    _log_io(cap, inp, out)
    return out, tok


def run_capability_batch(cap: dict, inputs: list[dict], budget: gateway.Budget) -> list:
    """foreach: code steps run all items in ONE sandbox container; LLM steps run per item."""
    if cap["manifest"].get("impl") == "llm":
        return [run_capability(cap, x, budget)[0] for x in inputs]
    outs = _sandbox_call(cap, {"batch": inputs}, timeout=120)
    for i, (x, o) in enumerate(zip(inputs, outs)):
        if o == FALLBACK:
            outs[i], _ = _fallback(cap, x, budget)
        _log_io(cap, x, outs[i], "fallback" if o == FALLBACK else "")
    return outs


def _run_llm(cap: dict, inp: dict, budget: gateway.Budget) -> tuple[dict, int]:
    m = cap["manifest"]
    prompt = Path(cap["path"], "prompt.txt").read_text(encoding="utf-8")
    before = budget.tokens
    out = gateway.complete_json(budget, f"llm-step:{cap['name']}", model=config.RUNTIME_MODEL,
                                system=prompt + "\nOutput JSON schema: " + m["signature"]["out_schema"],
                                prompt=json.dumps(inp, ensure_ascii=False))
    return out, budget.tokens - before


def resolve(source: str, wf_input: dict, outputs: dict, item=None):
    if source.startswith("$item"):
        return _dig(item, source[len("$item"):])
    if source.startswith("$input"):
        return _dig(wf_input, source[len("$input"):])
    if source.startswith("$steps."):
        rest = source[len("$steps."):]
        sid, _, path = rest.partition(".")
        return _dig(outputs[sid], "." + path if path else "")
    try:
        return json.loads(source)
    except json.JSONDecodeError:
        return source


def _dig(obj, path: str):
    for part in [p for p in path.split(".") if p]:
        obj = obj[int(part)] if isinstance(obj, list) else obj[part]
    return obj


def run_workflow(wf: dict, wf_input: dict, *, budget: gateway.Budget | None = None) -> dict:
    """Execute a workflow DAG (steps listed in topological order)."""
    m = wf["manifest"]
    max_tokens = m.get("budget", {}).get("max_tokens_per_run")
    budget = budget or gateway.Budget(scope=f"run:{wf['name']}", max_usd=0.50, max_tokens=max_tokens)
    emit("stage", stage="run", workflow=wf["name"], msg=f"running {wf['name']} v{wf['version']}")
    outputs: dict = {}
    t0 = time.time()
    tokens0 = budget.tokens
    try:
        for step in m["steps"]:
            cap = registry.get(step["uses"], step.get("version"))
            if not cap:
                raise StepFailed(step["id"], step["uses"], "capability not installed")
            items = resolve(step["foreach"], wf_input, outputs) if step.get("foreach") else None
            emit("log", msg=f"  {step['id']}: {step['uses']} v{cap['version']}" + (f" × {len(items)}" if items is not None else ""))
            ts = time.time()
            try:
                if items is not None:
                    before = budget.tokens
                    batch = [{b["param"]: resolve(b["source"], wf_input, outputs, it) for b in step["inputs"]} for it in items]
                    out, tok = {"items": run_capability_batch(cap, batch, budget)}, budget.tokens - before
                else:
                    inp = {b["param"]: resolve(b["source"], wf_input, outputs) for b in step["inputs"]}
                    out, tok = run_capability(cap, inp, budget)
            except Exception as e:
                registry.record_run(cap["name"], cap["version"], False, 0, int((time.time() - ts) * 1000))
                raise StepFailed(step["id"], step["uses"], str(e)) from e
            registry.record_run(cap["name"], cap["version"], True, tok, int((time.time() - ts) * 1000))
            outputs[step["id"]] = out
        result = outputs[m["output_step"]]
        ok = True
        return result
    except Exception:
        ok = False
        raise
    finally:
        ms = int((time.time() - t0) * 1000)
        used = budget.tokens - tokens0
        registry.record_run(wf["name"], wf["version"], ok, used, ms)
        emit("run", workflow=wf["name"], version=wf["version"], ok=ok, tokens=used, ms=ms,
             msg=f"{wf['name']} {'ok' if ok else 'FAILED'} · {used} tok · {ms} ms")
