"""Workflow execution.

Two strategies, one state machine. `sequential` walks the DAG in topological
order in-process. `parallel` (Phase 5) dispatches each ready wave as Celery
tasks and reschedules itself to collect them.

Still not chains or chords: those make partial failure opaque, prevent per-node
retry/cancel/resume, and would have to be re-shaped for every LLM-produced
topology. The orchestrator also never blocks on group results -- with
task_acks_late and prefetch 1 that deadlocks, and under --pool=solo (the
Windows default) the orchestrator would occupy the only worker slot while
waiting for children that can never start. It polls the database instead, which
is the same source of truth /status reads.

Sequential remains the default for exactly that reason: parallel is a win only
on a prefork worker with concurrency > 1.

execute_one() is the only place that touches NodeRun timing, artifact
persistence and error capture, so both strategies share it unchanged.
"""

from __future__ import annotations

import logging
import shutil
import tempfile
import traceback
from datetime import datetime, timezone
from pathlib import Path

from celery import group

from app.celery_app import celery_app
from app.config import get_settings
from app.database import SessionLocal
from app.models import Artifact, NodeRun, Workflow, WorkflowRun
from app.services.dag import (blocking, descendants, in_flight, next_actions,
                              resolve_inputs, topo_sort, validate_graph)
from app.services.nodes import load_registry
from app.services.nodes.base import ArtifactRef, NodeContext, NodeError, Produced
from app.services.storage import get_store

log = logging.getLogger(__name__)

