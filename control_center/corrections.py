import json
import time
import uuid

from . import store
from .config import FIELDS, TAXONOMY, PROJECT
from .google import Publisher
from .logic import clean_url, MONEY_OCCURRENCE, make_package, normalize_fields, portfolio_match, remaining_missing_fields, validate_selected

FIT_FIELDS = {"website", "geography", "sector", "description", "growth_stage", "round", "announcement_link"}


def apply_saved_fields(deal):
    for item in store.query("SELECT * FROM field_overrides WHERE deal_id=? AND active=1 ORDER BY at", (deal["id"],)):
        deal["fields"][item["field"]] = item["value"]
        if item["value"]:
            deal["missing"].pop(item["field"], None)
        else:
            deal["missing"][item["field"]] = item["reason"]
        if not any(e["id"] == item["id"] for e in deal["evidence"]):
            deal["evidence"].append({"id": item["id"], "kind": "human", "url": item["source_url"], "summary": item["reason"], "fields": [item["field"]], "at": item["at"]})
    deal["fields"] = normalize_fields(deal["fields"])


def run_records(db, run):
    records = [json.loads(row["record"]) for row in db.execute("SELECT record FROM review_records WHERE run_id=?", (run["id"],))]
    if records:
        return records
    return [store.unpack(dict(row), ["fields", "block_ids", "sources", "evidence", "missing", "portfolio_match"]) for row in db.execute("SELECT * FROM deals WHERE week_id=? AND active=1 ORDER BY company", (run["week_id"],))]


def rebuild_package(run, week, deals, decisions):
    if {deal["id"] for deal in deals} != set(decisions):
        raise CorrectionError("Deal records changed since this review; review the updated week first")
    errors = validate_selected(deals, decisions, week["start"], week["end"])
    if errors:
        raise CorrectionError("Selected-row validation failed: " + "; ".join(errors), 422)
    package = make_package(deals, decisions, week["start"], week["end"], run["package"].get("fx_rates", {}))
    periods = run["package"].get("selection_weeks", [])
    if periods:
        if any(not any(period["start"]<=deal["fields"]["deal_date"]<=period["end"] for period in periods) for deal in deals):
            raise CorrectionError("Financing date falls outside the selected weeks", 422)
        from .dataset import add_selection_package
        add_selection_package(package, deals, decisions, periods)
    selected = [d for d in deals if decisions[d["id"]]["fit"] == "Yes" and not decisions[d["id"]].get("known_investment") and week["start"] <= d["fields"]["deal_date"] <= week["end"]]
    changed_outputs = package["rows"] != run["package"]["rows"] or any(package["stats"][key] != run["package"]["stats"][key] for key in ["count", "capital_usd_m"])
    metrics = {**run["metrics"], **package["stats"], "excluded": sum(d["fit"] == "No" and not d.get("known_investment") for d in decisions.values()), "known_investments": sum(bool(d.get("known_investment")) for d in decisions.values()), "manual_corrections": sum(bool(d.get("manual_override")) for d in decisions.values()), "outputs_dirty": run["target"] != "local" and (run["metrics"].get("outputs_dirty", False) or changed_outputs)}
    metrics["row_completeness"] = {"selected_rows": len(selected), "fields_per_row": len(FIELDS), "populated_fields": sum(bool(d["fields"][k]) for d in selected for k in FIELDS), "documented_unavailable": remaining_missing_fields(selected), "unexplained_blanks": 0}
    return package, metrics


def save_baseline(db, run, deals, decisions):
    db.execute("INSERT OR IGNORE INTO review_baselines(run_id,payload) VALUES(?,?)", (run["id"], store.encode({"deals": deals, "final": decisions, "package": run["package"], "metrics": run["metrics"]})))


def sync_prepared(db, week):
    from .pipeline import freeze_prepared
    deals = [store.unpack(dict(row), ["fields", "block_ids", "sources", "evidence", "missing", "portfolio_match"]) for row in db.execute("SELECT * FROM deals WHERE week_id=? AND active=1 ORDER BY company", (week["id"],))]
    report = json.loads(week["report"])
    report.update(prepared_snapshot=freeze_prepared(week["id"], deals, db), missing_fields=len(remaining_missing_fields(deals)))
    at = store.now()
    db.execute("UPDATE weeks SET report=?,updated_at=? WHERE id=?", (store.encode(report), at, week["id"]))
    return at


