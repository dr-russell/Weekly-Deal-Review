import json
import os
import threading
from contextlib import asynccontextmanager
from datetime import date
from typing import Literal

from fastapi import FastAPI, HTTPException, Request, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import store
from .config import GOOGLE_TOKEN, MODEL, ROOT, TARGETS, TAXONOMY
from .google import Publisher
from .logic import completed_week, normalized_name, normalized_usd_amount, sheet_amount, normalize_round, remaining_missing_fields
from .pipeline import import_history, register_week, run_job
from .missing_review import missing_summary

JOB_LOCK = threading.Lock()


@asynccontextmanager
async def lifespan(app):
    store.initialize()
    import_history()
    start, end = completed_week()
    register_week(start, end, f"{start:%b %d} - {end:%b %d, %Y}")
    yield


app = FastAPI(title="MetaProp Deal Control Center", lifespan=lifespan)
app.state.workspace = {"name": "Local workspace", "allow_preparation": True, "targets": ["test", "local", "production"], "actions": []}
app.state.before_job = None


@app.middleware("http")
async def local_only(request: Request, call_next):
    host = request.headers.get("host", "").split(":")[0]
    origin = request.headers.get("origin")
    if host not in {"localhost", "127.0.0.1", "testserver"} or origin and origin.split("://")[-1].split(":")[0] not in {"localhost", "127.0.0.1", "testserver"}:
        from fastapi.responses import JSONResponse
        return JSONResponse({"detail": "Local access only"}, status_code=403)
    return await call_next(request)


@app.get("/api/overview")
def overview():
    weeks = store.query("SELECT w.*, (SELECT COUNT(*) FROM deals d WHERE d.week_id=w.id AND d.active=1) AS deal_count, (SELECT COUNT(*) FROM blocks b WHERE b.week_id=w.id) AS block_count, (SELECT COUNT(DISTINCT message_id) FROM blocks b WHERE b.week_id=w.id) AS email_count FROM weeks w ORDER BY end DESC")
    for w in weeks:
        store.unpack(w, ["report"])
        w["sources"] = store.query("SELECT source,COUNT(DISTINCT message_id) AS emails,COUNT(*) AS source_items FROM blocks WHERE week_id=? GROUP BY source ORDER BY source", (w["id"],))
        w.update(missing_summary(store.get_deals(w["id"])))
    active = store.query("SELECT id,status,kind,week_id,stage FROM runs WHERE status='running' ORDER BY started_at DESC LIMIT 1")
    return {"weeks": weeks, "totals": {"deals": store.query("SELECT COUNT(*) AS n FROM deals WHERE active=1")[0]["n"], "emails": store.query("SELECT COUNT(*) AS n FROM messages")[0]["n"], "source_items": store.query("SELECT COUNT(*) AS n FROM blocks")[0]["n"]}, "active_run": active[0] if active else None, "model": MODEL, "targets": TARGETS, "google_authorized": GOOGLE_TOKEN.exists(), "production_enabled": os.environ.get("DEAL_ALLOW_PRODUCTION") == "1", "workspace": app.state.workspace, "field_options": {"sector": TAXONOMY, "growth_stage": ["Early", "Growth"], "round": ["Pre-seed", "Seed"] + ["Series " + chr(n) for n in range(ord("A"), ord("Z")+1)] + ["Early", "Growth"]}}


@app.get("/api/runs")
def runs():
    return [store.unpack(r, ["metrics", "timings"]) for r in store.query("SELECT r.id,r.week_id,r.kind,r.target,r.status,r.stage,r.started_at,r.finished_at,r.elapsed,r.timings,r.metrics,r.error,(SELECT json_extract(b.payload,'$.metrics.count') FROM review_baselines b WHERE b.run_id=r.id) AS historical_selected_count FROM runs r ORDER BY r.started_at DESC LIMIT 100")]


@app.get("/api/runs/{run_id}")
def run_detail(run_id: str):
    try:
        return store.get_run(run_id)
    except KeyError:
        raise HTTPException(404, "Run not found")


@app.get("/api/runs/{run_id}/history")
def run_history_detail(run_id: str):
    from .history import historical_run
    try:
        result = historical_run(run_id)
        result.pop("snapshot_records")
        return result
    except KeyError:
        raise HTTPException(404, "Run not found")


