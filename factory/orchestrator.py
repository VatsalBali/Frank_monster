"""The factory loop:
PLAN → DISCOVER → GAP? → BUILD (learn+test) → GATE → INSTALL → ASSEMBLE → E2E TEST → ACCEPT? → GATE → INSTALL → RUN.
A failed acceptance check is itself a detected gap: the factory replans with the judge's feedback (capped)."""
import json
import threading
import time
from pathlib import Path

from pydantic import BaseModel, Field

from . import config, gate, gateway, registry
from .builder import build_capability
from .events import emit
from .executor import StepFailed, resolve, run_capability, run_capability_batch, run_workflow
from .models import GapSpec, Plan

PLANNER = """You are the planner of a workflow factory. A workflow is a pipeline of reusable capabilities that runs
cheaply and deterministically after it is built. Given a task and the registry, decide:
1. If an existing workflow already solves this kind of task, set reuse_workflow and give its input.
2. Otherwise design a workflow (steps in execution order). Reuse existing capabilities wherever their signatures fit.
   For anything missing, declare a gap: a GENERIC, reusable capability (e.g. `fetch_company_record`, not
   `fetch_acme_record`), with JSON schemas, a realistic example input, and the minimal network hosts it needs.
Prefer kind=code. Use kind=llm only for steps that need real language judgment; they cost tokens on every run.
Prefer official structured APIs (JSON/REST/CSV) over scraping HTML pages.
Only use a data source you are sure exists at a real URL; never invent or use placeholder URLs or datasets.
If no reliable structured source exists for the facts asked (general-knowledge or research questions), do not force
a data pipeline: make it ONE kind=llm step with knowledge=true that answers from published knowledge, states its sources and uncertainty,
and takes the question's variable parts (region, measure, unit) as input. Use foreach only on real lists.
To apply a single-item capability to a list, use `foreach` — do not build batch duplicates of existing capabilities.
Never add trivial adapter/glue capabilities (format conversion, renaming fields). If two capabilities don't fit,
re-declare the consuming capability as a gap with the SAME name so an improved version is built.
Workflow input should be the task's variable parts (ids, dates, lists), so the workflow is reusable for similar tasks.
Bindings: '$input.<field>', '$steps.<id>.<field>', '$steps.<id>', or a JSON literal.
Be brief, it saves time: reasoning in at most 2 sentences, one-line descriptions, minimal schemas (only the needed
properties, no long descriptions). Prefer hosts the factory already knows (listed with the capabilities) when they fit."""

JUDGE = """You check whether a workflow's output actually answers the question it was given.
Be strict about facts being correct and plausible; ignore formatting and extra fields.
Judge only the answer to the given input, not the workflow's design. If the output honestly says that some requested
detail does not exist in published sources, accept that.
satisfied=false ONLY for blocking problems: the question is not answered at all, a key figure is clearly wrong or
implausible, or a source is invented. Wishes for more detail, more breakdowns, more citations or tighter ranges are
NOT blocking: set satisfied=true and put them in notes. List in `missing` only the blocking problems."""


class Verdict(BaseModel):
    satisfied: bool = Field(description="true only if the output fully answers the task")
    missing: list[str] = Field(description="what the task asked for that the output lacks or gets wrong")
    notes: str


def plan(task: str, budget: gateway.Budget, feedback: str = "") -> Plan:
    emit("stage", stage="plan", msg="planning" + (" (revised)" if feedback else ""))
    prompt = f"Registry:\n{registry.catalog_text()}\n\nTask:\n{task}"
    if feedback:
        prompt += ("\n\nA previous attempt was REJECTED by the acceptance check:\n" + feedback +
                   "\nFix it: declare a gap with the SAME name to build an improved version of an inadequate "
                   "capability, or add capabilities/steps for what is missing.")
    p: Plan = gateway.complete_json(budget, "plan", system=PLANNER, schema=Plan, prompt=prompt)
    emit("log", msg=f"plan: {p.reasoning[:300]}")
    return p


