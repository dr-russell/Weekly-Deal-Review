"""Apply a completed, audited sector reclassification to current deal records."""
import hashlib
import json
import sqlite3
import uuid
from contextlib import closing

from . import store
from .sectors import SECTORS, VERSION


def apply_sector_plan(plan):
    from .pipeline import freeze_prepared
    with store.WRITE_LOCK, store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        if db.execute("SELECT id FROM runs WHERE status='running'").fetchone():
            raise ValueError("Wait for the active workflow before updating sectors")
        rows = [dict(row) for row in db.execute("SELECT * FROM deals ORDER BY id")]
        digest = hashlib.sha256(store.encode(rows).encode()).hexdigest()
        if digest != plan["before_sha256"]:
            raise ValueError("Deal records changed during classification; regenerate the migration plan")
        assignments = plan["assignments"]
        if set(assignments) != {row["id"] for row in rows} or any(item["sector"] not in SECTORS.values() or not item["reason"].strip() for item in assignments.values()):
            raise ValueError("Sector migration coverage or classification is invalid")
        backup = store.DATA / "backups" / ("before-sector-taxonomy-" + store.now().replace(":", "").replace(".", "-") + ".sqlite")
        backup.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(store.DB)) as source, closing(sqlite3.connect(backup)) as destination:
            source.backup(destination)
        at = store.now()
        changes = []
        for row in rows:
            fields = json.loads(row["fields"])
            assignment = assignments[row["id"]]
            old = fields["sector"]
            if old == assignment["sector"]:
                continue
            fields["sector"] = assignment["sector"]
            missing = json.loads(row["missing"])
            missing.pop("sector", None)
            db.execute("UPDATE deals SET fields=?,missing=?,updated_at=? WHERE id=?", (store.encode(fields), store.encode(missing), at, row["id"]))
            db.execute("UPDATE field_overrides SET active=0 WHERE deal_id=? AND field='sector' AND value=?", (row["id"], old))
            changes.append({"deal_id": row["id"], "company": row["company"], "week_id": row["week_id"], "old": old, **assignment})
        for wid in sorted({change["week_id"] for change in changes}):
            week = dict(db.execute("SELECT * FROM weeks WHERE id=?", (wid,)).fetchone())
            report = json.loads(week["report"])
            previous = report.get("prepared_snapshot")
            if week["status"] == "prepared":
                records = [store.unpack(dict(row), ["fields", "block_ids", "sources", "evidence", "missing", "portfolio_match"]) for row in db.execute("SELECT * FROM deals WHERE week_id=? AND active=1 ORDER BY company", (wid,))]
                report["prepared_snapshot"] = freeze_prepared(wid, records, db)
            run_id = uuid.uuid4().hex[:16]
            week_changes = [change for change in changes if change["week_id"] == wid]
            metrics = {"sector_taxonomy": VERSION, "sector_changes": len(week_changes), "input_snapshot": report.get("prepared_snapshot"), "google_updated": False, "fit_decisions_changed": False}
            db.execute("INSERT INTO runs(id,week_id,kind,target,status,stage,started_at,finished_at,elapsed,metrics) VALUES(?,?,?,?,?,?,?,?,?,?)", (run_id, wid, "prepare_normalization", "local", "completed", "prepared", at, at, 0, store.encode(metrics)))
            db.execute("INSERT INTO events(run_id,at,stage,message,detail) VALUES(?,?,?,?,?)", (run_id, at, "normalization", "Sector taxonomy changed to StartHere primary industries", store.encode({"changes": week_changes, "previous_snapshot": previous, **metrics})))
            report["sector_taxonomy"] = VERSION
            report.setdefault("sector_migrations", []).append({"run_id": run_id, "changes": len(week_changes), "at": at})
            db.execute("UPDATE weeks SET report=?,updated_at=? WHERE id=?", (store.encode(report), at, wid))
        result = {"version": VERSION, "changed_records": len(changes), "backup": str(backup), "changes": changes, "fit_decisions_changed": False, "google_updated": False}
        return result