@app.get("/api/runs/{run_id}/deals/{deal_id}")
def run_company_detail(run_id: str, deal_id: str):
    from .history import historical_deal
    try:
        return historical_deal(run_id, deal_id)
    except KeyError as error:
        raise HTTPException(404, str(error).strip("'"))


@app.get("/api/weeks/{week_id}/latest-review")
def latest_review(week_id: str):
    rows = store.query("SELECT id FROM runs WHERE week_id=? AND kind='review' AND status IN ('completed','local_completed') AND json_extract(metrics,'$.selection_key') IS NULL ORDER BY started_at DESC LIMIT 1", (week_id,))
    return store.get_run(rows[0]['id']) if rows else None


@app.get("/api/dataset/latest-review")
def latest_dataset_review(week_ids: list[str] = Query(...)):
    from .dataset import selected_weeks
    try:
        weeks = selected_weeks(week_ids)
    except ValueError as error:
        raise HTTPException(422, str(error))
    if len(weeks)==1:
        return latest_review(weeks[0]["id"])
    key = "|".join(sorted(week["id"] for week in weeks))
    rows = store.query("SELECT id FROM runs WHERE kind='review' AND status IN ('completed','local_completed') AND json_extract(metrics,'$.selection_key')=? ORDER BY started_at DESC LIMIT 1", (key,))
    return store.get_run(rows[0]["id"]) if rows else None


@app.get("/api/deals")
def deals(week: str = "All", q: str = "", fit: str = "All"):
    if week == "All":
        from .database import database_matches, dedupe_database
        weeks = store.query("SELECT id FROM weeks ORDER BY end DESC")
        values = dedupe_database([value for item in weeks for value in deals(item["id"])])
        return [value for value in values if database_matches(value, q, fit)]
    values = store.get_deals(week)
    from .fx import prepared_rates
    ratebook, _ = prepared_rates(week)
    latest = store.query("SELECT id FROM runs r WHERE kind='review' AND status IN ('completed','local_completed') AND (week_id=? OR EXISTS(SELECT 1 FROM json_each(r.metrics,'$.week_ids') WHERE value=?)) ORDER BY started_at DESC LIMIT 1", (week,week))
    decisions = {r["deal_id"]: store.unpack(r, ["first_pass", "verification", "final"]) for r in store.query("SELECT * FROM decisions WHERE run_id=?", (latest[0]["id"],))} if latest else {}
    from .corrections import user_decision
    feedback = {r["deal_id"]: r for r in store.query("SELECT f.* FROM feedback f JOIN deals d ON d.id=f.deal_id WHERE d.week_id=? AND f.active=1 ORDER BY f.at", (week,))}
    result = []
    for value in values:
        decision = decisions.get(value["id"])
        corrected = user_decision(decision["final"] if decision else None, feedback.get(value["id"]))
        if corrected:
            decision = {**(decision or {"run_id": None, "first_pass": None, "verification": None}), "final": corrected}
        value["decision"] = decision
        if q.casefold() not in store.encode([value["company"], value["fields"], value["sources"]]).casefold():
            continue
        status = decision["final"]["fit"] if decision else "Unreviewed"
        if fit not in {"All", status}:
            continue
        display, _, audit = normalized_usd_amount(value['fields']['amount'], value['fields']['deal_date'], ratebook)
        value['fields']['amount'] = sheet_amount(display)
        value['fields']['round'] = normalize_round(value['fields']['round'], value['fields']['growth_stage'])
        value['amount_normalization'] = audit
        result.append(value)
    return result


@app.get("/api/dataset/deals")
def dataset_deals(week_ids: list[str] = Query(default=[]), q: str = "", fit: str = "All"):
    from .dataset import selected_weeks
    from .database import database_matches, dedupe_database
    if not week_ids:
        return []
    try:
        weeks = selected_weeks(week_ids)
    except ValueError as error:
        raise HTTPException(422, str(error))
    records = dedupe_database([record for week in weeks for record in deals(week["id"])])
    return [record for record in records if database_matches(record, q, fit)]