def judge(task: str, output, budget: gateway.Budget, purpose: str = "", task_input=None) -> Verdict:
    emit("stage", stage="accept", msg="acceptance check: does the output answer the task?")
    out = json.dumps(output, ensure_ascii=False, default=str)[:6000]
    ask = (f"A bot built for: {purpose}\nIt was given this input:\n{json.dumps(task_input, ensure_ascii=False)}"
           if purpose else f"Task:\n{task}")
    v: Verdict = gateway.complete_json(budget, "accept", system=JUDGE, schema=Verdict,
                                       prompt=f"{ask}\n\nOutput:\n{out}")
    emit("test", capability="acceptance",
         msg="acceptance: PASSED" if v.satisfied else f"acceptance: FAILED, missing {', '.join(v.missing)}")
    return v


BOT_FRAME = ("Build a reusable bot for this purpose: {purpose}\n"
             "There is no concrete question yet. Design the workflow input so a user can ask many different questions "
             "of this kind, and choose a realistic example input to test it with (task_input_json).")

EXTRACT = """Turn the user's question into the input of a workflow. Reply with one JSON object that matches the input
schema exactly. Use only facts from the question; normalise obvious formats (lists, numbers, dates)."""


INSTANT = """You are the scientist in a lab that builds reusable, tested bots. The user just described a bot they
want; it is being built now. If their message contains a concrete question, answer it right away, directly and
concisely (at most 120 words). If it only describes a purpose, say in 1-2 sentences what the bot will do and give one
example question. Plain text; short bullet lines starting with '- ' are fine. No headings."""


def _instant(purpose: str) -> None:
    """A quick, direct answer so nobody waits for the build. It is one plain model call: not a tested bot."""
    b = gateway.Budget(scope=f"instant:{purpose[:40]}", max_usd=0.05)
    try:
        text = gateway.complete(b, "instant", model=config.INSTANT_MODEL, system=INSTANT, prompt=purpose)
        emit("instant", text=text.strip(), tokens=b.tokens, model=config.INSTANT_MODEL,
             msg=f"quick answer ({b.tokens} tok, {config.INSTANT_MODEL}) while the monster is built")
    except Exception as e:
        emit("log", msg=f"quick answer skipped: {e}")


def check_input(schema, inp) -> tuple[dict, list[str]]:
    """Make an extracted input fit the bot's input schema: coerce obvious types ('80' -> 80, 'usd' kept as is),
    drop unknown keys, and report required fields that are still missing."""
    try:
        s = json.loads(schema) if isinstance(schema, str) else (schema or {})
    except json.JSONDecodeError:
        s = {}
    props, problems = s.get("properties") or {}, []
    if not isinstance(inp, dict):
        return {}, ["the input must be a JSON object"]
    out = {k: v for k, v in inp.items() if not props or k in props}
    for k, p in props.items():
        if k not in out or out[k] is None:
            continue
        t, v = p.get("type"), out[k]
        try:
            if t in ("number", "integer") and isinstance(v, str):
                n = float(v.replace(" ", "").replace("\u00a0", "").replace(",", "."))
                out[k] = int(n) if t == "integer" or n.is_integer() else n
            elif t == "array" and not isinstance(v, list):
                out[k] = [v]
            elif t == "string" and not isinstance(v, str):
                out[k] = str(v)
        except ValueError:
            problems.append(f"{k} should be a {t}, got {v!r}")
    problems += [f"missing required field {k}" for k in s.get("required", []) if out.get(k) in (None, "", [])]
    return out, problems


DESCRIBE = """You write the presentation layer of a bot, once, so every later answer reads well at zero cost.
Given its purpose, input schema, an example input and its real output, reply with JSON:
{"headline": "<one-sentence answer template with {placeholders}>", "examples": ["<question>", "<question>", "<question>"]}
Placeholders are dotted paths into the OUTPUT (lists by index: {ranking.0.currency}) or into the input as
{input.<field>}. Use only paths that exist in the example output, and prefer output fields over input fields
(outputs are cleaned up; inputs may be messy). The sentence must read naturally for other inputs
too, e.g. "{amount} {currency} is {czk} CZK at the ČNB rate of {rate_date}." If the output already has a
full-sentence answer field, the headline is just that placeholder, e.g. "{answer}".
examples: 3 short, varied questions a real user would type to this bot, in plain words."""


