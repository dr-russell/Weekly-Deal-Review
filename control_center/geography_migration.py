"""Audited backfill of location formatting in the current deal database."""
import json
import sqlite3
import uuid
from contextlib import closing

from . import store
from .geography import normalize_geography


def normalize_database(week_id=None, apply=False):
    from .pipeline import freeze_prepared, prepared_deals

    with store.WRITE_LOCK, store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        if db.execute("SELECT id FROM runs WHERE status='running'").fetchone():
            raise ValueError("Wait for the active workflow before normalizing geography")
        rows = db.execute("SELECT * FROM deals" + (" WHERE week_id=?" if week_id else ""), (week_id,) if week_id else ()).fetchall()
        changes = []
        for row in rows:
            fields = json.loads(row["fields"])
            old = fields.get("geography", "")
            value = normalize_geography(old)
            if value != old:
                changes.append({"deal_id": row["id"], "company": row["company"], "week_id": row["week_id"], "active": bool(row["active"]), "old": old, "value": value})
        result = {"applied": apply, "changed_deals": len(changes), "changed_active_deals": sum(c["active"] for c in changes), "changes": changes, "google_updated": False}
        if not apply or not changes:
            return result
        # Backup from a separate reader while this transaction prevents writes.
        backup = store.DATA / "backups" / ("before-geography-normalization-" + store.now().replace(":", "").replace(".", "-") + ".sqlite")
        backup.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(store.DB)) as source, closing(sqlite3.connect(backup)) as destination:
            source.backup(destination)
        result["backup"] = str(backup)
        at = store.now()
        for wid in sorted({c["week_id"] for c in changes}):
            week = db.execute("SELECT * FROM weeks WHERE id=?", (wid,)).fetchone()
            week_changes = [c for c in changes if c["week_id"] == wid]
            run_id = uuid.uuid4().hex[:16]
            previous, snapshot = None, None
            if db.execute("SELECT value FROM settings WHERE key=?", ("prepared:" + wid,)).fetchone():
                records, previous = prepared_deals(wid)
                active = {r["id"]: json.loads(r["fields"]) for r in db.execute("SELECT id,fields FROM deals WHERE week_id=? AND active=1", (wid,))}
                if len(records) != len(active) or {d["id"] for d in records} != set(active):
                    raise ValueError("Prepared snapshot does not match current deals for " + wid)
                for record in records:
                    if record["fields"] != active[record["id"]]:
                        raise ValueError("Prepared fields do not match current deals for " + wid)
                    record["fields"]["geography"] = normalize_geography(record["fields"].get("geography", ""))
                snapshot = freeze_prepared(wid, records, db)
            for change in week_changes:
                row = next(r for r in rows if r["id"] == change["deal_id"])
                fields = json.loads(row["fields"])
                fields["geography"] = change["value"]
                db.execute("UPDATE deals SET fields=?,updated_at=? WHERE id=?", (store.encode(fields), at, row["id"]))
            detail = {"policy": "Full state/province and country names; country-scoped region aliases; no inferred locations", "changes": week_changes, "previous_snapshot": previous, "new_snapshot": snapshot, "google_updated": False}
            db.execute("INSERT INTO runs(id,week_id,kind,target,status,stage,started_at,finished_at,elapsed,metrics) VALUES(?,?,?,?,?,?,?,?,?,?)", (run_id, wid, "prepare_normalization", "local", "completed", "prepared", at, at, 0, store.encode({"geography_normalizations": len(week_changes), "input_snapshot": snapshot, "google_updated": False})))
            db.execute("INSERT INTO events(run_id,at,stage,message,detail) VALUES(?,?,?,?,?)", (run_id, at, "normalization", "State/province and country names standardized", store.encode(detail)))
            report = json.loads(week["report"])
            report.setdefault("geography_normalizations", []).append({"run_id": run_id, "changed_deals": len(week_changes), "at": at})
            if snapshot:
                report["prepared_snapshot"] = snapshot
            db.execute("UPDATE weeks SET report=?,updated_at=? WHERE id=?", (store.encode(report), at, wid))
        return result