class CorrectionError(ValueError):
    def __init__(self, message, status=409):
        super().__init__(message)
        self.status = status


def prepared_versions_match(db, run):
    metrics = run["metrics"] if isinstance(run["metrics"], dict) else json.loads(run["metrics"])
    versions = metrics.get("prepared_versions")
    if versions:
        rows = db.execute("SELECT id,status,updated_at FROM weeks WHERE id IN ("+",".join("?" for _ in versions)+")", list(versions)).fetchall()
        return len(rows)==len(versions) and all(row["status"]=="prepared" and row["updated_at"]==versions[row["id"]] for row in rows)
    week = db.execute("SELECT updated_at FROM weeks WHERE id=?", (run["week_id"],)).fetchone()
    return metrics.get("prepared_at") in {None, week["updated_at"]}


def latest_record_review(db, deal_id):
    return db.execute("SELECT r.* FROM runs r JOIN decisions d ON d.run_id=r.id WHERE d.deal_id=? AND r.kind='review' AND r.status IN ('completed','local_completed') ORDER BY r.started_at DESC,r.rowid DESC LIMIT 1", (deal_id,)).fetchone()


def editable_run(db, run_id):
    row = db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
    if not row:
        raise CorrectionError("Run not found", 404)
    run = store.unpack(dict(row), ["metrics", "package"])
    if run["kind"] != "review" or run["status"] not in {"completed", "local_completed"}:
        raise CorrectionError("Only completed reviews can be corrected")
    if not isinstance(run["package"], dict):
        raise CorrectionError("This review has no finalized publication package")
    key = run["metrics"].get("selection_key")
    latest = db.execute("SELECT id FROM runs WHERE kind='review' AND status IN ('completed','local_completed') AND json_extract(metrics,'$.selection_key')=? ORDER BY started_at DESC,rowid DESC LIMIT 1", (key,)).fetchone() if key else db.execute("SELECT id FROM runs WHERE week_id=? AND kind='review' AND status IN ('completed','local_completed') AND json_extract(metrics,'$.selection_key') IS NULL ORDER BY started_at DESC,rowid DESC LIMIT 1", (run["week_id"],)).fetchone()
    if latest["id"] != run_id:
        raise CorrectionError("Open this week's latest completed review to make changes")
    if db.execute("SELECT id FROM runs WHERE status='running'").fetchone():
        raise CorrectionError("Wait for the running workflow to finish before making changes")
    week = dict(db.execute("SELECT * FROM weeks WHERE id=?", (run["week_id"],)).fetchone())
    if not prepared_versions_match(db, run):
        raise CorrectionError("Prepared inputs changed since this review; review the updated week first")
    if run["package"].get("selection_weeks"):
        periods = run["package"]["selection_weeks"]
        week.update(start=min(period["start"] for period in periods), end=max(period["end"] for period in periods))
    return run, week


def correction_source(deal, source_url, evidence_id):
    cleaned = clean_url(source_url)
    if source_url.strip() and not cleaned:
        raise CorrectionError("Use a valid source URL", 422)
    valid_evidence = set(deal["block_ids"]) | {e["id"] for e in deal["evidence"]}
    if evidence_id and evidence_id not in valid_evidence:
        raise CorrectionError("Select evidence belonging to this company", 422)
    return cleaned


def user_decision(previous, feedback):
    if not feedback or (previous and previous.get("known_investment")):
        return previous
    if previous and previous.get("manual_override", {}).get("at", "") >= feedback["at"]:
        return previous
    original = (previous or {}).get("manual_override", {}).get("model_final", previous)
    prior = json.loads(feedback["previous"]) if feedback.get("previous") else None
    return {**(previous or {"confidence": None, "known_investment": False, "evidence_ids": []}), "fit": feedback["fit"], "reason": feedback["reason"], "manual_override": {"at": feedback["at"], "model_final": original, "source_url": feedback.get("source_url", ""), "evidence_id": feedback.get("evidence_id", ""), "previous_fit": (prior or {}).get("fit")}}


