# Test it yourself (≈25 minutes)

Everything below is clicked in the lab UI. Keep it open at **http://localhost:8777**.

## 0 · Start (2 min)

1. Docker in WSL must be running: `wsl -d Ubuntu-24.04 -- sudo service docker start`
2. Start the lab: `.venv\Scripts\python server.py` → open http://localhost:8777
3. Header shows **idle** and `builds with claude-opus-5-5 … via ElevenLabs`.
4. Click **Voice: off** once → it says "I'm listening" (browsers need a click before audio).
5. Click **Reset lab** → confirm. Check: Registry wall says *empty · nothing pre-installed*, monster is *dormant*,
   ledger panel is empty.

## 1 · Build from nothing (≈5 min) — gap detection, learn, test, gate

1. Click the **Company lookup** chip → **Bring it to life ⚡**.
2. Watch: stage `plan` → `learn`. The monster appears with a **dashed** torso band (missing part); the scientist
   says *"I'm missing a capability…"*; coil sparks, lever pumps.
3. If an amber **authority request** appears (e.g. `ares.gov.cz`): read the reason → **Grant access**.
   *(Optional: click Reject once on a later run to see the factory stop honestly.)*
4. **Code on the bench** shows the `impl.py` the agent wrote; toggle **tests**. The notebook shows pytest output.
5. **Install request** → read tests/network → **It's alive · install**. Then a second gate for the workflow.
6. Expect: flash of light, monster's eyes open and it breathes; result shows name *Asseco Central Europe, a.s.*,
   address, active = true. **Last run · 0 tokens**. A new tile on the wall.

## 2 · Fresh session, different task (≈3 min) — reuse without rebuilding

1. Click **Supplier check** chip → **Bring it to life**.
2. Expect: the monster has a **green (ready) band 1** reused from step 1 and only the new part(s) get built.
   Notebook says `reusing: …`. Approve installs. Result lists 3 companies + inactive flags. Run = 0 tokens.

## 3 · An external agent (≈2 min) — MCP front door + the knock

In a terminal:
```
.venv\Scripts\python scripts\mcp_smoke.py "check suppliers" <workflow-name-from-the-wall> "{\"icos\": [\"27074358\", \"45274649\"]}"
```
Expect: the **door opens**, a visitor appears, *"Someone's at the door."*; the run returns at **0 tokens**.
*(Claude Desktop works the same — config snippet in mcp_server.py.)*

## 4 · Self-repair (≈3 min) — break it with messy input

1. Click the supplier-check **workflow tile** → in **Run it**, replace the input with
   `{"icos": ["CZ27074358", "6947", "45274649"]}` → **Run**.
2. Expect: run fails at step 1, torso band turns **red-hatched**, *"Step s1 broke… I'll repair just that part."*,
   new code appears, tests include `test_regression.py` (built from the step's own history).
3. Approve **heal … v1 → v2** → it re-runs and succeeds (`6947` → Ministerstvo financí).
4. Open the capability tile → **Lineage** shows v1 archived, v2 active → **Roll back** works.

## 5 · Minimal tokens (≈6 min) — LLM step → code

1. Click **Categorise invoices** → build + approve. Note **Last run** ≈ thousands of tokens (LLM step).
2. Run it twice more with different lines (tile → Run it → edit the `lines`).
3. Click **⚗ Optimize**: *"I keep paying tokens for …"* → equivalence test vs recorded runs → approve
   **replace LLM step … with code**. A **flask** appears on the shelf.
4. Run it again on new lines → tokens drop (unfamiliar lines fall back to the LLM, logged as such).
   The bar chart under *Last run* shows the drop.

## 6 · Honesty checks (1 min)

* **Ledger & model audit** panel: every row must say **✓ claude**. Any ⚠ row = substitution (the gateway also logs
  a red `model substitution` error).
* Caps line shows the hard limits enforced in code.
* Full automated version of all of the above: `.venv\Scripts\python scripts\rehearse.py` → `data/rehearsal.md`.

## If something goes wrong

| Symptom | Fix |
|---|---|
| "the scientist is busy" | a job is still running — wait for status **idle** |
| Gate never appears, nothing moves | check the terminal running `server.py`; Docker in WSL may be stopped |
| No voice | click Voice again; check ElevenLabs credits |
| `model substitution` error | ElevenLabs agent settings changed — backup LLM must be disabled (see llm_elevenlabs.py) |
