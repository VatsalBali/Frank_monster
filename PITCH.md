# Pitch and video

## The one message

**It learns a skill once, proves it with tests, and from then on does the job for free. Its skills keep growing.
Its permissions never do.**

Every other agent pays a language model every time it works and gets no better. Ours pays once, about 30 cents, to
write and test a tool. After that the tool runs as plain code: 0 tokens, under 2 seconds. And when a new problem
arrives in a new session, it reuses what it already built.

## 60-second pitch (on stage)

> **[0–10 s] The problem.** Every small company has a person who spends days matching invoices to orders to bank
> payments. AI agents can do it, but they think from scratch every time, cost money every time, and nobody
> can tell what they're allowed to touch.
>
> **[10–35 s] What we built.** Frankenstein Lab is an agent that builds its own tools. We gave it a month of a
> company's paperwork it had never seen. It found it had no tools for this, wrote two, tested them in a sandbox, and
> found **all 5 problems an accountant would find, including 16,552 crowns paid twice**. Building that cost **26 cents
> once**. Every answer after that costs **zero tokens**.
> Next day, in a fresh session, we asked a different question, a cash-flow forecast. It **found yesterday's tools on
> its own and reused them**. Nobody wired anything.
>
> **[35–55 s] Why you can trust it.** Its skills grow; its authority doesn't. We attacked our own bot: it tried to email
> out, reach new sites, approve its own install, read our keys. **All 9 attempts blocked.** We asked it for an
> upgrade that would break another bot: **refused, with proof**. And you can just talk to it. Every monster
> answers in its own ElevenLabs voice.
>
> **[55–60 s] Close.** Pay once to learn. Free forever after. Never more power than you gave it.

Lines to keep if time runs short: *"26 cents once, zero tokens after"* and *"its skills grow; its authority doesn't."*

## 90-second video (submission: explainer for the jury)

| Time | Screen | Voice-over |
|---|---|---|
| 0–8 s | Empty lab. Header: `0 tested skills`. | "The registry is empty. No tools for invoices, banks or finance. Everything you'll see is written live." |
| 8–30 s | Attach the sample company, ask to reconcile. Quick answer in ~2 s; work log: gaps found → probe files → write code + tests → sandbox run → judge rejects v1 → v2 passes. (4× speed, labelled.) | "It finds what it can't do, writes the missing capabilities, tests them in a Docker sandbox and installs only what passes. An independent judge sent the first version back for thin evidence." |
| 30–42 s | Answer headline: *12 invoices, 5 problems*. Answer key opened beside it. | "Five of five planted problems, each pointing at the file, the order line and the bank row. Built for 26 cents; it now runs as code: 0 tokens, 0.6 seconds." |
| 42–58 s | **↻ Fresh session**. Ask for next month's cash flow. Log: `reusing: parse_supplier_invoices, reconcile_invoices_orders_bank`. Family tree: shared parts glow. Header: `♻ shared`. | "New session, no chat memory. A different task. It discovers its earlier capabilities in the registry and composes them with one new part, with no rebuilding and no manual wiring." |
| 58–72 s | **🛡 Authority test**: 9 red rows, all blocked. Then **Propose upgrade** → **⛔ refused** with old/new diff. | "Its capabilities grow; its authority doesn't. Hostile code with the bot's own permissions: every escape blocked. A change that would break a dependent bot is refused by a regression gate." |
| 72–84 s | Click 🎙, ask by voice: "Were any invoices paid twice?" The monster answers aloud. | "Voice in with ElevenLabs Scribe, voice out with a voice designed for each monster. The spoken headline is written by code: zero tokens." |
| 84–90 s | 📈 Value card. | "Measured cost, time and success per answer. Estimates are labelled as estimates." |

## How it meets the brief

| Brief | Where it happens |
|---|---|
| A task exposes a missing capability | Planner lists `gaps`; the log shows `N gaps: …` |
| The agent creates, tests and registers it, then completes the task | Builder probes the real files in the sandbox, writes `impl.py` + ≥3 tests, installs only on pass; the workflow runs and a judge checks the answer |
| Builds tooling for discovering and managing capabilities | Versioned registry with tests, I/O history, know-how and lineage; family tree, registry drawer, value card, kill, upgrade proposals, regression gate, distillation of LLM steps into code; also exposed over MCP |
| Fresh session, a different task combines earlier capabilities without rebuilding or manual wiring | ↻ Fresh session clears the chat; the cash-flow task reuses session-1 parts (log: `reusing: …`, tree: *earlier session*) |
| Capabilities may grow; authority may not | Sandbox with per-capability network allowlist; new hosts and paid agents need the operator; spend approvals never automatic; 🛡 authority test; ⛔ regression gate |

## Against the judging criteria

| Criterion (weight) | Our evidence |
|---|---|
| Value (35 %) | A real back-office job (invoice ↔ order ↔ bank reconciliation, cash-flow exposure) on realistic Czech paperwork; 5/5 planted problems found; checkable against a published answer key; value card with measured cost/time |
| Originality (25 %) | Pays the LLM once, then the skill is plain tested code (0 tokens); skills compound across sessions; authority fixed while skills grow; LLM steps distilled into code over time; can hire paid Sokosumi agents only behind a spend gate |
| End-to-end (20 %) | Upload files or a voice question → build → test → install → answer with evidence → spoken reply |
| Technical (10 %) | Docker sandbox in WSL with egress proxy; tests gate every install; regression replay of recorded inputs; self-repair of a broken step; cost ledger reconciled with provider billing |
| Validation and honesty (10 %) | Answer key the agent never sees; README "Honest status: real, simulated, missing"; estimates dashed and labelled; the ElevenLabs model substitution we found and fixed is documented |

## ElevenLabs (separate prize)

* **The brain runs through ElevenLabs Agents**: every Claude call (plan, build, judge, runtime) is an ElevenLabs
  conversation, with cost reconciled from ElevenLabs billing.
* **Voice in**: 🎙 records the question, **ElevenLabs Scribe** transcribes it (≈1 s), and it goes the same way as typed text.
* **Voice out**: **Voice Design** creates five monster archetypes (Brute, Gremlin, Ghoul, Golem, Witch), one is
  picked for each bot when it is built; answers are spoken with **Flash v2.5** TTS. The spoken line is the bot's
  headline, filled in by code, so speaking costs 0 LLM tokens. The scientist speaks the quick first answer.
