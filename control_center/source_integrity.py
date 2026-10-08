import copy
import json

from . import store
from .logic import company_domain, consolidate_prepared, merge_deals, normalize_fields, normalized_name, remaining_missing_fields


def rebuild_sources(week_id, apply=False):
    from .pipeline import freeze_prepared, save_deal
    weeks = store.query("SELECT * FROM weeks WHERE id=?", (week_id,))
    if not weeks:
        raise ValueError("Week not found")
    if store.query("SELECT id FROM runs WHERE status='running'"):
        raise ValueError("Wait for the active workflow before repairing sources")
    blocks = store.query("SELECT * FROM blocks WHERE week_id=? ORDER BY received_at,id", (week_id,))
    if any(b["kind"] == "pending" for b in blocks):
        raise ValueError("Extract every source item before repairing associations")
    old = [store.unpack(r, ["fields", "block_ids", "sources", "evidence", "missing", "portfolio_match"]) for r in store.query("SELECT * FROM deals WHERE week_id=?", (week_id,))]
    mentions, names_by_block = [], {}
    for block in blocks:
        extracted = json.loads(block["extraction"])
        names_by_block[block["id"]] = {normalized_name(item["company"]) for item in extracted}
        for item in extracted:
            fields = normalize_fields(item["fields"])
            fields["deal_date"] = fields["deal_date"] or block["received_at"]
            mentions.append({"company": item["company"], "fields": fields, "block_ids": [block["id"]], "sources": [block["source"]], "evidence": [{"id": block["id"], "kind": "newsletter", "source": block["source"], "at": block["received_at"]}], "missing": {}})
    rebuilt = merge_deals(mentions)
    invalid = [{"company": d["company"], "block_id": bid} for d in old if d.get("active", 1) for bid in d["block_ids"] if normalized_name(d["company"]) not in names_by_block.get(bid, set())]
    claimed = set()
    for deal in rebuilt:
        missing_reasons = {}
        candidates = [d for d in old if normalized_name(d["company"]) == normalized_name(deal["company"]) and set(d["block_ids"]) & set(deal["block_ids"])]
        active = [d for d in candidates if d.get("active", 1)]
        candidates = active or candidates
        if len(candidates) == 1:
            prior = candidates[0]
            if prior["id"] not in claimed:
                deal["id"] = prior["id"]
                claimed.add(prior["id"])
            web = [copy.deepcopy(e) for e in prior["evidence"] if e.get("kind") in {"web", "human"}]
            deal["evidence"].extend(web)
            supported = {key for e in web for key in e.get("fields", [])}
            for key in supported - {"deal_date", "investors"}:
                if key in deal["fields"] and prior["fields"].get(key):
                    deal["fields"][key] = prior["fields"][key]
            for key, reason in prior["missing"].items():
                if reason and not prior["fields"].get(key):
                    deal["fields"][key] = ""
                    missing_reasons[key] = reason
            if not company_domain(deal["fields"]["website"]):
                deal["fields"]["website"] = ""
        deal["fields"] = normalize_fields(deal["fields"])
        deal["missing"] = {key: missing_reasons.get(key, "No verified value after source-association repair; targeted research is needed") for key, value in deal["fields"].items() if not value}
        from .corrections import apply_saved_fields
        apply_saved_fields(deal)
    rebuilt, reconciled = consolidate_prepared(rebuilt)
    active_names = {normalized_name(d["company"]) for d in old if d.get("active", 1)}
    result = {"week_id": week_id, "invalid_associations": invalid, "before": len([d for d in old if d.get("active", 1)]), "after": len(rebuilt), "restored_companies": sorted({d["company"] for d in rebuilt if normalized_name(d["company"]) not in active_names}), "remaining_missing_fields": len(remaining_missing_fields(rebuilt)), "reconciled": reconciled, "applied": apply}
    if apply:
        rid = store.new_run(week_id, "source_repair", "local")
        try:
            for deal in rebuilt:
                save_deal(week_id, deal)
            ids = {d["id"] for d in rebuilt}
            for deal in old:
                if deal["id"] not in ids:
                    store.execute("UPDATE deals SET active=0 WHERE id=?", (deal["id"],))
            report = json.loads(weeks[0]["report"])
            report.update(prepared_snapshot=freeze_prepared(week_id, rebuilt), unique_deals=len(rebuilt), duplicate_mentions=len(mentions)-len(rebuilt), missing_fields=len(remaining_missing_fields(rebuilt)))
            report.setdefault("source_repairs", []).append(result)
            store.execute("UPDATE weeks SET report=?,updated_at=? WHERE id=?", (store.encode(report), store.now(), week_id))
            store.update_run(rid, status="completed", stage="prepared", finished_at=store.now(), metrics=result)
            store.event(rid, "prepared", "Source associations rebuilt from extracted company identities; fresh fit review required", result)
        except Exception as error:
            store.update_run(rid, status="failed", error=str(error), finished_at=store.now())
            raise
    return result
