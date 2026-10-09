# Frankenstein Lab — a factory that builds its own workflows

> AI agents cost money every time they run. This factory pays the model **once** to build a workflow — detecting
> what it can't do yet, writing the missing capabilities, testing them in a sandbox and asking an operator to
> install them — and from then on runs that workflow as plain code at **~0 tokens**.

Topic: **Frankenstein** (Etnetera / prg.ai) — *build an agent that can build itself.*

## How you use it

1. **Create a monster.** Describe a purpose to the scientist ("a bot that checks Czech suppliers before we pay
   them"). The factory builds the bot once — finding gaps, writing and testing capabilities, and installing whatever passes
   its tests. You only approve new network access.
2. **Ask it.** Pick the monster in the chat's bot selector and ask in plain words (a small Claude Haiku call turns the sentence into the bot's
   input; the token count is shown) or fill the auto-generated form (0 tokens). The bot itself runs as compiled
   code. Pay once to build, ask as often as you like.

## What it does

```
Task ──► PLAN ─► DISCOVER ─► GAP? ──► LEARN (probe real APIs in sandbox) ─► WRITE code+tests ─► TEST (sandbox)
                     │                    ▲                                                        │
                     │ reuse              └──── repair (capped) ◄──── fail ◄───────────────────────┤
                     ▼                                                                             ▼ pass
                    RUN ◄── INSTALL ◄── OPERATOR GATE ◄── ACCEPTANCE CHECK ◄── E2E TEST ◄── INSTALL capability
                     │                                          │ fail → replan with feedback (capped)
                     └── every run is logged ──► DISTILL: LLM steps with history are rewritten as code (0 tok/run)
```

