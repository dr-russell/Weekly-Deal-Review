import hashlib
import json
from datetime import date, timedelta

import requests

from . import store
from .config import DATA


def freeze_rates(wid, ratebook):
    payload = store.encode(ratebook)
    digest = hashlib.sha256(payload.encode()).hexdigest()
    folder = DATA / "prepared"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{wid}-fx-{digest[:20]}.json"
    if not path.exists():
        path.write_text(payload)
    manifest = {"path": str(path.relative_to(DATA)), "sha256": digest, "at": store.now(), "observations": len(ratebook.get("observations", []))}
    store.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", ("rates:" + wid, store.encode(manifest)))
    return manifest


def prepared_rates(wid):
    records = store.query("SELECT value FROM settings WHERE key=?", ("rates:" + wid,))
    if not records:
        return {}, None
    manifest = json.loads(records[0]["value"])
    path = (DATA / manifest["path"]).resolve()
    if not path.is_relative_to(DATA.resolve()):
        raise ValueError("Exchange-rate snapshot must stay in local workflow data")
    payload = path.read_text()
    if hashlib.sha256(payload.encode()).hexdigest() != manifest["sha256"]:
        raise ValueError("Prepared exchange rates changed; stop before publishing")
    return json.loads(payload), manifest


def prepare_rates(run_id, wid):
    week = store.query("SELECT * FROM weeks WHERE id=?", (wid,))[0]
    start = (date.fromisoformat(week["start"]) - timedelta(days=28)).isoformat()
    url = "https://www.bankofcanada.ca/valet/observations/group/FX_RATES_DAILY/json"
    response = requests.get(url, params={"start_date": start, "end_date": week["end"]}, timeout=(10, 30))
    response.raise_for_status()
    data = response.json()
    observations = data.get("observations", [])
    if not observations or not all(start <= row.get("d", "") <= week["end"] for row in observations):
        raise ValueError("Exchange-rate provider returned missing or out-of-window observations")
    ratebook = {"provider": "Bank of Canada", "source_url": response.url, "start": start, "end": week["end"], "fetched_at": store.now(), "observations": observations, "series": data.get("seriesDetail", {}), "policy": "Use same-day or latest prior business-day rate, at most seven calendar days old; preserve source USD equivalents first"}
    manifest = freeze_rates(wid, ratebook)
    store.event(run_id, "rates", "Dated exchange rates frozen before the live review", {"outside_demo_timer": True, "manifest": manifest, "source": response.url})
    return manifest
