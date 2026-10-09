# Test it yourself (≈25 minutes)

Everything below is clicked in the lab UI. Keep it open at **http://localhost:8777**.

## 0 · Start (2 min)

1. Docker in WSL must be running: `wsl -d Ubuntu-24.04 -- sudo service docker start`
2. Start the lab: `.venv\Scripts\python server.py` → open http://localhost:8777
3. Header shows **idle** and `builds with claude-sonnet-5-5 … via ElevenLabs`.
4. Click **Voice: off** once → it says "I'm listening" (browsers need a click before audio).
5. **⋯ → Reset lab** → confirm. Check: Work log → Registry says *empty · nothing pre-installed*, the monster is *dormant*.
   Layout: **lab** (left) · **chat** (middle, bot selector on top) · **work log** (right: Log / Code / Registry / Ledger).

## 1 · Build from nothing (≈5 min) — gap detection, learn, test, gate

1. Bot selector shows **New monster**. In the chat box describe a bot, e.g. *a bot that looks up Czech companies in the
   official ARES registry by ICO* → Enter.
2. Watch the scientist's chat message tick through its steps, and the lab: stage `plan` → `learn`. The monster appears with a **dashed** torso band (missing part); the scientist
   says *"I'm missing a capability…"*; coil sparks, lever pumps.
3. If an amber **authority request** appears in the chat (e.g. `ares.gov.cz`): read the reason → **Grant access**.
   This is the only thing you approve. Installs are automatic once tests pass.
   *(Optional: click Reject once on a later run to see the factory stop honestly.)*
4. Work log → **Code** shows the `impl.py` the agent wrote; toggle **tests**. **Log** shows pytest output and
   `installed automatically (tests passed)`.
5. Expect: flash of light, the monster's eyes open and it breathes; the chat says **… is alive** with build cost.
6. **Talk to it →** (or pick it in the bot selector). Ask: *"Who is ICO 27074358 and are they still active?"* → the
   answer appears as a chat reply: ~300 tokens to read the question, **0 tokens** to run. **☰ Form** → Run → 0 + 0.

## 2 · Fresh session, different task (≈3 min) — reuse without rebuilding

1. Create a second monster: *a bot that checks a list of suppliers before payment and flags inactive ones*.
2. Expect: the monster has a **green (ready) band 1** reused from step 1 and only the new part(s) get built.
   Notebook says `reusing: …`. Installs are automatic. Result lists 3 companies + inactive flags. Run = 0 tokens.

## 3 · An external agent (≈2 min) — MCP front door + the knock

In a terminal:
```
.venv\Scripts\python scripts\mcp_smoke.py "check suppliers" <workflow-name-from-the-wall> "{\"icos\": [\"27074358\", \"45274649\"]}"
```
Expect: the **door opens**, a visitor appears, *"Someone's at the door."*; the run returns at **0 tokens**.
*(Claude Desktop works the same — config snippet in mcp_server.py.)*

## 4 · Self-repair (≈3 min) — break it with messy input

1. Work log → **Registry** → click the supplier-check monster tile → in **Run it**, replace the input with
   `{"icos": ["CZ27074358", "6947", "45274649"]}` → **Run**.
2. Expect: run fails at step 1, torso band turns **red-hatched**, *"Step s1 broke… I'll repair just that part."*,
   new code appears, tests include `test_regression.py` (built from the step's own history).
3. The repaired v2 installs automatically once its tests pass → it re-runs and succeeds (`6947` → Ministerstvo financí).
4. Open the capability tile → **Lineage** shows v1 archived, v2 active → **Roll back** works.

## 5 · Minimal tokens (≈6 min) — LLM step → code

1. New monster: *a bot that sorts supplier invoice lines into accounting categories*. Its replies show
   ≈ thousands of tokens to run (LLM step).
2. Ask it twice more with different lines.
3. **⋯ → ⚗ Optimize**: *"I keep paying tokens for …"* → equivalence test vs recorded runs → installs automatically
   when it matches. A **flask** appears on the shelf.
4. Ask it again on new lines → the reply's *tok to run* drops (unfamiliar lines fall back to the LLM, logged as such).

## 6 · Honesty checks (1 min)

* Work log → **Ledger** tab: every row must say **✓ claude**. Any ⚠ row = substitution (the gateway also logs
  a red `model substitution` error).
* Caps line shows the hard limits enforced in code.
* Full automated version of all of the above: `.venv\Scripts\python scripts\rehearse.py` → `data/rehearsal.md`.

## If something goes wrong

| Symptom | Fix |
|---|---|
| "the scientist is busy" | a job is still running — wait for status **idle** |
| Nothing moves after sending | check the terminal running `server.py`; Docker in WSL may be stopped |
| No voice | click Voice again; check ElevenLabs credits |
| `model substitution` error | ElevenLabs agent settings changed — backup LLM must be disabled (see llm_elevenlabs.py) |
