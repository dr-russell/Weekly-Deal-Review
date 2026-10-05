import json
import os
import threading
from contextlib import asynccontextmanager
from datetime import date
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import store
from .config import GOOGLE_TOKEN, MODEL, ROOT, TARGETS
from .google import Publisher
from .logic import completed_week, normalized_name, normalized_usd_amount, sheet_amount, normalize_round
from .pipeline import import_history, register_week, run_job

JOB_LOCK = threading.Lock()


@asynccontextmanager
async def lifespan(app):
    store.initialize()
    import_history()
    start, end = completed_week()
    register_week(start, end, f"{start:%b %d} - {end:%b %d, %Y}")
    yield


app = FastAPI(title="MetaProp Deal Control Center", lifespan=lifespan)


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
    active = store.query("SELECT id,status,kind,week_id,stage FROM runs WHERE status='running' ORDER BY started_at DESC LIMIT 1")
    return {"weeks": weeks, "totals": {"deals": store.query("SELECT COUNT(*) AS n FROM deals WHERE active=1")[0]["n"], "emails": store.query("SELECT COUNT(*) AS n FROM messages")[0]["n"], "source_items": store.query("SELECT COUNT(*) AS n FROM blocks")[0]["n"]}, "active_run": active[0] if active else None, "model": MODEL, "targets": TARGETS, "google_authorized": GOOGLE_TOKEN.exists(), "production_enabled": os.environ.get("DEAL_ALLOW_PRODUCTION") == "1"}


@app.get("/api/runs")
def runs():
    return [store.unpack(r, ["metrics", "timings"]) for r in store.query("SELECT id,week_id,kind,target,status,stage,started_at,finished_at,elapsed,timings,metrics,error FROM runs ORDER BY started_at DESC LIMIT 100")]


@app.get("/api/runs/{run_id}")
def run_detail(run_id: str):
    try:
        return store.get_run(run_id)
    except KeyError:
        raise HTTPException(404, "Run not found")


@app.get("/api/deals")
def deals(week: str, q: str = "", fit: str = "All"):
    values = store.get_deals(week)
    from .fx import prepared_rates
    ratebook, _ = prepared_rates(week)
    latest = store.query("SELECT id FROM runs WHERE week_id=? AND kind='review' ORDER BY started_at DESC LIMIT 1", (week,))
    decisions = {r["deal_id"]: store.unpack(r, ["first_pass", "verification", "final"]) for r in store.query("SELECT * FROM decisions WHERE run_id=?", (latest[0]["id"],))} if latest else {}
    result = []
    for value in values:
        decision = decisions.get(value["id"])
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


@app.get("/api/deals/{deal_id}")
def deal_detail(deal_id: str):
    rows = store.query("SELECT * FROM deals WHERE id=?", (deal_id,))
    if not rows:
        raise HTTPException(404, "Company not found")
    value = store.unpack(rows[0], ["fields", "block_ids", "sources", "evidence", "missing", "portfolio_match"])
    from .pipeline import source_evidence
    value["source_evidence"] = source_evidence(value)
    value["decisions"] = [store.unpack(r, ["first_pass", "verification", "final"]) for r in store.query("SELECT d.*,r.started_at FROM decisions d JOIN runs r ON d.run_id=r.id WHERE deal_id=? ORDER BY r.started_at DESC", (deal_id,))]
    value["feedback"] = store.query("SELECT * FROM feedback WHERE deal_id=? ORDER BY at DESC", (deal_id,))
    from .fx import prepared_rates
    ratebook, _ = prepared_rates(value['week_id'])
    display, _, audit = normalized_usd_amount(value['fields']['amount'], value['fields']['deal_date'], ratebook)
    value['fields']['amount'] = sheet_amount(display)
    value['fields']['round'] = normalize_round(value['fields']['round'], value['fields']['growth_stage'])
    value['amount_normalization'] = audit
    value['source_evidence'].append({'id':'usd-'+deal_id,'source':'USD conversion audit','text':store.encode(audit)})
    return value


class Job(BaseModel):
    week_id: str
    kind: Literal["prepare", "review"]
    target: Literal["test", "local", "production"] = "test"
    collect: bool = False


@app.post("/api/jobs")
def start_job(job: Job):
    week = store.query("SELECT * FROM weeks WHERE id=?", (job.week_id,))
    if not week:
        raise HTTPException(404, "Week not found")
    if job.target == "production" and os.environ.get("DEAL_ALLOW_PRODUCTION") != "1":
        raise HTTPException(403, "Production is disabled")
    if job.kind == "review" and week[0]["status"] != "prepared":
        raise HTTPException(409, "Prepare this week before reviewing")
    if not JOB_LOCK.acquire(blocking=False):
        raise HTTPException(409, "Another workflow is running")
    try:
        run_id = store.new_run(job.week_id, job.kind, job.target)
    except Exception:
        JOB_LOCK.release()
        raise
    def worker():
        try:
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


class Feedback(BaseModel):
    deal_id: str
    fit: Literal["Yes", "No"]
    reason: str = Field(min_length=5, max_length=3000)


@app.post("/api/feedback")
def feedback(item: Feedback):
    import uuid
    if not store.query("SELECT id FROM deals WHERE id=?", (item.deal_id,)):
        raise HTTPException(404, "Company not found")
    previous = store.query("SELECT final FROM decisions WHERE deal_id=? ORDER BY rowid DESC LIMIT 1", (item.deal_id,))
    fid = uuid.uuid4().hex[:16]
    store.execute("UPDATE feedback SET active=0 WHERE deal_id=?", (item.deal_id,))
    store.execute("INSERT INTO feedback(id,deal_id,fit,reason,previous,at) VALUES(?,?,?,?,?,?)", (fid, item.deal_id, item.fit, item.reason, previous[0]["final"] if previous else None, store.now()))
    return {"id": fid, "saved": True}


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
        gold.extend(json.loads((PROJECT / "benchmark" / filename).read_text())["cases"])
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
        first, final = json.loads(predicted["first_pass"]), json.loads(predicted["final"])
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