@app.get("/api/deals/{deal_id}")
def deal_detail(deal_id: str, run_id: str | None = None):
    rows = store.query("SELECT * FROM deals WHERE id=?", (deal_id,))
    if not rows:
        raise HTTPException(404, "Company not found")
    value = store.unpack(rows[0], ["fields", "block_ids", "sources", "evidence", "missing", "portfolio_match"])
    if run_id:
        snapshot = store.query("SELECT record FROM review_records WHERE run_id=? AND deal_id=?", (run_id, deal_id))
        if snapshot:
            value.update(json.loads(snapshot[0]["record"]))
    from .pipeline import source_evidence
    value["source_evidence"] = source_evidence(value)
    value["decisions"] = [store.unpack(r, ["first_pass", "verification", "final"]) for r in store.query("SELECT d.*,r.started_at FROM decisions d JOIN runs r ON d.run_id=r.id WHERE deal_id=? ORDER BY r.started_at DESC", (deal_id,))]
    value["feedback"] = store.query("SELECT * FROM feedback WHERE deal_id=? ORDER BY at DESC", (deal_id,))
    value["field_history"] = store.query("SELECT field,old_value,value,source_url,evidence_id,reason,at,active FROM field_overrides WHERE deal_id=? ORDER BY at DESC", (deal_id,))
    value["editable_fields"] = dict(value["fields"])
    week = store.query("SELECT status,updated_at FROM weeks WHERE id=?", (value["week_id"],))[0]
    from .corrections import latest_record_review, prepared_versions_match
    with store.connection() as db:
        latest_row = latest_record_review(db, deal_id)
        latest = [dict(latest_row)] if latest_row else []
        value["editable_run_id"] = latest[0]["id"] if latest and prepared_versions_match(db, latest[0]) else None
    value["editable"] = bool(value.get("active", True) and week["status"] == "prepared" and (not run_id or run_id == value["editable_run_id"]))
    if run_id:
        value["decisions"].sort(key=lambda d: d["run_id"] != run_id)
    elif latest:
        value["decisions"].sort(key=lambda d: d["run_id"] != latest[0]["id"])
    value["decision_current"] = bool(value["decisions"] and value["decisions"][0]["run_id"] == value["editable_run_id"])
    value["pending_recheck"] = bool(latest and value["editable_run_id"] and deal_id in json.loads(latest[0]["metrics"]).get("pending_rechecks", []))
    from .corrections import user_decision
    previous = value["decisions"][0] if value["decisions"] else None
    final = user_decision(previous["final"] if previous else None, next((f for f in value["feedback"] if f["active"]), None)) if not run_id else (previous["final"] if previous else None)
    value["review_decision"] = {**(previous or {"run_id": None, "first_pass": None, "verification": None}), "final": final} if final else None
    value["editable_decision"] = value["editable"] and value["portfolio_match"].get("status") != "known" and not (final or {}).get("known_investment")
    from .fx import prepared_rates
    ratebook, _ = prepared_rates(value['week_id'])
    display, _, audit = normalized_usd_amount(value['fields']['amount'], value['fields']['deal_date'], ratebook)
    value['fields']['amount'] = sheet_amount(display)
    value['fields']['round'] = normalize_round(value['fields']['round'], value['fields']['growth_stage'])
    value['amount_normalization'] = audit
    return value


class Job(BaseModel):
    week_id: str
    kind: Literal["prepare", "review"]
    target: Literal["test", "local", "production"] = "test"
    collect: bool = False
    week_ids: list[str] | None = Field(default=None, min_length=1)


class DatasetJob(BaseModel):
    week_ids: list[str] = Field(min_length=1)
    collect: bool = True


@app.get("/api/dataset")
def selected_dataset(week_ids: list[str] = Query(...)):
    from .dataset import dataset_summary
    try:
        return dataset_summary(week_ids)
    except ValueError as error:
        raise HTTPException(422, str(error))