* **Gap detection from the task.** A planner maps the task onto the registry. Anything it can't cover becomes a
  gap. Gaps are also detected *after* the fact: an end-to-end failure or a failed acceptance check ("the output
  doesn't contain the registered address") sends the factory back to the bench with that feedback.
* **Learn before writing.** The builder probes the real API / page from inside the sandbox, then writes
  `impl.py` + pytest tests. Missing capabilities are built **on the real output of the previous steps**, so parts fit.
* **Tests before install, visible in the log.** The harness rejects anything with failing tests or fewer than 3 tests.
* **Operator control.** Installs and upgrades happen automatically, but only after their tests pass (enforced in
  `registry.install()`, logged as `auto · tests passed`). Every *widening of authority* (new network host) still needs
  the operator's approval in the chat. Rollback and revoke per artifact. Set `INSTALL_GATE=console` to approve
  installs by hand as well.
* **Capabilities grow, authority doesn't.** Each artifact declares its network hosts. The sandbox's egress proxy
  enforces them. A builder that needs a new host must ask, with a reason (it once found that the planner had
  granted an unofficial look-alike site and asked for the official `ares.gov.cz` instead).
* **Self-repair.** When a running workflow breaks (e.g. an outside agent sends input the code never handled), the
  factory rebuilds only the failing capability, from the input it failed on, and the harness adds a regression test
  generated from that capability's own successful history. v2 installs once its tests pass; if the re-run still fails, the
  factory rolls back automatically.
* **Fresh-session composition.** The registry persists (SQLite + a git repo of artifacts). A new process — or an
  external agent over MCP — gets a different task and composes existing capabilities (incl. `foreach` over lists)
  without rebuilding or manual wiring.
* **Minimal tokens.** Workflows are code. LLM steps exist only where judgment is needed, and once they have
  enough recorded runs the factory distills them into code, proven equivalent on that history by a
  harness-generated test, falling back to the LLM only for inputs the code isn't sure about.
* **The scientist talks.** The lab UI is a Frankenstein laboratory: the scientist (factory) builds the monster
  (workflow) on the bench, the wall shows the registry, a cost gauge shows build cost vs run cost, a knock on the
  door shows external agents arriving via MCP. Voice by ElevenLabs.

## Measured (from the ledger, real runs)

| Workflow | Build (one-time) | Run cost |
|---|---|---|
| ARES company lookup (session 1, empty registry) | $0.18, 10 LLM calls | 0 tokens, ~3 s |
| Check 3 suppliers + flag inactive (session 2, reuses session 1 via `foreach`) | $0.12, 4 LLM calls | 0 tokens, ~4 s |
| Self-repair: external agent sends `CZ27074358`, `6947` → step breaks → only that capability rebuilt (15 tests incl. regression from its own history) → v2 installed → re-run | one rebuild | 0 tokens after repair |
| Categorise 5 invoice lines (needs judgment → LLM step) | $0.06 | v1 LLM: ~4,270 tok → v2 distilled: 1,702 tok on unseen lines (2/5 fell back) → v3 re-distilled from fallbacks: 835 tok |

## Architecture

| Part | File | Notes |
|---|---|---|
| Orchestrator | `factory/orchestrator.py` | plan → gaps → build on real data → e2e → acceptance → gate → install |
| Builder | `factory/builder.py` | probe / submit / request_access loop, repair capped |
| Distiller | `factory/compiler.py` | LLM step → code, equivalence-tested on recorded runs |
| Registry | `factory/registry.py` | versions, status, rollback; artifacts committed to `data/registry/.git` |
| Sandbox | `factory/sandbox.py`, `sandbox/` | Docker in WSL, read-only fs, no caps, internal network + allowlisting egress proxy |
| LLM gateway | `factory/gateway.py`, `factory/llm_elevenlabs.py` | the only holder of the key; caps calls / $ / tokens; ledger reconciled with real usage |
| Gate | `factory/gate.py` | operator approvals (lab UI, CLI) |
| Lab UI | `server.py`, `ui/index.html` | SSE event stream, approvals, registry wall, rollback/revoke, voice |
| MCP gateway | `mcp_server.py` | `search_capabilities`, `describe`, `run`, `request_capability` |

## Hard rules → where they are enforced

| Rule | Enforcement |
|---|---|
| Generated code runs in a sandbox, never on a host with credentials | `sandbox.run()` is the only executor of generated code: Docker (WSL), `--read-only`, `--cap-drop ALL`, no env secrets, egress only via allowlist proxy. The API key lives only in the gateway process. |
| No install without passing tests; test run visible | `registry.install()` raises unless the attached report passed; pytest output is streamed to the lab log. |
| Gap comes from a task | No capability names in code; the registry starts empty (`scripts/reset_registry.py`). |
| Self-iterations and spend capped in code | `config.py`: `MAX_FACTORY_ITERATIONS`, `MAX_REPAIR_ATTEMPTS`, `MAX_REPLANS`, `MAX_USD_PER_TASK`, `MAX_LLM_CALLS_PER_TASK`; per-workflow token budget enforced by `gateway.Budget`. |

## Run it

```bash
# once: Docker inside WSL Ubuntu
wsl -d Ubuntu-24.04 -- bash -lc "sudo apt-get install -y docker.io && sudo usermod -aG docker \$USER"
py -3.12 -m venv .venv && .venv/Scripts/pip install -r requirements.txt
echo ELEVENLABS_API_KEY=... > .env

python scripts/reset_registry.py   # start from an empty registry
python server.py                   # lab UI on http://localhost:8777
python cli.py solve "..."          # or from the terminal
python cli.py optimize             # distill LLM steps that have enough history
```

MCP (Claude Desktop): see the config snippet at the top of `mcp_server.py`.

## Honest status: real, simulated, missing

**Real**
* Everything in the demo is generated live by the model from an empty registry; no capability was written or
  seeded by us. We wrote the *machine* (planner/builder/harness/sandbox/registry/UI); the agent writes the *products*.
* Real public data sources (e.g. the Czech ARES registry), real sandbox network enforcement, real test runs.
* Token and dollar figures come from the provider's billing data for each call (reconciled a few seconds after the call).

**Simulated / caveats**
* The LLM is Claude (Opus 5.5 to build, Haiku 4.5 for runtime LLM steps) **served through ElevenLabs Agents in
  text-only mode** — ElevenLabs has no plain completions endpoint, so each call is a short text conversation. We
  read the raw stream because ElevenLabs' final message is normalised for speech (it strips `*`).
* **Model substitution (found and fixed during the hackathon).** ElevenLabs agents silently cascade to a backup
  model when the primary is slow to produce its first token (default cascade timeout 4 s). Our audit of the ledger
  showed that of the first 70 build-time calls, 33 were served by Claude Opus 5.5, **33 by GPT-4o and 4 by Gemini
  2.5 Flash**. We disabled the backup cascade, set Opus to low reasoning effort (≈5 s per call, under the 15 s limit),
  and the gateway now reconciles every call against the provider's billing and raises a `model substitution` error
  if any other model answered. The demo is re-recorded from an empty registry after this fix.
* Installs are approved by the test gate, not a human (labelled `auto · tests passed`). Development runs also used an
  auto-approve mode for network access, labelled `auto-mode`; in the demo the operator grants access by hand.
* The acceptance check is an LLM judge — it can be wrong.

**Missing / limits**
* Workflows are linear step lists (+ `foreach`), not arbitrary DAGs or branches.
* Distillation is equivalence-tested only on recorded history; unseen inputs fall back to the LLM by design.
* The candidate race ("compete") and adversarial test agent from our design are not implemented.
* Self-repair heals code capabilities only (not LLM steps), one repair attempt per failed run.
* Single-user, single-machine; no auth on the lab UI (binds to 127.0.0.1).
