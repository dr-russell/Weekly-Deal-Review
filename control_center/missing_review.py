"""User acknowledgments of missing cells, separate from actual data completeness."""
from . import store
from .logic import remaining_missing_fields


def missing_summary(records):
    marks = {(row["deal_id"], row["field"]) for row in store.query("SELECT deal_id,field FROM addressed_missing_fields")}
    by_id = {record["id"]: record for record in records}
    remaining, addressed = [], []
    for item in remaining_missing_fields(records):
        record = by_id[item["deal_id"]]
        ids = [member["id"] for member in record.get("coverage_records", [])] or [record["id"]]
        item["addressed"] = any((deal_id, item["field"]) in marks for deal_id in ids)
        (addressed if item["addressed"] else remaining).append(item)
    return {"remaining_missing_fields": remaining, "remaining_missing_count": len(remaining),
            "addressed_missing_fields": addressed}


def set_addressed(deal_id, field, addressed, week_ids):
    from .dataset import combined_records, selected_weeks
    records = combined_records(selected_weeks(week_ids))
    record = next((record for record in records if record["id"] == deal_id), None)
    if record is None:
        raise ValueError("Deal not found in selected weeks")
    if not any(item["field"] == field for item in remaining_missing_fields([record])):
        raise ValueError("This field is no longer missing")
    ids = [member["id"] for member in record.get("coverage_records", [])] or [deal_id]
    with store.WRITE_LOCK, store.connection() as db:
        for member_id in ids:
            if addressed:
                db.execute("INSERT OR REPLACE INTO addressed_missing_fields(deal_id,field,at) VALUES(?,?,?)", (member_id, field, store.now()))
            else:
                db.execute("DELETE FROM addressed_missing_fields WHERE deal_id=? AND field=?", (member_id, field))
    return {"addressed": addressed}