@app.post("/api/dataset/prepare")
def prepare_dataset(item: DatasetJob):
    from .dataset import dataset_summary, prepare_selected
    if not app.state.workspace["allow_preparation"]:
        raise HTTPException(403, "Dataset preparation is disabled in this workspace")
    if not JOB_LOCK.acquire(blocking=False):
        raise HTTPException(409, "Another workflow is running")
    try:
        if store.query("SELECT id FROM runs WHERE status='running'"):
            raise HTTPException(409, "Another workflow is running")
        try:
            summary = dataset_summary(item.week_ids)
        except ValueError as error:
            raise HTTPException(422, str(error))
        if not summary["pending_weeks"]:
            JOB_LOCK.release()
            return {"run_id": None, "prepared": True}
        run_id = store.new_run(summary["pending_weeks"][0], "prepare_batch", "local")
        store.update_run(run_id, metrics={"week_ids": summary["week_ids"]})
    except Exception:
        JOB_LOCK.release()
        raise
    def worker():
        try:
            prepare_selected(run_id, summary["week_ids"], item.collect)
        finally:
            JOB_LOCK.release()
    threading.Thread(target=worker, daemon=False, name="dataset-preparation").start()
    return {"run_id": run_id, "accepted_at": store.now()}


@app.post("/api/jobs")
def start_job(job: Job):
    if job.target not in app.state.workspace["targets"]:
        raise HTTPException(403, "That publication destination is disabled in this workspace")
    if job.kind == "prepare" and not app.state.workspace["allow_preparation"]:
        raise HTTPException(403, "Dataset preparation is disabled in this workspace")
    selection = None
    if job.week_ids:
        from .dataset import selected_weeks
        if job.kind != "review":
            raise HTTPException(422, "Use dataset preparation for multiple weeks")
        try:
            selection = selected_weeks(job.week_ids)
        except ValueError as error:
            raise HTTPException(422, str(error))
        if any(item["status"] != "prepared" for item in selection):
            raise HTTPException(409, "Prepare every selected week before reviewing")
        job = job.model_copy(update={"week_id": selection[0]["id"]})
    week = store.query("SELECT * FROM weeks WHERE id=?", (job.week_id,))
    if not week:
        raise HTTPException(404, "Week not found")
    if job.target == "production" and os.environ.get("DEAL_ALLOW_PRODUCTION") != "1":
        raise HTTPException(403, "Production is disabled")
    if job.kind == "review" and week[0]["status"] != "prepared":
        raise HTTPException(409, "Prepare this week before reviewing")
    if not JOB_LOCK.acquire(blocking=False):
        raise HTTPException(409, "Another workflow is running")
    run_id = None
    try:
        run_id = store.new_run(job.week_id, job.kind, job.target)
        if selection and len(selection)>1:
            ids = [item["id"] for item in selection]
            store.update_run(run_id, metrics={"week_ids": ids, "selection_key": "|".join(sorted(ids))})
        if app.state.before_job:
            for item in selection or week:
                app.state.before_job(job.model_copy(update={"week_id": item["id"], "week_ids": None}))
    except Exception as error:
        if run_id:
            store.update_run(run_id, status="failed", finished_at=store.now(), error=str(getattr(error, "detail", error)))
        JOB_LOCK.release()
        raise
    def worker():
        try:
            if selection and len(selection)>1:
                run_job(run_id, job.week_id, job.kind, job.target, job.collect, week_ids=[item["id"] for item in selection])
            else:
                run_job(run_id, job.week_id, job.kind, job.target, job.collect)
        finally:
            JOB_LOCK.release()
    threading.Thread(target=worker, daemon=False, name="deal-workflow").start()
    return {"run_id": run_id, "accepted_at": store.now()}


class Week(BaseModel):
    start: date
    end: date


@app.post("/api/weeks")
def new_week(week: Week):
    if (week.end - week.start).days != 6 or week.start.weekday() != 2:
        raise HTTPException(422, "MetaProp weeks must be Wednesday through Tuesday")
    return {"week_id": register_week(week.start, week.end)}


class DecisionCorrection(BaseModel):
    fit: Literal["Yes", "No"]
    reason: str = Field(default="", max_length=3000)
    source_url: str = Field(default="", max_length=2000)
    evidence_id: str = ""
    expected_final: dict
    publish_outputs: bool = False


