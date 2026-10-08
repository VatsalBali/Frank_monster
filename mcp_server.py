"""Factory MCP gateway — the one front door for external agents (Claude Desktop, Cursor, ...).

A handful of meta-tools instead of one MCP tool per capability, so a client's context cost stays flat
however large the registry grows. Execution always happens in the sandbox; installs still need the operator.

Claude Desktop config (claude_desktop_config.json):
  "frankenstein-factory": {"command": "D:\\Dusktilldawn\\.venv\\Scripts\\python.exe",
                           "args": ["D:\\Dusktilldawn\\mcp_server.py"]}
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
os.environ.setdefault("GATE_MODE", "console")

from mcp.server.mcpserver import MCPServer  # noqa: E402

from factory import registry, sandbox  # noqa: E402
from factory.events import emit  # noqa: E402

mcp = MCPServer("frankenstein-factory")


def _knock(action: str, detail: str) -> None:
    emit("knock", who="external agent (MCP)", say="Someone's at the door.", msg=f"MCP {action}: {detail}")


@mcp.tool()
def search_capabilities(query: str) -> str:
    """Search the factory's registry of agent-built workflows and capabilities. Returns matching names,
    signatures and descriptions. Use this before asking the factory to build something new."""
    _knock("search", query)
    words = [w for w in query.lower().replace("_", " ").split() if len(w) > 2]
    hits = []
    for a in registry.list_artifacts():
        hay = (a["name"].replace("_", " ") + " " + a["description"]).lower()
        score = sum(w in hay for w in words)
        if score:
            hits.append((score, a))
    hits.sort(key=lambda x: (-x[0], x[1]["kind"] != "workflow"))
    if not hits:
        return "No match. Use request_capability to have the factory build it."
    return "\n".join(f"- {a['name']} v{a['version']} [{a['kind']}] in={a['manifest']['signature'].get('in')} :: "
                     f"{a['description']}" for _, a in hits[:8])


@mcp.tool()
def describe(name: str) -> str:
    """Show a workflow or capability: input schema, permissions, token budget, steps, run stats, example input."""
    a = registry.get(name)
    if not a:
        return f"{name} is not installed."
    m = a["manifest"]
    return json.dumps({"name": name, "version": a["version"], "kind": a["kind"], "description": a["description"],
                       "input": m["signature"].get("in_schema") or m["signature"].get("in"),
                       "example_input": m.get("example_input"), "permissions": m.get("permissions"),
                       "budget": m.get("budget"), "steps": [s["uses"] for s in m.get("steps", [])],
                       "runs": a["runs"], "avg_tokens_per_run": round(a["tokens"] / a["runs"]) if a["runs"] else 0},
                      indent=2)


@mcp.tool()
def run(name: str, input_json: str) -> str:
    """Run an installed workflow or capability with a JSON input. Runs in the factory sandbox."""
    from factory import gateway
    from factory.executor import run_capability, run_workflow
    _knock("run", name)
    a = registry.get(name)
    if not a:
        return f"{name} is not installed. Use search_capabilities or request_capability."
    sandbox.ensure_infra()
    inp = json.loads(input_json)
    if a["kind"] == "workflow":
        from factory import config
        from factory.orchestrator import run_with_heal
        m = a["manifest"]
        out = run_with_heal(a, inp,
                            gateway.Budget(scope=f"run:{name}", max_usd=0.5,
                                           max_tokens=m.get("budget", {}).get("max_tokens_per_run") or None),
                            gateway.Budget(scope=f"task:heal {name}", max_usd=config.MAX_USD_PER_TASK))
    else:
        out, _ = run_capability(a, inp, gateway.Budget(scope=f"run:{name}", max_usd=0.2))
    emit("result", workflow=name, result=out, input=inp, via="external agent (MCP)", msg=f"MCP ran {name}")
    return json.dumps(out, ensure_ascii=False, default=str)


@mcp.tool()
def request_capability(task: str) -> str:
    """Ask the factory to solve a task it may not be able to do yet. It reuses what exists, builds and tests what
    is missing (operator approves installs in the lab), installs a reusable workflow, runs it and returns the result.
    Slow the first time (minutes); later calls of the same kind are fast and nearly free."""
    from factory.orchestrator import solve
    _knock("request", task[:120])
    sandbox.ensure_infra()
    out = solve(task)
    return json.dumps(out, ensure_ascii=False, default=str)


if __name__ == "__main__":
    mcp.run()
