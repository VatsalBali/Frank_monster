"""The factory loop:
PLAN → DISCOVER → GAP? → BUILD (learn+test) → GATE → INSTALL → ASSEMBLE → E2E TEST → ACCEPT? → GATE → INSTALL → RUN.
A failed acceptance check is itself a detected gap: the factory replans with the judge's feedback (capped)."""
import json
from pathlib import Path

from pydantic import BaseModel, Field

from . import config, gate, gateway, registry
from .builder import build_capability
from .events import emit
from .executor import StepFailed, resolve, run_capability, run_capability_batch, run_workflow
from .models import Plan

PLANNER = """You are the planner of a workflow factory. A workflow is a pipeline of reusable capabilities that runs
cheaply and deterministically after it is built. Given a task and the registry, decide:
1. If an existing workflow already solves this kind of task, set reuse_workflow and give its input.
2. Otherwise design a workflow (steps in execution order). Reuse existing capabilities wherever their signatures fit.
   For anything missing, declare a gap: a GENERIC, reusable capability (e.g. `fetch_company_record`, not
   `fetch_acme_record`), with JSON schemas, a realistic example input, and the minimal network hosts it needs.
Prefer kind=code. Use kind=llm only for steps that need real language judgment; they cost tokens on every run.
Prefer official structured APIs (JSON/REST/CSV) over scraping HTML pages.
To apply a single-item capability to a list, use `foreach` — do not build batch duplicates of existing capabilities.
Never add trivial adapter/glue capabilities (format conversion, renaming fields). If two capabilities don't fit,
re-declare the consuming capability as a gap with the SAME name so an improved version is built.
Workflow input should be the task's variable parts (ids, dates, lists), so the workflow is reusable for similar tasks.
Bindings: '$input.<field>', '$steps.<id>.<field>', '$steps.<id>', or a JSON literal."""

JUDGE = """You check whether a workflow's output actually answers the task it was built for.
Be strict about requested facts being present and plausible; ignore formatting and extra fields."""


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


def judge(task: str, output, budget: gateway.Budget) -> Verdict:
    emit("stage", stage="accept", msg="acceptance check: does the output answer the task?")
    out = json.dumps(output, ensure_ascii=False, default=str)[:6000]
    v: Verdict = gateway.complete_json(budget, "accept", system=JUDGE, schema=Verdict,
                                       prompt=f"Task:\n{task}\n\nOutput:\n{out}")
    emit("test", capability="acceptance",
         msg="acceptance: PASSED" if v.satisfied else f"acceptance: FAILED, missing {', '.join(v.missing)}")
    return v


def solve(task: str) -> dict:
    budget = gateway.Budget(scope=f"task:{task[:40]}", max_usd=config.MAX_USD_PER_TASK)
    emit("task", text=task, msg=f"task: {task}")
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
            wf, output = assemble(p)
        except StepFailed as e:
            emit("say", text=f"The parts don't fit together yet: {e.capability.replace('_', ' ')} failed. Back to the bench.")
            feedback = f"End-to-end run failed at step {e.step} ({e.capability}): {e.detail[-1200:]}"
            continue
        verdict = judge(task, output, budget)
        if verdict.satisfied:
            return install_workflow(wf, task_input, output, budget)
        registry.set_status(wf["name"], wf["version"], "archived")
        emit("say", text=f"Not good enough. It's missing {', '.join(verdict.missing[:3])}. Back to the bench.")
        feedback = (f"Workflow {wf['name']} used {[s['uses'] for s in wf['manifest']['steps']]}.\n"
                    f"Missing: {verdict.missing}\nJudge notes: {verdict.notes}\n"
                    f"Output was: {json.dumps(output, ensure_ascii=False, default=str)[:1500]}")
    raise RuntimeError(f"gave up after {config.MAX_REPLANS + 1} attempts (replan cap)")


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
            g.example_input_json = json.dumps(inp, ensure_ascii=False, default=str)[:6000]
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


def assemble(p: Plan) -> tuple[dict, object]:
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
    wf = registry.get(wf["name"])
    _record_replay(wf, task_input, output)
    emit("result", workflow=wf["name"], result=output, build_usd=round(budget.spent_usd, 4),
         msg=f"done · build ${budget.spent_usd:.3f} · {budget.calls} LLM calls")
    emit("say", text="It's alive. It still uses a language model for judgment, so each run costs tokens."
         if m["permissions"].get("llm") else "It's alive. From now on, this job costs zero tokens.")
    return {"workflow": wf["name"], "version": wf["version"], "result": output, "build_usd": budget.spent_usd}


def _finish(wf: dict, task_input: dict, budget: gateway.Budget) -> dict:
    run_budget = gateway.Budget(scope=f"run:{wf['name']}", max_usd=0.50,
                                max_tokens=wf["manifest"].get("budget", {}).get("max_tokens_per_run") or None)
    try:
        result = run_workflow(wf, task_input, budget=run_budget)
    except StepFailed as e:
        emit("say", text=f"Step {e.step} broke: {e.capability.replace('_', ' ')}.")
        raise
    _record_replay(wf, task_input, result)
    emit("result", workflow=wf["name"], result=result, build_usd=round(budget.spent_usd, 4),
         run_tokens=run_budget.tokens, msg=f"done · planning ${budget.spent_usd:.3f} · run {run_budget.tokens} tok")
    emit("say", text="Done. Zero tokens for the run itself." if not run_budget.tokens else "Done.")
    return {"workflow": wf["name"], "version": wf["version"], "result": result,
            "build_usd": budget.spent_usd, "run_tokens": run_budget.tokens}


def _record_replay(wf: dict, inp: dict, out) -> None:
    """Every real run becomes a regression case for future versions (tests grow from use)."""
    with Path(wf["path"], "replay.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps({"input": inp, "output": out}, ensure_ascii=False, default=str) + "\n")