class FieldCorrection(BaseModel):
    changes: dict[str, str]
    reason: str = Field(default="", max_length=3000)
    source_url: str = ""
    evidence_id: str = ""
    expected_fields: dict[str, str]
    run_id: str | None = None


class RecordDecisionCorrection(DecisionCorrection):
    expected_final: dict | None = None
    expected_fields: dict[str, str]


class MissingFieldAddressed(BaseModel):
    field: str
    addressed: bool
    week_ids: list[str]


@app.post("/api/deals/{deal_id}/missing-addressed")
def missing_field_addressed(deal_id: str, item: MissingFieldAddressed):
    from .missing_review import set_addressed
    if not JOB_LOCK.acquire(blocking=False):
        raise HTTPException(409, "Another workflow is running")
    try:
        return set_addressed(deal_id, item.field, item.addressed, item.week_ids)
    except ValueError as error:
        raise HTTPException(422, str(error))
    finally:
        JOB_LOCK.release()


@app.post("/api/deals/{deal_id}/decision")
def record_decision_correction(deal_id: str, item: RecordDecisionCorrection):
    from .corrections import CorrectionError, correct_record_decision
    if not JOB_LOCK.acquire(blocking=False):
        raise HTTPException(409, "Another workflow is running")
    try:
        return correct_record_decision(deal_id, item.fit, item.reason, item.source_url, item.evidence_id, item.expected_final, item.expected_fields)
    except CorrectionError as error:
        raise HTTPException(error.status, str(error))
    finally:
        JOB_LOCK.release()


@app.post("/api/deals/{deal_id}/correction")
def field_correction(deal_id: str, item: FieldCorrection):
    from .corrections import CorrectionError, correct_fields
    if not JOB_LOCK.acquire(blocking=False):
        raise HTTPException(409, "Another workflow is running")
    try:
        return correct_fields(deal_id, item.changes, item.reason, item.source_url, item.evidence_id, item.expected_fields, item.run_id)
    except CorrectionError as error:
        raise HTTPException(error.status, str(error))
    finally:
        JOB_LOCK.release()


@app.post("/api/runs/{run_id}/recheck/{deal_id}")
def recheck(run_id: str, deal_id: str):
    from .corrections import CorrectionError, recheck_company
    if not JOB_LOCK.acquire(blocking=False):
        raise HTTPException(409, "Another workflow is running")
    try:
        return recheck_company(run_id, deal_id)
    except CorrectionError as error:
        raise HTTPException(error.status, str(error))
    except Exception as error:
        raise HTTPException(502, str(error))
    finally:
        JOB_LOCK.release()


@app.post("/api/runs/{run_id}/decisions/{deal_id}/correction")
def decision_correction(run_id: str, deal_id: str, item: DecisionCorrection):
    from .corrections import CorrectionError, correct_decision
    if not JOB_LOCK.acquire(blocking=False):
        raise HTTPException(409, "Another workflow is running")
    try:
        return correct_decision(run_id, deal_id, item.fit, item.reason, item.expected_final, item.source_url, item.evidence_id, publish_outputs=item.publish_outputs)
    except CorrectionError as error:
        raise HTTPException(error.status, str(error))
    finally:
        JOB_LOCK.release()


@app.post("/api/runs/{run_id}/publish-corrections")
def refresh_corrected_outputs(run_id: str):
    from .corrections import CorrectionError, publish_corrections
    rows = store.query("SELECT target FROM runs WHERE id=?", (run_id,))
    if rows and rows[0]["target"] == "production" and os.environ.get("DEAL_ALLOW_PRODUCTION") != "1":
        raise HTTPException(403, "Production is disabled")
    if not JOB_LOCK.acquire(blocking=False):
        raise HTTPException(409, "Another workflow is running")
    try:
        return publish_corrections(run_id)
    except CorrectionError as error:
        raise HTTPException(error.status, str(error))
    except Exception as error:
        raise HTTPException(502, str(error))
    finally:
        JOB_LOCK.release()