def correct_decision(run_id, deal_id, fit, reason, expected_final, source_url="", evidence_id=""):
    reason = reason.strip()
    if fit not in {"Yes", "No"} or len(reason) > 3000:
        raise CorrectionError("Choose Yes or No; reasoning must be at most 3000 characters", 422)
    with store.WRITE_LOCK, store.connection() as db:
        run, week = editable_run(db, run_id)
        decisions = {row["deal_id"]: json.loads(row["final"]) for row in db.execute("SELECT deal_id,final FROM decisions WHERE run_id=?", (run_id,))}
        if deal_id not in decisions:
            raise CorrectionError("Decision not found in this review", 404)
        previous = decisions[deal_id]
        if previous != expected_final:
            raise CorrectionError("This decision changed since you opened it; reload and try again")
        if previous.get("known_investment"):
            raise CorrectionError("Portfolio exclusions are separate from mandate fit and cannot be changed here")
        pending = deal_id in run["metrics"].get("pending_rechecks", [])
        if previous["fit"] == fit and not pending and not source_url and not evidence_id and (not reason or reason == previous["reason"]):
            raise CorrectionError("Choose a different final decision", 422)
        deals = run_records(db, run)
        candidate = next(d for d in deals if d["id"] == deal_id)
        source_url = correction_source(candidate, source_url, evidence_id)
        at = store.now()
        model_final = previous.get("manual_override", {}).get("model_final", previous)
        reason_provided = bool(reason)
        reason = reason or f"User set final fit to {fit}; no reasoning provided."
        final = {**previous, "fit": fit, "reason": reason, "manual_override": {"at": at, "previous_fit": previous["fit"], "previous_reason": previous["reason"], "model_final": model_final, "reason_provided": reason_provided, "source_url": source_url, "evidence_id": evidence_id}}
        decisions[deal_id] = final
        if candidate.get("portfolio_match", {}).get("status") == "known":
            if fit == "Yes":
                raise CorrectionError("This identity matches an existing investment; recheck the company before including it", 422)
            final["known_investment"] = True
        save_baseline(db, run, deals, {**decisions, deal_id: previous})
        package, metrics = rebuild_package(run, week, deals, decisions)
        metrics["pending_rechecks"] = [did for did in metrics.get("pending_rechecks", []) if did != deal_id]
        company = next(deal["company"] for deal in deals if deal["id"] == deal_id)
        db.execute("UPDATE decisions SET final=? WHERE run_id=? AND deal_id=?", (store.encode(final), run_id, deal_id))
        db.execute("UPDATE feedback SET active=0 WHERE deal_id=?", (deal_id,))
        db.execute("INSERT INTO feedback(id,deal_id,fit,reason,previous,at,source_url,evidence_id) VALUES(?,?,?,?,?,?,?,?)", (uuid.uuid4().hex[:16], deal_id, fit, reason, store.encode(previous), at, source_url, evidence_id))
        db.execute("UPDATE runs SET package=?,metrics=? WHERE id=?", (store.encode(package), store.encode(metrics), run_id))
        db.execute("INSERT INTO events(run_id,at,stage,message,detail) VALUES(?,?,?,?,?)", (run_id, at, "correction", f"{company}: final decision changed from {previous['fit']} to {fit} by user", store.encode({"deal_id": deal_id, "reason": reason, "previous": previous, "final": final, "google_updated": False})))
    return store.get_run(run_id)


