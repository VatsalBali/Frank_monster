"""Act as an external agent: connect to the factory over MCP (stdio), search, describe and run a workflow."""
import asyncio
import json
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parent.parent
PY = str(ROOT / ".venv" / "Scripts" / "python.exe")


async def main(query: str, run_name: str | None, run_input: str | None):
    params = StdioServerParameters(command=PY, args=[str(ROOT / "mcp_server.py")])
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            tools = await s.list_tools()
            print("tools:", [t.name for t in tools.tools])
            res = await s.call_tool("search_capabilities", {"query": query})
            print("search:", res.content[0].text)
            if run_name:
                res = await s.call_tool("describe", {"name": run_name})
                print("describe:", res.content[0].text[:600])
                res = await s.call_tool("run", {"name": run_name, "input_json": run_input or "{}"})
                print("run:", res.content[0].text[:1500])


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None, sys.argv[3] if len(sys.argv) > 3 else None))
