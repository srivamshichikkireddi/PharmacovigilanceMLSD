"""run_case: execute one report through the agent graph and return (result, trace)."""
import logging, os, time

from pv import metrics, tracing
from pv.agents.graph import run_langgraph, run_sequential
from pv.agents.state import RunContext
from pv.config import settings as default_settings


def run_case(run_id, report, index, llm, settings=default_settings, drugs=None, use_langgraph=None):
    from pv.rules import load_drugs
    drugs = drugs if drugs is not None else load_drugs(settings.corpus_dir)
    def fresh():
        return RunContext(run_id, report, index, llm, settings, drugs)

    ctx = fresh()
    if use_langgraph is None:
        use_langgraph = os.environ.get("PV_USE_LANGGRAPH", "1") != "0"
    engine = "sequential"
    with tracing.span("run_case", run_id=run_id):
        if use_langgraph:
            try:
                import langgraph  # noqa
                run_langgraph(ctx)
                engine = "langgraph"
            except ImportError:
                ctx = fresh()
            except Exception as exc:  # noqa  orchestration-framework failure must not take the service down
                logging.getLogger("pipeline").warning("langgraph engine failed (%s); using the sequential executor", exc)
                ctx, engine = fresh(), "sequential(fallback)"
        if engine.startswith("sequential"):
            run_sequential(ctx)
    res = ctx.final
    elapsed = time.monotonic() - ctx.t0
    res.update(prompt_versions=ctx.prompt_versions, cost=ctx.cost_summary(), steps=ctx.steps, graph_steps=ctx.graph_steps,
               tool_calls=ctx.tool_calls, strategy_attempts=ctx.attempts + 1, engine=engine, mode=ctx.mode,
               corpus_version=index.version, retrieval_backend=index.backend, agentic=settings.agentic,
               run_seconds=round(elapsed, 3))
    metrics.RUN_SECONDS.labels(res["route"]).observe(elapsed)
    metrics.STEPS_PER_RUN.observe(ctx.graph_steps)
    metrics.TOOLS_PER_RUN.observe(ctx.tool_calls)
    metrics.VERIFY.labels((res["verification"] or {}).get("status", "none")).inc()
    for r in res["escalation"]["reasons"]:
        metrics.ESCALATIONS.labels(r.split(":")[0]).inc()
    for d in ctx.degraded:
        metrics.DEGRADED.labels(d.split("(")[0]).inc()
    return res, ctx.trace