def correct_record_decision(deal_id, fit, reason, source_url, evidence_id, expected_final, expected_fields):
    """Correct current records; rebuild only reviews whose inputs still match."""
    if fit not in {"Yes", "No"} or len(reason.strip()) > 3000:
        raise CorrectionError("Choose Yes or No; reasoning must be at most 3000 characters", 422)
    with store.WRITE_LOCK:
        with store.connection() as db:
            if db.execute("SELECT id FROM runs WHERE status='running'").fetchone():
                raise CorrectionError("Wait for the running workflow to finish")
            row = db.execute("SELECT * FROM deals WHERE id=? AND active=1", (deal_id,)).fetchone()
            if not row:
                raise CorrectionError("Active company record not found", 404)
            deal = store.unpack(dict(row), ["fields", "block_ids", "evidence", "portfolio_match"])
            if deal["fields"] != expected_fields:
                raise CorrectionError("These fields changed since you opened them; reload and try again")
            week = dict(db.execute("SELECT * FROM weeks WHERE id=?", (deal["week_id"],)).fetchone())
            if week["status"] != "prepared":
                raise CorrectionError("Prepare the dataset before editing its records")
            source_url = correction_source(deal, source_url, evidence_id)
            latest = latest_record_review(db, deal_id)
            decision_row = db.execute("SELECT final FROM decisions WHERE run_id=? AND deal_id=?", (latest["id"], deal_id)).fetchone() if latest else None
            model = json.loads(decision_row["final"]) if decision_row else None
            feedback = db.execute("SELECT * FROM feedback WHERE deal_id=? AND active=1 ORDER BY at DESC,rowid DESC LIMIT 1", (deal_id,)).fetchone()
            previous = user_decision(model, dict(feedback) if feedback else None)
            if previous != expected_final:
                raise CorrectionError("This decision changed since you opened it; reload and try again")
            if deal["portfolio_match"].get("status") == "known" or (previous and previous.get("known_investment")):
                raise CorrectionError("Portfolio exclusions are separate from mandate fit and cannot be changed here")
            current_run = latest and decision_row and prepared_versions_match(db, latest)
            if not current_run:
                if previous and previous["fit"] == fit and not reason.strip() and not source_url and not evidence_id:
                    raise CorrectionError("No decision values changed", 422)
                at = store.now()
                note = reason.strip() or f"User set final fit to {fit}; no reasoning provided."
                db.execute("UPDATE feedback SET active=0 WHERE deal_id=?", (deal_id,))
                db.execute("INSERT INTO feedback(id,deal_id,fit,reason,previous,at,source_url,evidence_id) VALUES(?,?,?,?,?,?,?,?)", (uuid.uuid4().hex[:16], deal_id, fit, note, store.encode(previous), at, source_url, evidence_id))
                return {"saved": True, "run_id": None, "review_required": True}
        run = correct_decision(latest["id"], deal_id, fit, reason, model, source_url, evidence_id)
        return {"saved": True, "run_id": run["id"], "review_required": False}