def describe_bot(wf: dict, example_input, output, budget: gateway.Budget | None = None) -> dict:
    b = budget or gateway.Budget(scope=f"describe:{wf['name']}", max_usd=0.05)
    m = wf["manifest"]
    try:
        d = gateway.complete_json(b, "describe", model=config.INSTANT_MODEL, system=DESCRIBE,
                                  prompt=f"Purpose: {m.get('purpose') or m.get('description')}\n"
                                         f"Input schema: {m['signature'].get('in')}\nExample input: {json.dumps(example_input, ensure_ascii=False)}\n"
                                         f"Output: {fit_json(output, 3000)}")
        patch = {"headline": str(d.get("headline") or "")[:300], "examples": [str(x)[:140] for x in (d.get("examples") or [])][:3]}
        registry.update_manifest(wf["name"], wf["version"], patch)
        return patch
    except Exception as e:
        emit("log", msg=f"could not write the answer template for {wf['name']}: {e}")
        return {}


def create_bot(purpose: str) -> dict:
    """The scientist's job: build (or find) a bot for a purpose. A quick direct answer arrives in seconds while
    the bot is built; asking the finished bot later is a separate, cheap step."""
    threading.Thread(target=_instant, args=(purpose,), daemon=True).start()
    return solve(BOT_FRAME.format(purpose=purpose), purpose=purpose)


def ask_bot(name: str, question: str = "", inp: dict | None = None) -> dict:
    """Ask an installed bot. A form input costs 0 tokens; a plain-language question costs one small extraction call
    on the runtime model (shown separately). The run itself is the compiled workflow, with self-repair."""
    wf = registry.get(name)
    if not wf or wf["kind"] != "workflow":
        raise KeyError(f"no active bot named {name}")
    m = wf["manifest"]
    ask_budget = gateway.Budget(scope=f"ask:{name}", max_usd=0.05)
    emit("ask", bot=name, question=question or None, input=inp, msg=f"asked {name}: {question or json.dumps(inp, ensure_ascii=False)[:120]}")
    if inp is None:
        emit("stage", stage="understand", msg=f"reading the question for {name}")
        system = EXTRACT + "\nInput schema: " + str(m["signature"].get("in")) + "\nExample input: " + str(m.get("example_input"))
        prompt = question
        for _ in range(2):
            inp, problems = check_input(m["signature"].get("in"), gateway.complete_json(
                ask_budget, f"ask:{name}", model=config.RUNTIME_MODEL, system=system, prompt=prompt))
            if not problems:
                break
            emit("log", msg=f"input did not fit the bot ({'; '.join(problems)}), asking again")
            prompt = f"{question}\n\nYour previous reading had problems: {'; '.join(problems)}. Fix them."
        emit("log", msg=f"question → input ({ask_budget.tokens} tok): {json.dumps(inp, ensure_ascii=False)[:200]}")
    run_budget = gateway.Budget(scope=f"run:{name}", max_usd=0.5,
                                max_tokens=m.get("budget", {}).get("max_tokens_per_run") or None)
    out = run_with_heal(wf, inp, run_budget, gateway.Budget(scope=f"task:heal {name}", max_usd=config.MAX_USD_PER_TASK))
    _record_replay(registry.get(name), inp, out)
    emit("result", workflow=name, result=out, input=inp, question=question or None, ask_tokens=ask_budget.tokens,
         run_tokens=run_budget.tokens, msg=f"{name} answered · {ask_budget.tokens} tok to read the question · "
                                            f"{run_budget.tokens} tok to run")
    return {"bot": name, "input": inp, "result": out, "ask_tokens": ask_budget.tokens, "run_tokens": run_budget.tokens}


