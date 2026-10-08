# 90-second demo script

Record the screen at 1440×900, lab UI full screen, voice on. Speed up waiting (label it "2× / 8×" on screen);
never cut failures. Start with `python scripts/reset_registry.py` so the wall is visibly empty.

| Time | Screen | Voice-over (you) / scientist (ElevenLabs) |
|---|---|---|
| 0–8s | Empty registry wall ("empty: nothing pre-installed"), cost gauge at $0 | **You:** "AI agents cost money every single time they run. Our factory pays the model once — and then never again." |
| 8–30s | Type task 1 (ARES supplier check). Monster appears on bench with a dashed (missing) part. Log shows probes. | **Scientist:** "I'm missing a capability… Let me build it." Then: "I need access to ares.gov.cz. Requesting permission." → click **Grant access** |
| 30–42s | Test log: a failing test (red), "Fixing it", then green "7 passed". Gate: **It's alive · install** → click | **Scientist:** "Tests failed. Fixing it." … "It's alive! May I install it?" |
| 42–50s | Acceptance PASSED, tile appears on wall (green). Gauge: build $0.18 · last run **0 tokens** | **Scientist:** "From now on, this job costs zero tokens." |
| 50–68s | Fresh session: Claude Desktop (or `scripts/mcp_smoke.py`) asks for "check our 3 suppliers before payment". Door **knocks**. Bench shows reused green part ×3 + one new part. | **Scientist:** "Someone's at the door." **You:** "A different agent, a different task — it reuses what was built and only builds what's missing." |
| 64–74s | Same external agent sends messy IDs (`CZ27074358`, `6947`). Torso band turns red-hatched, "Step s1 broke… I'll repair just that part." Code panel shows the new impl; 15 tests incl. regression; gate → v2; re-run passes | **You:** "When something breaks, it repairs only the broken part — and proves it didn't break what already worked." |
| 74–82s | `optimize`: "I keep paying tokens for classify… Let me turn it into plain code." Equivalence test vs recorded runs passes → flask on the shelf, tok/run drops to 0 | **You:** "LLM steps that keep costing money get distilled into code, proven equivalent on their own history." |
| 82–90s | Tile drawer: lineage v1→v2, permissions, Roll back button. Registry wall full. | **You:** "Capabilities grow. Authority doesn't. Every install is tested, gated and reversible." |

Honesty line for the submission text: the registry starts empty; every capability in the video was written live by
the model; LLM calls go through ElevenLabs Agents (Claude Opus 5.5); dev runs used auto-approve, the video uses
real approvals.
