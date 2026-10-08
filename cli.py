"""Command line entry point.

  python cli.py solve "task text" [--auto]   # run the factory on a task
  python cli.py registry                      # show the registry (do this before a demo run)
  python cli.py optimize [--auto]             # distill token-burning LLM steps into code
  python cli.py rollback <name>
  python cli.py ledger
"""
import json
import sys

from factory import gate, gateway, registry, sandbox
from factory.orchestrator import solve


def main(argv: list[str]) -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    if not argv:
        print(__doc__); return
    cmd, *rest = argv
    if cmd == "solve":
        if "--auto" in rest:
            gate.MODE = "auto"; rest.remove("--auto")
        sandbox.ensure_infra()
        out = solve(" ".join(rest))
        print(json.dumps(out, indent=2, ensure_ascii=False, default=str))
    elif cmd == "registry":
        for a in registry.list_artifacts(status=None):
            print(f"{a['status']:9} {a['kind']:10} {a['name']} v{a['version']}  runs={a['runs']} tok={a['tokens']}  {a['description'][:70]}")
        if not registry.list_artifacts(status=None):
            print("(registry is empty)")
    elif cmd == "rollback":
        print("now active: v", registry.rollback(rest[0]))
    elif cmd == "optimize":
        if "--auto" in rest:
            gate.MODE = "auto"
        sandbox.ensure_infra()
        from factory.compiler import optimize_all
        print("distilled:", optimize_all())
    elif cmd == "ledger":
        print(json.dumps(gateway.ledger_summary(), indent=2))
    else:
        print(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