def solve(task: str, purpose: str = "") -> dict:
    """Build (or reuse) a workflow for a task, then keep only what's useful: the working code, prompts and
    know-how stay; failed attempts and parts nothing uses are thrown away so the next build starts lean."""
    started = time.time()
    try:
        return _solve(task, purpose)
    finally:
        r = registry.prune(drop_unused_since=started)
        if r["dropped"]:
            emit("log", msg=f"tidied the registry: dropped {len(r['dropped'])} unused or failed versions "
                            f"({', '.join(r['dropped'][:6])}); kept working code, prompts and API notes")


def _solve(task: str, purpose: str = "") -> dict:
    budget = gateway.Budget(scope=f"task:{(purpose or task)[:40]}", max_usd=config.MAX_USD_PER_TASK)
    emit("task", text=purpose or task, mode="bot" if purpose else "task", msg=f"{'bot' if purpose else 'task'}: {purpose or task}")
    feedback = ""
    for attempt in range(config.MAX_REPLANS + 1):
        p = plan(task, budget, feedback)
        task_input = json.loads(p.task_input_json)

        emit("stage", stage="discover", msg="checking the registry")
        if p.reuse_workflow and registry.get(p.reuse_workflow) and not feedback:
            wf = registry.get(p.reuse_workflow)
            emit("say", text=f"I already know how to do this with {wf['name'].replace('_', ' ')}. No building needed.")
            return _finish(wf, task_input, budget)

        active = {a["name"] for a in registry.list_artifacts("capability")}
        gap_names = {g.name for g in p.gaps}
        reused = sorted({s.uses for s in p.steps if s.uses in active and s.uses not in gap_names})
        if reused:
            emit("log", msg=f"reusing: {', '.join(reused)}")
        unknown = [s.uses for s in p.steps if s.uses not in active and s.uses not in gap_names]
        if unknown:
            raise RuntimeError(f"plan references unknown capabilities: {unknown}")

        emit("stage", stage="gap", msg=f"{len(p.gaps)} gaps: {', '.join(sorted(gap_names)) or 'none'}")
        emit("monster", steps=[{"id": s.id, "uses": s.uses,
                                "state": "missing" if s.uses in gap_names else "ready"} for s in p.steps])
        try:
            build_on_real_data(p, task_input, budget)
            wf, output = assemble(p, purpose)
        except StepFailed as e:
            emit("say", text=f"The parts don't fit together yet: {e.capability.replace('_', ' ')} failed. Back to the bench.")
            feedback = f"End-to-end run failed at step {e.step} ({e.capability}): {e.detail[-1200:]}"
            continue
        verdict = judge(task, output, budget, purpose, task_input)
        if verdict.satisfied:
            return install_workflow(wf, task_input, output, budget)
        registry.set_status(wf["name"], wf["version"], "archived")
        emit("say", text=f"Not good enough. It's missing {', '.join(verdict.missing[:3])}. Back to the bench.")
        feedback = (f"Workflow {wf['name']} used {[s['uses'] for s in wf['manifest']['steps']]}.\n"
                    f"Missing: {verdict.missing}\nJudge notes: {verdict.notes}\n"
                    f"Output was: {json.dumps(output, ensure_ascii=False, default=str)[:1500]}")
    raise RuntimeError(f"gave up after {config.MAX_REPLANS + 1} attempts (replan cap)")


