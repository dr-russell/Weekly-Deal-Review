import json
import copy
import shutil
import subprocess
import time
import uuid

from jsonschema import validate, ValidationError

from .config import ARTIFACTS, ROOT, TAXONOMY


def object_schema(properties):
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


STRING = {"type": "string"}
STRINGS = {"type": "array", "items": STRING}
DECISION_SCHEMA = object_schema({"decisions": {"type": "array", "items": object_schema({
    "id": STRING, "fit": {"type": "string", "enum": ["Yes", "Maybe", "No"]},
    "confidence": {"type": "number", "minimum": 0, "maximum": 1}, "reason": STRING,
    "evidence_ids": STRINGS, "known_investment": {"type": "boolean"},
    "field_corrections": {"type": "array", "items": object_schema({
        "field": {"type": "string", "enum": ["growth_stage", "deal_date", "website", "round", "amount", "investors", "sector", "description", "geography", "announcement_link"]},
        "value": STRING, "reason": STRING, "evidence_id": STRING,
    })},
})}})
BINARY_DECISION_SCHEMA = copy.deepcopy(DECISION_SCHEMA)
BINARY_DECISION_SCHEMA["properties"]["decisions"]["items"]["properties"]["fit"]["enum"] = ["Yes", "No"]
EXTRACTION_SCHEMA = object_schema({"records": {"type": "array", "items": object_schema({
    "block_id": STRING, "kind": {"type": "string", "enum": ["deal", "editorial"]}, "reason": STRING,
    "deals": {"type": "array", "items": object_schema({"company": STRING, "fields": object_schema({key: STRING for key in ["growth_stage", "deal_date", "website", "round", "amount", "investors", "sector", "description", "geography", "announcement_link"]})})},
})}})
ENRICHMENT_SCHEMA = object_schema({"deals": {"type": "array", "items": object_schema({
    "id": STRING, "fields": object_schema({key: STRING for key in ["growth_stage", "deal_date", "website", "round", "amount", "investors", "sector", "description", "geography", "announcement_link"]}),
    "evidence": {"type": "array", "items": object_schema({"url": STRING, "quote": STRING, "summary": STRING, "fields": STRINGS})},
    "missing": object_schema({key: STRING for key in ["growth_stage", "deal_date", "website", "round", "amount", "investors", "sector", "description", "geography", "announcement_link"]}),
})}})
EXTRACTION_SCHEMA["properties"]["records"]["items"]["properties"]["deals"]["items"]["properties"]["fields"]["properties"]["sector"] = {"type": "string", "enum": ["", *TAXONOMY]}
ENRICHMENT_SCHEMA["properties"]["deals"]["items"]["properties"]["fields"]["properties"]["sector"] = {"type": "string", "enum": ["", *TAXONOMY]}


class ModelOutputError(ValueError):
    def __init__(self, message, metadata):
        super().__init__(message)
        self.model_metadata = metadata


def review_output_schema(ids, independent=False):
    schema = copy.deepcopy(BINARY_DECISION_SCHEMA if independent else DECISION_SCHEMA)
    decisions = schema["properties"]["decisions"]
    decisions.update(minItems=len(ids), maxItems=len(ids))
    decisions["items"]["properties"]["id"] = {"type": "string", "enum": list(ids)}
    return schema