def correct_fields(deal_id, changes, reason, source_url, evidence_id, expected_fields, run_id=None):
    reason = reason.strip()
    if not changes or not set(changes) <= set(FIELDS) or len(reason) > 3000:
        raise CorrectionError("Choose valid fields; correction notes must be at most 3000 characters", 422)
    reason = reason or "User edited company details."
    with store.WRITE_LOCK, store.connection() as db:
        if db.execute("SELECT id FROM runs WHERE status='running'").fetchone():
            raise CorrectionError("Wait for the running workflow to finish")
        row = db.execute("SELECT * FROM deals WHERE id=? AND active=1", (deal_id,)).fetchone()
        if not row:
            raise CorrectionError("Active company record not found", 404)
        deal = store.unpack(dict(row), ["fields", "block_ids", "sources", "evidence", "missing", "portfolio_match"])
        if deal["fields"] != expected_fields:
            raise CorrectionError("These fields changed since you opened them; reload and try again")
        week = dict(db.execute("SELECT * FROM weeks WHERE id=?", (deal["week_id"],)).fetchone())
        if week["status"] != "prepared":
            raise CorrectionError("Prepare the dataset before editing its records")
        source_url = correction_source(deal, source_url, evidence_id)
        run = None
        if not run_id:
            latest = latest_record_review(db, deal_id)
            if latest and prepared_versions_match(db, latest):
                run_id = latest["id"]
        if run_id:
            run, _ = editable_run(db, run_id)
            if deal["week_id"] not in run["metrics"].get("week_ids", [run["week_id"]]):
                raise CorrectionError("Correction belongs to another week")
        before = dict(deal["fields"])
        normalized = normalize_fields({**before, **changes})
        if "amount" in changes:
            if normalized["amount"].casefold() in {"undisclosed", "unknown", "not disclosed", "not available"}:
                normalized["amount"] = ""
            elif normalized["amount"] and not MONEY_OCCURRENCE.search(normalized["amount"]):
                raise CorrectionError("Enter a reported financing amount with its currency, or leave it blank if undisclosed", 422)
        if normalized["growth_stage"] not in {"Early", "Growth"} or normalized["sector"] not in TAXONOMY:
            raise CorrectionError("Choose a valid growth stage and sector", 422)
        from datetime import date
        try:
            date.fromisoformat(normalized["deal_date"])
        except ValueError:
            raise CorrectionError("Use a valid deal date", 422)
        if not week["start"] <= normalized["deal_date"] <= week["end"]:
            raise CorrectionError("The deal date must remain within this IC week", 422)
        for key in ("website", "announcement_link"):
            if changes.get(key, "").strip() and not normalized[key]:
                raise CorrectionError("Use an official company URL or a valid announcement URL, not a newsletter tracking link", 422)
        changed = {key: value for key, value in normalized.items() if value != before[key]}
        if not changed:
            raise CorrectionError("No field values changed", 422)
        decisions, records = {}, []
        if run:
            records = run_records(db, run)
            decisions = {r["deal_id"]: json.loads(r["final"]) for r in db.execute("SELECT deal_id,final FROM decisions WHERE run_id=?", (run_id,))}
            save_baseline(db, run, records, decisions)
        at = store.now()
        deal["fields"] = normalized
        for key, value in changed.items():
            fid = "user-" + uuid.uuid4().hex[:16]
            db.execute("UPDATE field_overrides SET active=0 WHERE deal_id=? AND field=?", (deal_id, key))
            db.execute("INSERT INTO field_overrides(id,deal_id,field,old_value,value,source_url,evidence_id,reason,at) VALUES(?,?,?,?,?,?,?,?,?)", (fid, deal_id, key, before[key], value, source_url, evidence_id, reason, at))
            if value:
                deal["missing"].pop(key, None)
            else:
                deal["missing"][key] = reason
            deal["evidence"].append({"id": fid, "kind": "human", "url": source_url, "summary": reason, "fields": [key], "at": at, "old": before[key], "value": value, "evidence_id": evidence_id})
        portfolio = PROJECT / "benchmark/portfolio.json"
        deal["portfolio_match"] = portfolio_match(deal, json.loads(portfolio.read_text()) if portfolio.exists() else [])
        if run:
            records = [deal if d["id"] == deal_id else d for d in records]
            package, metrics = rebuild_package(run, week, records, decisions)
            if set(changed) & FIT_FIELDS:
                metrics["pending_rechecks"] = sorted(set(metrics.get("pending_rechecks", [])) | {deal_id})
            metrics["manual_field_corrections"] = metrics.get("manual_field_corrections", 0) + len(changed)
        db.execute("UPDATE deals SET fields=?,missing=?,evidence=?,portfolio_match=?,updated_at=? WHERE id=?", (store.encode(deal["fields"]), store.encode(deal["missing"]), store.encode(deal["evidence"]), store.encode(deal["portfolio_match"]), at, deal_id))
        prepared_at = sync_prepared(db, week)
        detail = {"deal_id": deal_id, "changes": [{"field": k, "old": before[k], "value": v} for k, v in changed.items()], "reason": reason, "source_url": source_url, "evidence_id": evidence_id, "google_updated": False}
        if run:
            if metrics.get("prepared_versions"):
                metrics["prepared_versions"][deal["week_id"]] = prepared_at
                metrics["prepared_at"] = metrics["prepared_versions"][run["week_id"]]
            else:
                metrics["prepared_at"] = prepared_at
            for record in records:
                db.execute("INSERT INTO review_records(run_id,deal_id,record) VALUES(?,?,?) ON CONFLICT(run_id,deal_id) DO UPDATE SET record=excluded.record", (run_id, record["id"], store.encode(record)))
            db.execute("UPDATE runs SET package=?,metrics=? WHERE id=?", (store.encode(package), store.encode(metrics), run_id))
            db.execute("INSERT INTO events(run_id,at,stage,message,detail) VALUES(?,?,?,?,?)", (run_id, at, "correction", f"{deal['company']}: {len(changed)} field values corrected by user", store.encode(detail)))
    return {"run_id": run_id, "saved": True, "recheck_required": bool(run and set(changed) & FIT_FIELDS)}