def fit_json(obj, limit: int) -> str:
    """A JSON sample of `obj` no longer than `limit` chars that is still valid JSON with the same shape:
    long strings and lists are shortened instead of cutting the text mid-string."""
    def shrink(v, s, n):
        if isinstance(v, str):
            return v if len(v) <= s else v[:s] + "…"
        if isinstance(v, list):
            return [shrink(x, s, n) for x in v[:n]]
        if isinstance(v, dict):
            return {k: shrink(x, s, n) for k, x in v.items()}
        return v
    text = json.dumps(obj, ensure_ascii=False, default=str)
    s, n = 2000, 50
    while len(text) > limit and (s > 20 or n > 1):
        s, n = max(20, s // 2), max(1, n // 2)
        text = json.dumps(shrink(json.loads(json.dumps(obj, default=str)), s, n), ensure_ascii=False)
    return text


def build_on_real_data(p: Plan, task_input: dict, budget: gateway.Budget) -> None:
    """Build gaps in step order. Each missing capability is built and tested against the REAL output of the
    steps before it (not an imagined example), so the parts are guaranteed to fit together."""
    gaps = {g.name: g for g in p.gaps}
    built: set[str] = set()
    outputs: dict = {}
    for s in p.steps:
        try:
            items = resolve(s.foreach, task_input, outputs) if s.foreach else None
            if items is not None and not isinstance(items, list):
                raise TypeError(f"foreach source {s.foreach} is not a list")
            sample = items[0] if items else None
            inp = {b.param: resolve(b.source, task_input, outputs, sample) for b in s.inputs}
        except (KeyError, IndexError, TypeError) as e:
            raise StepFailed(s.id, s.uses, f"input binding failed: {e!r}; upstream outputs: "
                             f"{json.dumps(outputs, ensure_ascii=False, default=str)[:800]}")
        if s.uses in gaps and s.uses not in built:
            g = gaps[s.uses].model_copy()
            g.example_input_json = fit_json(inp, 6000)
            if outputs:
                g.why_missing += " (Example input is REAL output of the previous steps — handle exactly this shape.)"
            install_capability(g, budget)
            built.add(s.uses)
        cap = registry.get(s.uses)
        try:
            if items is not None:
                batch = [{b.param: resolve(b.source, task_input, outputs, it) for b in s.inputs} for it in items]
                outputs[s.id] = {"items": run_capability_batch(cap, batch, budget)}
            else:
                outputs[s.id], _ = run_capability(cap, inp, budget)
        except Exception as e:
            raise StepFailed(s.id, s.uses, str(e)) from e


def install_capability(g, budget: gateway.Budget) -> None:
    prev = registry.get(g.name)
    built = build_capability(g, budget)
    if not built:
        raise RuntimeError(f"could not build {g.name}")
    cap = registry.get(built["name"], built["version"])
    perms = cap["manifest"]["permissions"]
    title = (f"upgrade {g.name} v{prev['version']} → v{built['version']}" if prev
             else f"install capability {g.name} v{built['version']}")
    widened = sorted(set(perms["net"]) - set(prev["manifest"]["permissions"]["net"])) if prev else []
    detail = {"description": cap["description"], "tests": built["test_report"]["summary"],
              "network": perms["net"] or "none"}
    if widened:
        detail["NEW network access"] = widened
    if not gate.ask("install", title, detail):
        registry.set_status(g.name, built["version"], "archived")
        raise RuntimeError(f"operator rejected {g.name}")
    registry.install(g.name, built["version"], built["test_report"])
    emit("monster_part", uses=g.name, state="ready")


def assemble(p: Plan, purpose: str = "") -> tuple[dict, object]:
    """Register the workflow as a candidate and test it end-to-end on the task input. Returns (wf, output)."""
    emit("stage", stage="assemble", msg=f"assembling workflow {p.workflow_name}")
    steps = [{"id": s.id, "uses": s.uses, "foreach": s.foreach, "inputs": [b.model_dump() for b in s.inputs]}
             for s in p.steps]
    caps = [registry.get(s["uses"]) for s in steps]
    llm_budget = sum(c["manifest"].get("budget", {}).get("max_tokens_per_run", 0) for c in caps)
    manifest = {
        "name": p.workflow_name, "kind": "workflow", "description": p.workflow_description,
        "signature": {"in": p.workflow_input_schema_json, "out": f"output of {p.output_step}"},
        "steps": steps, "output_step": p.output_step,
        "permissions": {"net": sorted({h for c in caps for h in c["manifest"]["permissions"]["net"]}),
                        "llm": any(c["manifest"]["permissions"].get("llm") for c in caps)},
        "budget": {"max_tokens_per_run": llm_budget},
        "lineage": {"created_by": "factory", "uses": [f"{c['name']}@v{c['version']}" for c in caps]},
        "example_input": p.task_input_json,
        "purpose": purpose or p.workflow_description,
    }
    version = registry.save_candidate(manifest, {})
    wf = registry.get(p.workflow_name, version)
    emit("stage", stage="test", msg=f"end-to-end test of {p.workflow_name}")
    try:
        out = run_workflow(wf, json.loads(p.task_input_json))
    except StepFailed as e:
        registry.set_status(p.workflow_name, version, "archived")
        emit("test", msg=f"end-to-end run failed: {e}", capability=p.workflow_name)
        raise
    emit("test", msg="end-to-end run on task input succeeded", capability=p.workflow_name)
    return wf, out


def install_workflow(wf: dict, task_input: dict, output, budget: gateway.Budget) -> dict:
    m = wf["manifest"]
    rep = {"passed": True, "summary": "end-to-end run succeeded and acceptance check passed",
           "output": json.dumps(output, ensure_ascii=False, default=str)[:2000]}
    if not gate.ask("install", f"install workflow {wf['name']} v{wf['version']}",
                    {"steps": [s["uses"] for s in m["steps"]], "network": m["permissions"]["net"] or "none",
                     "llm tokens per run (cap)": m["budget"]["max_tokens_per_run"], "tests": rep["summary"]}):
        registry.set_status(wf["name"], wf["version"], "archived")
        raise RuntimeError("operator rejected workflow")
    registry.install(wf["name"], wf["version"], rep)
    describe_bot(registry.get(wf["name"]), task_input, output, budget)
    wf = registry.get(wf["name"])
    _record_replay(wf, task_input, output)
    emit("result", workflow=wf["name"], result=output, input=task_input, build_usd=round(budget.spent_usd, 4),
         msg=f"done · build ${budget.spent_usd:.3f} · {budget.calls} LLM calls")
    emit("say", text="It's alive. It still uses a language model for judgment, so each run costs tokens."
         if m["permissions"].get("llm") else "It's alive. From now on, this job costs zero tokens.")
    return {"workflow": wf["name"], "version": wf["version"], "result": output, "build_usd": budget.spent_usd}


def regression_test(rows: list[dict]) -> str:
    """Harness-generated contract test from a capability's own successful history: a healed version must still
    accept every input that used to work and return the same output fields with the same types."""
    return f'''import json
import pytest
from impl import run
ROWS = json.loads({json.dumps(json.dumps(rows, ensure_ascii=False, default=str))})

@pytest.mark.parametrize("row", ROWS)
def test_still_handles_recorded_input(row):
    out = run(row["input"])
    for k, v in row["output"].items():
        assert k in out, f"missing field {{k}}"
        if v is not None and out[k] is not None:
            assert type(out[k]) is type(v), f"field {{k}} changed type"
'''


def heal(e: StepFailed, budget: gateway.Budget, wf: dict) -> bool:
    """Self-repair: rebuild ONLY the capability that broke, against the input it failed on, and require it to
    keep working on its recorded history. Operator approves; the old version stays available for rollback."""
    from .compiler import history_all_versions
    cap = registry.get(e.capability)
    if not cap or cap["manifest"].get("impl") != "code":
        return False
    m = cap["manifest"]
    err = next((l.strip() for l in reversed(e.detail.splitlines()) if "Error" in l), e.detail.strip()[-160:])
    emit("stage", stage="heal", capability=cap["name"], msg=f"healing {cap['name']} v{cap['version']}: {err[:200]}")
    emit("say", text=f"Step {e.step} broke: {cap['name'].replace('_', ' ')}. I'll repair just that part.")
    emit("monster", steps=[{"id": s["id"], "uses": s["uses"],
                            "state": "failed" if s["uses"] == cap["name"] else "ready"} for s in wf["manifest"]["steps"]])
    history = history_all_versions(cap["name"])
    old_impl = Path(cap["path"], "impl.py").read_text(encoding="utf-8")
    gap = GapSpec(
        name=cap["name"], description=m.get("description", cap["description"]), kind="code",
        why_missing=(f"v{cap['version']} FAILED in production on input {json.dumps(e.inputs[:3], ensure_ascii=False)[:600]} "
                     f"with: {e.detail[-900:]}\nMake it robust to this kind of input while keeping its behaviour and "
                     f"output fields. Previous impl.py:\n{old_impl[:4000]}"),
        input_schema_json=m["signature"].get("in_schema") or "{}", output_schema_json=m["signature"].get("out_schema") or "{}",
        net_hosts=m["permissions"].get("net", []),
        example_input_json=json.dumps(e.inputs[0] if e.inputs else {}, ensure_ascii=False))
    extra = {"test_regression.py": regression_test(history)} if history else None
    built = build_capability(gap, budget, extra_tests=extra)
    if not built:
        emit("say", text="I couldn't repair it. The old version stays in place.")
        return False
    if not gate.ask("install", f"heal {cap['name']} v{cap['version']} → v{built['version']}",
                    {"broke on": json.dumps(e.inputs[:2], ensure_ascii=False)[:300], "error": e.detail[-300:],
                     "tests": built["test_report"]["summary"],
                     "regression": f"{len(history)} recorded inputs (every input shape seen) must still work" if history else "no history yet"}):
        registry.set_status(cap["name"], built["version"], "archived")
        return False
    registry.install(cap["name"], built["version"], built["test_report"])
    emit("monster_part", uses=cap["name"], state="ready")
    return True


def run_with_heal(wf: dict, task_input: dict, run_budget: gateway.Budget, heal_budget: gateway.Budget):
    """Run a workflow; if a step breaks, heal that step once and retry. If the healed version still fails,
    roll it back so the registry never ends up worse than before."""
    try:
        return run_workflow(wf, task_input, budget=run_budget)
    except StepFailed as e:
        if not heal(e, heal_budget, wf):
            raise
        healed = e.capability
    emit("say", text="Repaired. Running it again.")
    try:
        return run_workflow(registry.get(wf["name"]), task_input, budget=run_budget)
    except StepFailed:
        v = registry.rollback(healed)
        emit("say", text=f"Still broken. I rolled {healed.replace('_', ' ')} back to version {v}.")
        raise


IGOR = """You are Igor, the lab assistant whose job is to break bots before customers do. You get a bot's input
schema, an example input that works, and your earlier attempts that it survived. Write ONE new input a real user
could plausibly send (from a web form, a spreadsheet or another program) that means something valid but is messy in
form: numbers as strings in local format ("1 200,50", "1.200,50 Kč"), currency or country names instead of codes
("euros", "Czech"), symbols ("€"), stray whitespace, prefixes (country codes on ids), different case, short forms,
duplicates, an empty extra item. Keep the schema's field names; values may be sloppy in type. Each attempt must try a
DIFFERENT trick, nastier than the last. It must stay answerable by a careful human. Reply with JSON:
{"input": {...}, "trick": "<one short sentence, in Igor's voice, saying what you messed up>"}"""

IGOR_TRIES = 3


def sabotage(name: str) -> dict:
    """Igor attacks a bot with messy-but-legitimate input, escalating up to IGOR_TRIES times. When a step breaks,
    the factory repairs only that part (regression-tested against its history) and the bot answers anyway.
    Every outcome is reported as it happened."""
    wf = registry.get(name)
    if not wf or wf["kind"] != "workflow":
        raise KeyError(f"no active bot named {name}")
    m = wf["manifest"]
    before = {s["uses"]: registry.get(s["uses"])["version"] for s in m["steps"] if registry.get(s["uses"])}
    emit("igor", bot=name, phase="plot", msg=f"Igor is looking for a way to break {name}")
    b = gateway.Budget(scope=f"igor:{name}", max_usd=0.20)
    tried: list[str] = []
    broke = None
    for n in range(1, IGOR_TRIES + 1):
        attack = gateway.complete_json(
            b, "igor", model=config.INSTANT_MODEL, system=IGOR,
            prompt=f"Input schema: {m['signature'].get('in')}\nWorking example: {m.get('example_input')}\n"
                   f"Attempts it survived: {json.dumps(tried, ensure_ascii=False) if tried else 'none yet'}")
        inp, trick = attack.get("input") or {}, attack.get("trick") or "I messed up the input."
        emit("igor", bot=name, phase="attack", attempt=n, input=inp, trick=trick, msg=f"Igor (try {n}): {trick}")
        if n == 1:
            emit("say", text="Igor! What are you doing to my monster?")
        try:
            out = run_workflow(wf, inp, budget=gateway.Budget(scope=f"run:{name}", max_usd=0.5))
        except StepFailed as e:
            broke = (inp, e)
            emit("igor", bot=name, phase="hit", attempt=n, step=e.step, capability=e.capability,
                 msg=f"Igor broke step {e.step} ({e.capability})")
            break
        _record_replay(registry.get(name), inp, out)
        tried.append(trick)
        emit("igor", bot=name, phase="dodge", attempt=n, result=out, msg=f"{name} handled try {n}")
    if not broke:
        emit("igor", bot=name, phase="survived", msg=f"{name} survived all {IGOR_TRIES} of Igor's tries")
        emit("say", text="Nice try, Igor. Not a scratch.")
        return {"bot": name, "healed": [], "tries": tried}
    inp, _ = broke
    run_budget = gateway.Budget(scope=f"run:{name}", max_usd=0.5, max_tokens=m.get("budget", {}).get("max_tokens_per_run") or None)
    try:
        out = run_with_heal(wf, inp, run_budget, gateway.Budget(scope=f"task:heal {name}", max_usd=config.MAX_USD_PER_TASK))
    except Exception as e:
        emit("igor", bot=name, phase="won", msg=f"the repair did not hold, so the old version stays: {str(e)[:160]}")
        emit("say", text="He got me. The old version stays in place, nothing was made worse.")
        raise
    healed = [f"{c} v{v} → v{registry.get(c)['version']}" for c, v in before.items()
              if registry.get(c) and registry.get(c)["version"] != v]
    _record_replay(registry.get(name), inp, out)
    emit("result", workflow=name, result=out, input=inp, via="Igor (sabotage)", run_tokens=run_budget.tokens,
         msg=f"{name} answered Igor after repairing {', '.join(healed) or 'nothing'}")
    fixed = {c.split(" v")[0] for c in healed}
    also = sorted({w["name"] for w in registry.list_artifacts("workflow") if w["name"] != name
                   and fixed & {s["uses"] for s in w["manifest"].get("steps", [])}})
    emit("igor", bot=name, phase="healed", healed=healed, also_fixed=also,
         msg=f"repaired {', '.join(healed)}: Igor's input is now a regression test"
             + (f"; the shared part also fixes {', '.join(also)}" if also else ""))
    emit("say", text="Ha! Repaired, and stronger than before.")
    return {"bot": name, "input": inp, "healed": healed, "result": out}


def _finish(wf: dict, task_input: dict, budget: gateway.Budget) -> dict:
    run_budget = gateway.Budget(scope=f"run:{wf['name']}", max_usd=0.50,
                                max_tokens=wf["manifest"].get("budget", {}).get("max_tokens_per_run") or None)
    result = run_with_heal(wf, task_input, run_budget, budget)
    _record_replay(wf, task_input, result)
    emit("result", workflow=wf["name"], result=result, input=task_input, build_usd=round(budget.spent_usd, 4),
         run_tokens=run_budget.tokens, msg=f"done · planning ${budget.spent_usd:.3f} · run {run_budget.tokens} tok")
    emit("say", text="Done. Zero tokens for the run itself." if not run_budget.tokens else "Done.")
    return {"workflow": wf["name"], "version": wf["version"], "result": result,
            "build_usd": budget.spent_usd, "run_tokens": run_budget.tokens}


def _record_replay(wf: dict, inp: dict, out) -> None:
    """Every real run becomes a regression case for future versions (tests grow from use)."""
    with Path(wf["path"], "replay.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps({"input": inp, "output": out}, ensure_ascii=False, default=str) + "\n")
