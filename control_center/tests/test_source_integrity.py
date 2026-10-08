import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from control_center import pipeline, store, fx
from control_center.logic import company_domain, merge_deals, normalize_fields, same_financing
from control_center.source_integrity import rebuild_sources
from control_center.tests.test_workflow import deal


class SourceIntegrityTests(unittest.TestCase):
    def test_newsletter_tracking_domains_are_not_company_identity(self):
        first = deal("AcouBatt", amount="$1 million", round="Seed", website="https://click.mail.fortune.com/?qs=one")
        second = deal("Other Company", amount="$1 million", round="Seed", website="https://click.mail.fortune.com/?qs=two")
        self.assertFalse(same_financing(first, second))
        self.assertEqual(normalize_fields(first["fields"])["website"], "")
        self.assertEqual(company_domain("https://click.mail.fortune.com/?url=https%3A%2F%2Facoubatt.ai%2F"), "acoubatt.ai")

    def test_company_domain_can_still_resolve_legitimate_name_variants(self):
        first = deal("AcouBatt", website="https://acoubatt.ai")
        second = deal("AcouBatt Technologies", website="https://www.acoubatt.ai")
        self.assertTrue(same_financing(first, second))

    def test_repair_restores_deals_and_isolates_source_excerpts(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with patch.object(store, "DATA", root), patch.object(store, "DB", root / "test.sqlite"), patch.object(store, "ARTIFACTS", root / "artifacts"), patch.object(pipeline, "DATA", root), patch.object(pipeline, "PROJECT", root):
                store.initialize()
                wid = pipeline.register_week("2026-09-16", "2026-09-22")
                first, second = deal("AcouBatt", website="https://acoubatt.ai"), deal("Other Company", website="https://other.example.com")
                for item in [first, second]:
                    item["portfolio_match"] = {"status": "not_matched", "candidates": []}
                    bid = item["id"]
                    store.execute("INSERT INTO messages(id,source,received_at,subject,payload) VALUES(?,?,?,?,?)", (bid, "Term Sheet", "2026-09-18", "Deals", "{}"))
                    store.execute("INSERT INTO blocks(id,week_id,message_id,source,received_at,text,kind,extraction) VALUES(?,?,?,?,?,?,?,?)", (bid, wid, bid, "Term Sheet", "2026-09-18", item["company"] + " financing", "deal", store.encode([{"company": item["company"], "fields": item["fields"]}])))
                contaminated = copy.deepcopy(first)
                contaminated["block_ids"] = ["AcouBatt", "Other Company"]
                contaminated["fields"]["geography"] = ""
                contaminated["missing"] = {"geography": "Conflicting source locations remain unresolved"}
                pipeline.save_deal(wid, contaminated)
                store.execute("UPDATE weeks SET status='prepared' WHERE id=?", (wid,))
                before = rebuild_sources(wid)
                self.assertEqual(before["before"], 1)
                self.assertEqual(before["after"], 2)
                self.assertEqual(before["restored_companies"], ["Other Company"])
                self.assertEqual(len(before["invalid_associations"]), 1)
                self.assertEqual(len(store.get_deals(wid)), 1)
                rebuild_sources(wid, apply=True)
                values = store.get_deals(wid)
                self.assertEqual(len(values), 2)
                for value in values:
                    self.assertEqual(value["block_ids"], [value["company"]])
                    evidence = pipeline.source_evidence(value)
                    self.assertEqual(evidence[0]["date"], "2026-09-18")
                repaired = next(value for value in values if value["company"] == "AcouBatt")
                self.assertEqual(repaired["fields"]["geography"], "")
                self.assertEqual(repaired["missing"]["geography"], "Conflicting source locations remain unresolved")
                self.assertEqual(rebuild_sources(wid)["invalid_associations"], [])

    def test_public_history_import_does_not_require_private_assets(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(pipeline, "PROJECT", Path(folder)):
            self.assertEqual(pipeline.import_history()["imported_blocks"], 0)

    def test_preparation_preserves_scoped_corrections_and_deactivates_stale_records(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with patch.object(store, "DATA", root), patch.object(store, "DB", root / "test.sqlite"), patch.object(store, "ARTIFACTS", root / "artifacts"), patch.object(pipeline, "DATA", root), patch.object(pipeline, "PROJECT", root), patch.object(fx, "prepare_rates", return_value={}):
                store.initialize()
                wid = pipeline.register_week("2026-09-16", "2026-09-22")
                for company, bid in [("Example", "b1"), ("Other", "b2")]:
                    raw = deal(company, website="https://click.mail.fortune.com/?qs="+bid)
                    store.execute("INSERT INTO messages(id,source,received_at,subject,payload) VALUES(?,?,?,?,?)", (bid, "Term Sheet", "2026-09-18", "Deals", "{}"))
                    store.execute("INSERT INTO blocks(id,week_id,message_id,source,received_at,text,kind,extraction) VALUES(?,?,?,?,?,?,?,?)", (bid, wid, bid, "Term Sheet", "2026-09-18", company, "deal", store.encode([{"company": company, "fields": raw["fields"]}])))
                original = deal("Example", amount="$12 million")
                original["id"], original["block_ids"] = "stable-example", ["b1"]
                original["evidence"] = [{"id": "web-example", "kind": "web", "enrichment_version": "application-context-v3", "url": "https://example.com", "quote": "Contractor software", "fields": ["website"]}]
                pipeline.save_deal(wid, original)
                ghost = deal("Wrongly Merged")
                ghost["block_ids"] = ["b1"]
                pipeline.save_deal(wid, ghost)
                store.execute("INSERT INTO field_overrides(id,deal_id,field,old_value,value,source_url,evidence_id,reason,at) VALUES(?,?,?,?,?,?,?,?,?)", ("override", original["id"], "amount", "$10 million", "$12 million", "https://example.com/news", "", "Official amount correction", store.now()))
                def enrich(batch, run_id):
                    results = []
                    for record in batch:
                        fields = {**record["fields"], "website": "https://other.example.com", "announcement_link": "https://other.example.com/news"}
                        results.append({"id": record["id"], "fields": fields, "missing": {}, "evidence": [{"url": fields["website"], "quote": "Contractor software", "summary": "Products used by contractors", "fields": ["website", "description"]}]})
                    return results, {"model": "test", "usage": {}, "web_calls": 0}
                rid = store.new_run(wid, "prepare", "local")
                with patch.object(pipeline, "enrich_batch", enrich):
                    pipeline.prepare(rid, wid)
                records = store.get_deals(wid)
                self.assertEqual({d["company"] for d in records}, {"Example", "Other"})
                retained = next(d for d in records if d["company"] == "Example")
                self.assertEqual(retained["id"], "stable-example")
                self.assertEqual(retained["fields"]["amount"], "$12 million")
                self.assertEqual(store.query("SELECT active FROM deals WHERE id=?", (ghost["id"],))[0]["active"], 0)
                self.assertEqual(len(pipeline.prepared_deals(wid)[0]), 2)
                store.execute("UPDATE runs SET status='completed' WHERE id=?", (rid,))
                rid = store.new_run(wid, "prepare", "local")
                with patch.object(pipeline, "enrich_batch", side_effect=RuntimeError("Research unavailable")):
                    # Cached records require no model work on a repeat preparation.
                    pipeline.prepare(rid, wid)
                self.assertEqual(len(store.get_deals(wid)), 2)
