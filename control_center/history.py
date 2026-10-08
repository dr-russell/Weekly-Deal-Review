"""Read historical run records without substituting the current dataset."""
import copy
import hashlib
import json
import sqlite3
from contextlib import closing

from . import store


def historical_run(run_id):
    run = store.get_run(run_id)
    baseline_rows = store.query("SELECT payload FROM review_baselines WHERE run_id=?", (run_id,))
    baseline = json.loads(baseline_rows[0]["payload"]) if baseline_rows else None
    records = baseline.get("deals", []) if baseline else [json.loads(row["record"]) for row in store.query("SELECT record FROM review_records WHERE run_id=?", (run_id,))]
    original = bool(baseline and run["status"] in {"completed", "local_completed"})
    if baseline:
        run["metrics"] = copy.deepcopy(baseline["metrics"])
        run["package"] = copy.deepcopy(baseline["package"])
        for decision in run["decisions"]:
            if decision["deal_id"] in baseline["final"]:
                decision["final"] = copy.deepcopy(baseline["final"][decision["deal_id"]])
    snapshot_reason = "Historical company snapshot unavailable."
    manifest = run["metrics"].get("prepared_snapshot") or run["metrics"].get("input_snapshot")
    # Older reviews may retain only their prepared input manifest. That is an
    # input snapshot, distinct from company fields finalized during the review.
    snapshot_kind = "reviewed" if records else "prepared"
    if not records and isinstance(manifest, dict) and manifest.get("path"):
        path = (store.DATA / manifest["path"]).resolve()
        if path.is_relative_to(store.DATA.resolve()) and path.is_file():
            payload = path.read_text()
            if hashlib.sha256(payload.encode()).hexdigest() == manifest.get("sha256"):
                records = json.loads(payload)
            else:
                snapshot_reason = "Historical snapshot integrity check failed."
    record_names = {record["id"]: record["company"] for record in records}
    for decision in run["decisions"]:
        if decision["deal_id"] in record_names:
            decision["company"] = record_names[decision["deal_id"]]
    # Completion events are written just after finished_at. Split follow-ups by
    # their explicit correction stage or metadata, rather than timestamps alone.
    later = lambda event: event["stage"] == "correction" or event["detail"].get("ai_rerun") is False or (event["stage"] == "complete" and event["detail"].get("google_may_be_partially_updated") is True)
    run["later_events"] = [event for event in run["events"] if later(event)]
    run["events"] = [event for event in run["events"] if not later(event)]
    run["historical"] = {"read_only": True, "result_version": "original_completed" if original else "saved", "snapshot_available": bool(records), "snapshot_kind": snapshot_kind if records else None, "snapshot_count": len(records), "snapshot_message": "Reviewed company records saved with this run." if records and snapshot_kind == "reviewed" else "Prepared company inputs saved with this run." if records else snapshot_reason}
    run["snapshot_records"] = records
    return run


def historical_deal(run_id, deal_id):
    run = historical_run(run_id)
    record = next((record for record in run["snapshot_records"] if record["id"] == deal_id), None)
    if record is None:
        raise KeyError("Historical company snapshot unavailable for this run")
    record = copy.deepcopy(record)
    from .pipeline import source_evidence
    decision = next((decision for decision in run["decisions"] if decision["deal_id"] == deal_id), None)
    record.update(source_evidence=source_evidence(record), decisions=[decision] if decision else [], review_decision=decision, editable_fields=copy.deepcopy(record["fields"]), editable=False, editable_decision=False, editable_run_id=None, decision_current=False, pending_recheck=False, feedback=[], field_history=[], historical=True)
    return record


def clear_history(apply=False, week_id=None):
    """Back up and remove run-owned records; preserve prepared/source data."""
    with store.WRITE_LOCK, store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        if db.execute("SELECT id FROM runs WHERE status='running'").fetchone():
            raise ValueError("Wait for the running workflow before clearing history")
        condition = " WHERE week_id=?" if week_id else ""
        args = (week_id,) if week_id else ()
        ids = "SELECT id FROM runs" + condition
        result = {"applied": apply, "runs": db.execute("SELECT COUNT(*) FROM runs" + condition, args).fetchone()[0]}
        tables = ("decisions", "events", "review_records", "review_baselines")
        result["related_records"] = {table: db.execute(f"SELECT COUNT(*) FROM {table} WHERE run_id IN ({ids})", args).fetchone()[0] for table in tables}
        if not apply or not result["runs"]:
            return result
        backup = store.DATA / "backups" / ("before-clear-history-" + store.now().replace(":", "").replace(".", "-") + ".sqlite")
        backup.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(store.DB)) as source, closing(sqlite3.connect(backup)) as destination:
            source.backup(destination)
        result["backup"] = str(backup)
        for table in tables:
            db.execute(f"DELETE FROM {table} WHERE run_id IN ({ids})", args)
        db.execute("DELETE FROM runs" + condition, args)
        if db.execute("PRAGMA foreign_key_check").fetchone():
            raise ValueError("Database integrity check failed; history removal rolled back")
        return result