def recheck_company(run_id, deal_id):
    from .pipeline import classify_batch, review_inputs
    with store.connection() as db:
        run, week = editable_run(db, run_id)
        records = run_records(db, run)
        candidate = next((d for d in records if d["id"] == deal_id), None)
        if not candidate:
            raise CorrectionError("Company not found in this review", 404)
        decisions = {r["deal_id"]: json.loads(r["final"]) for r in db.execute("SELECT deal_id,final FROM decisions WHERE run_id=?", (run_id,))}
    previous = decisions[deal_id]
    if candidate["portfolio_match"].get("status") == "known":
        result = {**previous, "fit": "No", "known_investment": True, "reason": "Corrected identity matches an existing portfolio investment", "field_corrections": []}
        usage = {"model_calls": 0}
    else:
        result_list, usage = classify_batch(review_inputs([candidate]), f"{run_id}/rechecks/{uuid.uuid4().hex[:16]}", independent=True)
        result = result_list[0]
    for correction in result.get("field_corrections", []):
        # Human field corrections stay authoritative; contrary model findings are recorded.
        candidate["evidence"].append({**correction, "id": "recheck-note-" + uuid.uuid4().hex[:16], "kind": "recheck_finding", "at": store.now()})
    original = previous.get("original_model_final", previous.get("manual_override", {}).get("model_final", previous))
    final = {**result, "original_model_final": original}
    if previous.get("manual_override") and not result.get("known_investment"):
        final = {**previous, "latest_recheck": result}
    decisions[deal_id] = final
    with store.WRITE_LOCK, store.connection() as db:
        fresh, _ = editable_run(db, run_id)
        if fresh["metrics"] != run["metrics"]:
            raise CorrectionError("Review changed while rechecking; reload")
        package, metrics = rebuild_package(run, week, records, decisions)
        metrics["pending_rechecks"] = [did for did in metrics.get("pending_rechecks", []) if did != deal_id]
        metrics.setdefault("followup_checks", []).append({"deal_id": deal_id, "at": store.now(), "result": result, "usage": usage})
        db.execute("UPDATE decisions SET final=? WHERE run_id=? AND deal_id=?", (store.encode(final), run_id, deal_id))
        db.execute("UPDATE runs SET package=?,metrics=? WHERE id=?", (store.encode(package), store.encode(metrics), run_id))
        db.execute("INSERT INTO events(run_id,at,stage,message,detail) VALUES(?,?,?,?,?)", (run_id, store.now(), "correction", f"{candidate['company']}: targeted AI recheck complete", store.encode({"previous": previous, "result": result, "human_override_preserved": bool(previous.get("manual_override"))})))
    return store.get_run(run_id)


def publish_corrections(run_id):
    with store.connection() as db:
        run, _ = editable_run(db, run_id)
    if run["target"] == "local":
        raise CorrectionError("This is a local-only review; no Google destination was selected")
    if not run["metrics"].get("outputs_dirty"):
        raise CorrectionError("There are no unpublished corrections")
    if run["metrics"].get("pending_rechecks"):
        raise CorrectionError("Confirm or recheck the companies whose corrected facts could affect mandate fit before publishing")
    started = time.perf_counter()
    metrics = run["metrics"]
    try:
        publisher = Publisher(run["target"])
        snapshot = publisher.snapshot()
        result = publisher.publish(run["package"], snapshot, f"{run_id}/corrections/{uuid.uuid4().hex[:16]}")
        publisher.verify(run["package"], snapshot)
        metrics.update(outputs_dirty=False, corrections_published_at=store.now(), correction_publication_seconds=time.perf_counter() - started, publication=result)
        metrics.pop("correction_publication_error", None)
        store.update_run(run_id, metrics=metrics, package=run["package"])
        store.event(run_id, "complete", "Corrected Sheet, Stats, and roundup published and confirmed", {"seconds": metrics["correction_publication_seconds"], "ai_rerun": False})
    except Exception as error:
        metrics["correction_publication_error"] = str(error)
        store.update_run(run_id, metrics=metrics)
        store.event(run_id, "complete", "Correction publication failed; outputs remain pending", {"error": str(error), "google_may_be_partially_updated": True})
        raise
    return store.get_run(run_id)
