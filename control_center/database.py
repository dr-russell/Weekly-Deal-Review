"""Combine repeated financing coverage for the database without changing week records."""
import copy
import json
from collections import defaultdict

from .logic import company_domain, normalized_name, same_financing


def financing_record(value):
    record = copy.deepcopy(value)
    # Match source currencies/amounts, not USD conversions at different weekly rates.
    record["fields"]["amount"] = value.get("amount_normalization", {}).get("original_amount", value["fields"]["amount"])
    return record


def dedupe_database(values):
    groups = []
    index = defaultdict(set)
    for value in sorted(values, key=lambda item: (item["fields"]["deal_date"], item["id"])):
        candidate = financing_record(value)
        keys = [("name", normalized_name(value["company"])),
                ("domain", company_domain(value["fields"]["website"])),
                ("announcement", value["fields"]["announcement_link"])]
        keys = [key for key in keys if key[1]]
        possible = sorted({group_id for key in keys for group_id in index[key]})
        fit = value.get("decision", {}).get("final", {}).get("fit") if value.get("decision") else None
        group_id = next((group_id for group_id in possible if all(
            same_financing(candidate, record) and (not fit or not member.get("decision") or fit == member["decision"]["final"]["fit"])
            for member, record in groups[group_id]
        )), None)
        if group_id is None:
            group_id = len(groups)
            groups.append([(value, candidate)])
        else:
            groups[group_id].append((value, candidate))
        for key in keys:
            index[key].add(group_id)
    result = []
    for group in groups:
        members = [member for member, _ in group]
        # The displayed decision belongs to the displayed record; never copy it to another ID.
        representative = max(members, key=lambda item: (
            bool((item.get("decision") or {}).get("final", {}).get("manual_override")),
            bool(item.get("decision")), item["fields"]["deal_date"], item["id"],
        ))
        value = copy.deepcopy(representative)
        value["sources"] = list(dict.fromkeys(source for member in members for source in member["sources"]))
        value["coverage_records"] = [{"id": member["id"], "company": member["company"], "week_id": member["week_id"], "sources": member["sources"]} for member in members]
        result.append(value)
    return sorted(result, key=lambda item: item["company"].casefold())


def database_matches(value, query, fit):
    searchable = [value["company"], value["fields"], value["sources"], value.get("coverage_records", [])]
    status = value["decision"]["final"]["fit"] if value.get("decision") else "Unreviewed"
    return query.casefold() in json.dumps(searchable).casefold() and fit in {"All", status}