def run_model(prompt, schema, model, label, research=False, timeout=240, reasoning_effort="low"):
    folder = ARTIFACTS / label / uuid.uuid4().hex[:10]
    folder.mkdir(parents=True)
    (folder / "prompt.txt").write_text(prompt)
    (folder / "schema.json").write_text(json.dumps(schema))
    if reasoning_effort not in {"none", "low", "medium", "high", "xhigh", "max"}:
        raise ValueError("Unsupported reasoning effort")
    command = [shutil.which("codex") or "codex", "exec", "--ignore-user-config", "--skip-git-repo-check", "--ephemeral", "-s", "read-only", "-C", str(ROOT), "-m", model, "-c", f'model_reasoning_effort="{reasoning_effort}"', "-c", f'web_search="{"live" if research else "disabled"}"', "--json", "--output-schema", str(folder / "schema.json"), "-o", str(folder / "output.json"), "-"]
    started = time.perf_counter()
    try:
        result = subprocess.run(command, input=prompt, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as error:
        for name, value in [("events.jsonl", error.stdout), ("stderr.txt", error.stderr)]:
            (folder / name).write_text(value.decode(errors="replace") if isinstance(value, bytes) else value or "")
        (folder / "metadata.json").write_text(json.dumps({"model": model, "seconds": round(time.perf_counter() - started, 3), "status": "timed_out", "usage": None, "timeout": timeout}))
        raise
    (folder / "events.jsonl").write_text(result.stdout)
    (folder / "stderr.txt").write_text(result.stderr)
    if result.returncode:
        raise RuntimeError(f"Model job {label} failed ({result.returncode}): {result.stderr[-700:]}")
    output = json.loads((folder / "output.json").read_text())
    usage, web_calls, forbidden = {}, 0, []
    for line in result.stdout.splitlines():
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if item.get("type") == "turn.completed":
            usage = item.get("usage", {})
        if item.get("type") == "item.completed":
            kind = item.get("item", {}).get("type")
            if kind == "web_search":
                web_calls += 1
            if kind in {"command_execution", "mcp_tool_call", "file_change"} or kind == "web_search" and not research:
                forbidden.append(kind)
    if forbidden:
        raise RuntimeError(f"Model used a tool outside its review contract: {forbidden}")
    artifact = folder.relative_to(ROOT) if folder.is_relative_to(ROOT) else folder
    metadata = {"model": model, "reasoning_effort": reasoning_effort, "seconds": round(time.perf_counter() - started, 3), "usage": usage, "web_calls": web_calls, "artifact": str(artifact), "status": "completed"}
    try:
        validate(output, schema)
    except ValidationError as error:
        metadata.update(status="invalid_output", validation_error=error.message)
        (folder / "metadata.json").write_text(json.dumps(metadata))
        raise ModelOutputError("Model output failed schema validation: " + error.message, metadata) from error
    (folder / "metadata.json").write_text(json.dumps(metadata))
    return output, metadata


def reconcile_ids(records, ids, key="id"):
    actual = [r[key] for r in records]
    if len(actual) != len(set(actual)) or set(actual) != set(ids):
        raise ValueError(f"Model coverage failed: missing={set(ids)-set(actual)}, extra={set(actual)-set(ids)}, duplicate={len(actual)-len(set(actual))}")


def compact_review_payload(payload):
    compact, mapping = copy.deepcopy(payload), {}
    for i, item in enumerate(compact, 1):
        alias = f"D{i}"
        original = item["id"]
        sources = {}
        for j, evidence in enumerate(item["evidence"], 1):
            short = f"E{j}"
            sources[short] = evidence["id"]
            evidence["id"] = short
        item["id"] = alias
        reverse = {value: key for key, value in sources.items()}
        for flag in item.get("data_quality_flags", []):
            flag["evidence_id"] = reverse[flag["evidence_id"]]
        mapping[alias] = {"id": original, "evidence": sources}
    return compact, mapping


def restore_review_ids(decisions, mapping):
    reconcile_ids(decisions, mapping)
    for decision in decisions:
        entry = mapping[decision["id"]]
        decision["id"] = entry["id"]
        try:
            decision["evidence_ids"] = [entry["evidence"][key] for key in decision["evidence_ids"]]
            for correction in decision["field_corrections"]:
                correction["evidence_id"] = entry["evidence"][correction["evidence_id"]]
        except KeyError as error:
            raise ValueError("Review cited an evidence identifier absent from this company's record") from error
    return decisions


def usage_totals(jobs):
    totals = {}
    for job in jobs:
        model = job["model"]
        item = totals.setdefault(model, {"jobs": 0, "input_tokens": 0, "cached_input_tokens": 0, "output_tokens": 0, "web_calls": 0})
        item["jobs"] += job.get("attempt_count", 1)
        for key in ["input_tokens", "cached_input_tokens", "output_tokens"]:
            item[key] += job.get("usage", {}).get(key, 0)
        item["web_calls"] += job.get("web_calls", 0)
    for item in totals.values():
        item["uncached_input_tokens"] = max(0, item["input_tokens"] - item["cached_input_tokens"])
    return {"by_model": totals, "estimated_dollars": None, "billing_basis": "Signed-in Codex CLI usage; not an API billing receipt"}