@app.get("/api/health/google")
def google_health():
    try:
        from .google import linked_charts
        snapshot = Publisher("test").snapshot()
        charts = linked_charts(snapshot["doc"])
        mismatched = [value for value in charts.values() if value["spreadsheetId"] != TARGETS["test"]["sheet"]]
        return {"ready": not mismatched, "file_access": True, "sheet": snapshot["meta"]["properties"]["title"], "doc": snapshot["doc"]["title"], "linked_charts": len(charts), "error": "Demo Doc charts still reference a different Sheet; relink them before a timed run" if mismatched else None}
    except Exception as error:
        return {"ready": False, "error": str(error)}


@app.get("/api/evaluation")
def evaluation():
    gold = []
    for filename in ["tri_state_diagnostic.json", "agreement_sample.json"]:
        from .config import PROJECT
        path = PROJECT / "benchmark" / filename
        if path.exists():
            gold.extend(json.loads(path.read_text())["cases"])
    weeks = store.query("SELECT * FROM weeks ORDER BY start")
    results, counts = [], {"labeled": len(gold), "scored": 0, "missing": 0, "extraction_missing": 0, "first_correct": 0, "final_correct": 0, "tp": 0, "fn": 0, "fp": 0, "tn": 0}
    for case in gold:
        start = date(2026, 7, 15) + __import__("datetime").timedelta(days=(case["week"] - 1) * 7)
        week = next((w for w in weeks if w["start"] == start.isoformat()), None)
        run = store.query("SELECT id,package FROM runs WHERE week_id=? AND kind='review' AND status IN ('completed','local_completed') ORDER BY started_at DESC LIMIT 1", (week["id"],)) if week else []
        decisions = store.query("SELECT x.company,d.first_pass,d.final FROM decisions d JOIN deals x ON d.deal_id=x.id WHERE d.run_id=?", (run[0]["id"],)) if run else []
        predicted = next((d for d in decisions if normalized_name(d["company"]) == normalized_name(case["company"])), None)
        if not run:
            counts["missing"] += 1
            results.append({**case, "status": "unscored", "reason_unscored": "No completed review for this week"})
            continue
        if not predicted:
            counts["scored"] += 1
            counts["extraction_missing"] += 1
            counts["fn" if case["fit"] != "No" else "tn"] += 1
            results.append({**case, "status": "extraction_missing", "correct": False, "run_id": run[0]["id"], "reason_unscored": "Company absent from the completed run; relevant missing companies count as recall failures"})
            continue
        first, saved_final = json.loads(predicted["first_pass"]), json.loads(predicted["final"])
        final = saved_final.get("original_model_final", saved_final.get("manual_override", {}).get("model_final", saved_final))
        counts["scored"] += 1
        expected_final = "No" if case["fit"] == "No" else "Yes"
        binary_policy = json.loads(run[0]["package"] or "{}").get("policy", "").startswith("Final binary")
        first_fit = "No" if first["fit"] == "No" or first["known_investment"] else "Yes"
        counts["first_correct"] += first_fit == expected_final if binary_policy else first["fit"] == case["fit"]
        correct = final["fit"] == (expected_final if binary_policy else case["fit"])
        counts["final_correct"] += correct
        actual, expected = final["fit"] != "No" and not final["known_investment"], case["fit"] != "No"
        counts["tp" if actual and expected else "fn" if expected else "fp" if actual else "tn"] += 1
        results.append({**case, "expected_final": expected_final if binary_policy else case["fit"], "first": first["fit"], "final": final["fit"], "correct": correct, "run_id": run[0]["id"]})
    counts["precision"] = counts["tp"] / (counts["tp"] + counts["fp"]) if counts["tp"] + counts["fp"] else None
    counts["recall"] = counts["tp"] / (counts["tp"] + counts["fn"]) if counts["tp"] + counts["fn"] else None
    return {"counts": counts, "cases": results, "limitation": "80 historical diagnostic labels, partly selected after model disagreement. Not a blind or representative accuracy estimate. Weeks without a completed run are unscored; relevant companies missing from completed runs count as recall failures."}


WEB = ROOT / "web/dist"
if WEB.exists():
    app.mount("/assets", StaticFiles(directory=WEB / "assets"), name="assets")


@app.get("/{path:path}")
def frontend(path: str):
    index = WEB / "index.html"
    if not index.exists():
        raise HTTPException(503, "Frontend not built. Run npm run build in control_center/web")
    return FileResponse(index)
