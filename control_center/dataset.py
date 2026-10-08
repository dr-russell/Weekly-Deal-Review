"""Shared multi-week datasets for preparation, review, and publication."""
import hashlib
import json
import time

from . import store
from .database import dedupe_database
from .logic import normalize_fields, remaining_missing_fields
from .missing_review import missing_summary


def selected_weeks(week_ids):
    ids = list(dict.fromkeys(week_ids))
    if not ids:
        raise ValueError("Choose at least one week")
    rows = store.query("SELECT * FROM weeks WHERE id IN (" + ",".join("?" for _ in ids) + ") ORDER BY start", ids)
    if len(rows) != len(ids):
        raise ValueError("One or more selected weeks do not exist")
    return [store.unpack(row, ["report"]) for row in rows]


def combined_records(weeks, records=None):
    records = records if records is not None else [record for week in weeks for record in store.get_deals(week["id"])]
    by_id = {record["id"]: record for record in records}
    combined = dedupe_database(records)
    unavailable = {"", "unknown", "undisclosed", "not disclosed", "not available", "usd amount unavailable"}
    for record in combined:
        members = [by_id[item["id"]] for item in record["coverage_records"]]
        record["fields"] = normalize_fields(record["fields"])
        for member in members:
            fields = normalize_fields(member["fields"])
            for field, value in fields.items():
                if record["fields"][field].casefold() in unavailable and value.casefold() not in unavailable:
                    record["fields"][field] = value
                    record["missing"].pop(field, None)
        record["block_ids"] = list(dict.fromkeys(bid for member in members for bid in member["block_ids"]))
        record["evidence"] = list({item["id"]: item for member in members for item in member["evidence"]}.values())
    return combined


def dataset_summary(week_ids):
    weeks = selected_weeks(week_ids)
    pending = [week["id"] for week in weeks if week["status"] != "prepared"]
    result = {"week_ids": [week["id"] for week in weeks], "pending_weeks": pending, "status": "raw" if pending else "prepared", "deal_count": None, "duplicate_mentions": None, "remaining_missing_count": None, "remaining_missing_fields": []}
    if not pending:
        records = combined_records(weeks)
        mentions = sum(week["report"].get("deal_mentions", len(store.get_deals(week["id"])) + week["report"].get("duplicate_mentions", 0)) for week in weeks)
        progress = missing_summary(records)
        missing = progress["remaining_missing_fields"] + progress["addressed_missing_fields"]
        by_id = {record["id"]: record for record in records}
        for item in missing:
            item["week_id"] = by_id[item["deal_id"]]["week_id"]
        result.update(deal_count=len(records), duplicate_mentions=max(0, mentions - len(records)), **progress)
    return result


def freeze_selection(run_id, label, value):
    payload = store.encode(value)
    digest = hashlib.sha256(payload.encode()).hexdigest()
    folder = store.DATA / "prepared"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / (run_id + "-" + label + "-" + digest[:20] + ".json")
    if not path.exists():
        path.write_text(payload)
    return {"path": str(path.relative_to(store.DATA)), "sha256": digest, "at": store.now()}


def review_selection(run_id, week_ids):
    from .pipeline import prepared_deals
    from .fx import prepared_rates
    weeks = selected_weeks(week_ids)
    if any(week["status"] != "prepared" for week in weeks):
        raise ValueError("Prepare every selected week before reviewing")
    records, inputs, rates, observations = [], [], [], {}
    ratebook = {}
    for week in weeks:
        values, manifest = prepared_deals(week["id"])
        for value in values:
            value["week_id"] = week["id"]
        records.extend(values)
        inputs.append({"week_id": week["id"], "manifest": manifest})
        book, manifest = prepared_rates(week["id"])
        rates.append({"week_id": week["id"], "manifest": manifest})
        if book:
            ratebook = {**ratebook, **book}
            observations.update({row["d"]: row for row in book.get("observations", [])})
    if ratebook:
        ratebook["observations"] = [observations[day] for day in sorted(observations)]
        ratebook["start"] = min(observations) if observations else weeks[0]["start"]
    records = combined_records(weeks, records)
    input_manifest = {**freeze_selection(run_id, "inputs", records), "companies": len(records), "source_snapshots": inputs}
    rates_manifest = {**freeze_selection(run_id, "rates", ratebook), "source_snapshots": rates}
    periods = [{key: week[key] for key in ["id", "start", "end", "label"]} for week in weeks]
    context = {"week_ids": [week["id"] for week in weeks], "selection_key": "|".join(sorted(week["id"] for week in weeks)), "prepared_versions": {week["id"]: week["updated_at"] for week in weeks}, "selection_weeks": periods}
    return {"start": weeks[0]["start"], "end": weeks[-1]["end"], "updated_at": weeks[0]["updated_at"]}, records, input_manifest, ratebook, rates_manifest, context


def add_selection_package(package, deals, decisions, periods):
    from .logic import make_package
    package["selection_weeks"] = periods
    package["week_stats"] = []
    for period in periods:
        stats = make_package(deals, decisions, period["start"], period["end"], package["fx_rates"])["stats"]
        package["week_stats"].append({**period, "count": stats["count"], "capital_usd_m": stats["capital_usd_m"]})
    return package


def prepare_selected(run_id, week_ids, collect=True):
    from .pipeline import prepare
    started = time.perf_counter()
    ids = [week["id"] for week in selected_weeks(week_ids)]
    try:
        for wid in ids:
            week = selected_weeks([wid])[0]
            if week["status"] == "prepared":
                continue
            metrics = {"week_ids": ids, "current_week": wid}
            store.update_run(run_id, metrics=metrics)
            store.event(run_id, "preparation", "Preparing " + week["label"], metrics)
            prepare(run_id, wid, collect=collect, finalize=False)
        summary = dataset_summary(ids)
        if summary["pending_weeks"]:
            raise ValueError("Some selected weeks still need preparation")
        payload = store.encode(combined_records(selected_weeks(ids)))
        digest = hashlib.sha256(payload.encode()).hexdigest()
        folder = store.DATA / "prepared"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / ("batch-" + run_id + "-" + digest[:20] + ".json")
        path.write_text(payload)
        manifest = {"path": str(path.relative_to(store.DATA)), "sha256": digest, "companies": summary["deal_count"], "at": store.now()}
        metrics = {**summary, "unique_deals": summary["deal_count"], "missing_fields": len(remaining_missing_fields(combined_records(selected_weeks(ids)))), "input_snapshot": manifest}
        store.event(run_id, "prepared", "Selected weeks prepared", {"week_ids": ids, "unique_deals": summary["deal_count"]})
        store.update_run(run_id, status="completed", stage="prepared", finished_at=store.now(), elapsed=time.perf_counter() - started, metrics=metrics)
    except Exception as error:
        store.event(run_id, "failed", str(error), {"week_ids": ids})
        store.update_run(run_id, status="failed", finished_at=store.now(), elapsed=time.perf_counter() - started, error=str(error), metrics={"week_ids": ids, "pending_weeks": dataset_summary(ids)["pending_weeks"]})