_EXT = {
    "json": ".json", "scalar": ".json", "raster": ".tif",
    "vector": ".geojson", "table": ".csv", "ee_object": ".eejson",
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _persist(db, store, run: WorkflowRun, node_run: NodeRun,
             node_id: str, p: Produced) -> Artifact:
    ext = p.ext or _EXT.get(p.kind, ".bin")
    key = f"{run.id}/{node_id}/{p.name}{ext}"

    size = None
    if p.local_path is not None:
        uri = store.put(Path(p.local_path), key)
        size = Path(p.local_path).stat().st_size
    elif p.kind == "ee_object":
        data = str(p.value).encode()
        uri = store.put_bytes(data, key)
        size = len(data)
    else:
        uri = store.put_json(p.value, key)

    meta = dict(p.meta or {})
    if size is not None:
        meta["size_bytes"] = size

    art = Artifact(workflow_run_id=run.id, node_run_id=node_run.id, node_id=node_id,
                   name=p.name, kind=p.kind, uri=uri, meta=meta)
    db.add(art)
    return art


def _resolve_input_refs(db, run: WorkflowRun, graph: dict,
                        node_id: str) -> dict[str, ArtifactRef]:
    wanted = resolve_inputs(graph, node_id)      # name -> (producer_id, output_name)
    refs: dict[str, ArtifactRef] = {}
    for name, (producer, out_name) in wanted.items():
        art = (db.query(Artifact)
               .filter_by(workflow_run_id=run.id, node_id=producer, name=out_name)
               .first())
        if art is None:
            raise NodeError(
                f"Upstream artifact '{out_name}' from node '{producer}' is missing.")
        refs[name] = ArtifactRef(id=str(art.id), node_id=art.node_id, name=art.name,
                                 kind=art.kind, uri=art.uri, meta=art.meta or {})
    return refs


def execute_one(db, store, run: WorkflowRun, wf: Workflow,
                graph: dict, node: dict) -> str:
    """Run a single node and record the outcome. Returns 'done' or 'failed'."""
    registry = load_registry()
    node_id = node["id"]

    node_run = db.query(NodeRun).filter_by(workflow_run_id=run.id,
                                           node_id=node_id).first()
    if node_run is None:
        node_run = NodeRun(workflow_run_id=run.id, node_id=node_id,
                           node_type=node["type"], status="pending")
        db.add(node_run)
        db.commit()
        db.refresh(node_run)

    workdir = Path(tempfile.mkdtemp(prefix=f"geoflow_{node_id}_"))
    try:
        node_run.status = "running"
        node_run.started_at = _utcnow()
        db.commit()

        nd = registry[node["type"]]
        params = {**nd.defaults, **(node.get("params") or {})}
        inputs = _resolve_input_refs(db, run, graph, node_id)

        ctx = NodeContext(
            workflow_run_id=str(run.id), node_id=node_id, node_type=node["type"],
            params=params, aoi=wf.aoi or {}, date_range=wf.date_range or {},
            store=store, db=db, log=log.getChild(node_id), workdir=workdir,
        )

        produced = nd.fn(ctx, inputs) or []
        arts = [_persist(db, store, run, node_run, node_id, p) for p in produced]
        db.flush()

        node_run.status = "done"
        node_run.output_ref = arts[0].uri if arts else None
        node_run.finished_at = _utcnow()
        db.commit()
        return "done"

    except Exception as e:
        db.rollback()
        # Re-fetch: the rollback detached whatever was pending on the session.
        node_run = db.query(NodeRun).filter_by(workflow_run_id=run.id,
                                               node_id=node_id).first()
        if node_run is not None:
            node_run.status = "failed"
            node_run.error = f"{type(e).__name__}: {e}"
            node_run.logs = traceback.format_exc()[-8000:]
            node_run.finished_at = _utcnow()
            db.commit()
        log.exception("Node %s failed", node_id)
        return "failed"
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def _node_statuses(db, run: WorkflowRun) -> dict[str, str]:
    return {nr.node_id: nr.status
            for nr in db.query(NodeRun).filter_by(workflow_run_id=run.id).all()}


def _node_run(db, run: WorkflowRun, by_id: dict, node_id: str) -> NodeRun:
    nr = db.query(NodeRun).filter_by(workflow_run_id=run.id, node_id=node_id).first()
    if nr is None:
        nr = NodeRun(workflow_run_id=run.id, node_id=node_id,
                     node_type=by_id[node_id]["type"], status="pending")
        db.add(nr)
    return nr


def _mark_skipped(db, run: WorkflowRun, by_id: dict, node_ids) -> None:
    for node_id in node_ids:
        nr = _node_run(db, run, by_id, node_id)
        nr.status = "skipped"
        nr.error = "Upstream node failed."
        nr.finished_at = _utcnow()
    if node_ids:
        db.commit()


def _finalize(db, run: WorkflowRun, wf: Workflow) -> str:
    db.refresh(run)
    statuses = list(_node_statuses(db, run).values())
    if run.status != "cancelled":
        run.status = "done" if statuses and all(s == "done" for s in statuses) else "failed"
    run.finished_at = _utcnow()
    wf.status = run.status
    db.commit()
    return run.status


def _begin(db, run: WorkflowRun, wf: Workflow, graph: dict) -> str | None:
    """Mark the run started and validate. Returns 'invalid' to stop, else None.

    Validation happens before any NodeRun row exists, so a rejected graph
    leaves no execution rows behind at all.
    """
    if run.status != "running":
        run.status = "running"
        run.started_at = _utcnow()
        db.commit()

    errors = blocking(validate_graph(graph, load_registry()))
    if not errors:
        return None

    run.status = "failed"
    run.error = "; ".join(f"[{e.code}] {e.message}" for e in errors)
    run.finished_at = _utcnow()
    wf.status = "failed"
    db.commit()
    return "invalid"


def _expired(run: WorkflowRun) -> bool:
    started = run.started_at
    if started is None:
        return False
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    return (_utcnow() - started).total_seconds() > get_settings().run_timeout_seconds


def _run_sequential(db, store, run: WorkflowRun, wf: Workflow, graph: dict) -> str:
    order = topo_sort(graph)
    by_id = {n["id"]: n for n in graph["nodes"]}
    dead: set[str] = set()

    for node_id in order:
        db.refresh(run)
        if run.status == "cancelled":
            break

        if node_id in dead:
            _mark_skipped(db, run, by_id, [node_id])
            continue

        if execute_one(db, store, run, wf, graph, by_id[node_id]) == "failed":
            dead |= descendants(graph, node_id)

    return _finalize(db, run, wf)


def _tick_parallel(db, run: WorkflowRun, wf: Workflow, graph: dict, tick: int) -> str:
    """One scheduling pass: skip what is doomed, dispatch what is ready, and
    reschedule until nothing is left in flight."""
    # Local import: executor imports execute_one from this module.
    from app.services.executor import execute_node

    s = get_settings()
    by_id = {n["id"]: n for n in graph["nodes"]}

    db.refresh(run)
    if run.status == "cancelled":
        return _finalize(db, run, wf)

    if _expired(run):
        run.error = (f"Run exceeded run_timeout_seconds="
                     f"{s.run_timeout_seconds} with nodes still in flight.")
        run.status = "failed"
        return _finalize(db, run, wf)

    # A tick that dispatches nothing and collects nothing means a node is stuck
    # in 'queued' -- a worker died, or its task was never picked up. Bounded so
    # that failure surfaces as a failed run rather than an endless reschedule.
    if tick > 4 * len(by_id) + 20:
        run.error = "Scheduler stopped making progress; nodes stuck in flight."
        run.status = "failed"
        return _finalize(db, run, wf)

    statuses = _node_statuses(db, run)
    ready, skip = next_actions(graph, statuses)
    _mark_skipped(db, run, by_id, skip)

    if skip:
        statuses = _node_statuses(db, run)
        ready, _ = next_actions(graph, statuses)

    running = in_flight(statuses)
    if not ready and not running:
        return _finalize(db, run, wf)

    # Claim before dispatching: the status flip is what stops the next tick
    # from queueing the same node twice.
    batch = ready[:s.max_parallel_nodes] if s.max_parallel_nodes else ready
    for node_id in batch:
        nr = _node_run(db, run, by_id, node_id)
        nr.status = "queued"
    db.commit()

    run_id = str(run.id)
    if batch:
        log.info("Run %s wave: dispatching %s", run_id, batch)
        group(execute_node.s(run_id, n) for n in batch).apply_async()

    # Release the connection before rescheduling. Under task_always_eager the
    # next tick runs inline, nested inside this one, so holding the session
    # would pile up one connection per wave and exhaust the pool.
    db.close()
    run_workflow_task.apply_async((run_id, tick + 1),
                                  countdown=s.orchestrator_poll_seconds)
    return "running"


@celery_app.task(name="run_workflow", bind=True)
def run_workflow_task(self, workflow_run_id: str, tick: int = 0):
    db = SessionLocal()
    store = get_store()
    try:
        run = db.query(WorkflowRun).filter_by(id=workflow_run_id).first()
        if run is None:
            log.error("WorkflowRun %s not found", workflow_run_id)
            return "missing"

        wf = db.query(Workflow).filter_by(id=run.workflow_id).first()
        graph = wf.graph

        # Validation is first-tick only; re-running it every tick would just
        # re-read a graph that cannot have changed.
        if tick == 0 and _begin(db, run, wf, graph) == "invalid":
            return "invalid"

        if get_settings().execution_strategy == "parallel":
            return _tick_parallel(db, run, wf, graph, tick)
        return _run_sequential(db, store, run, wf, graph)

    except Exception as e:
        db.rollback()
        log.exception("Orchestrator crashed for run %s", workflow_run_id)
        run = db.query(WorkflowRun).filter_by(id=workflow_run_id).first()
        if run is not None:
            run.status = "failed"
            run.error = f"orchestrator: {type(e).__name__}: {e}"
            run.finished_at = _utcnow()
            db.commit()
        return "crashed"
    finally:
        db.close()