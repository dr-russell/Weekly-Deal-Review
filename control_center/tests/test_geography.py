import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from control_center import pipeline, store
from control_center.geography import normalize_geography
from control_center.geography_migration import normalize_database
from control_center.logic import normalize_fields


class GeographyTests(unittest.TestCase):
    def test_us_aliases_and_state_names(self):
        for country in ("US", "USA", "U.S.", "U.S.A.", "United States of America", "united states"):
            self.assertEqual(normalize_geography("New York City, NY, " + country), "New York City, New York, United States")
        self.assertEqual(normalize_geography("Washington, D.C., USA"), "Washington, District of Columbia, United States")
        self.assertEqual(normalize_geography("United States"), "United States")
        self.assertEqual(normalize_geography("US"), "United States")

    def test_regions_are_scoped_to_country(self):
        for raw, expected in [
            ("Toronto, ON, CA", "Toronto, Ontario, Canada"),
            ("Sydney, NSW, AU", "Sydney, New South Wales, Australia"),
            ("Perth, WA, Australia", "Perth, Western Australia, Australia"),
            ("Seattle, WA, USA", "Seattle, Washington, United States"),
            ("London, UK", "London, United Kingdom"),
            ("Tel Aviv, Israel", "Tel Aviv, Israel"),
        ]:
            self.assertEqual(normalize_geography(raw), expected)

    def test_multiple_offices_and_city_text_are_preserved(self):
        raw = " Boston, MA, USA ; Brisbane, QLD, Australia; New York City, NY, US "
        expected = "Boston, Massachusetts, United States; Brisbane, Queensland, Australia; New York City, New York, United States"
        self.assertEqual(normalize_geography(raw), expected)
        self.assertEqual(normalize_geography(expected), expected)

    def test_unknown_and_ambiguous_locations_are_not_inferred(self):
        for raw in ("", "Unknown", "CA", "San Francisco, CA", "Bengaluru, Karnataka, India", "Tbilisi, Georgia", "Paris, Île-de-France, France", "New York"):
            self.assertEqual(normalize_geography(raw), raw)

    def test_shared_field_normalizer_formats_geography(self):
        self.assertEqual(normalize_fields({"geography": "Austin, TX, USA"})["geography"], "Austin, Texas, United States")


class GeographyMigrationTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        root = Path(self.folder.name)
        self.patches = [patch.object(store, "DATA", root), patch.object(store, "DB", root / "test.sqlite"), patch.object(store, "ARTIFACTS", root / "artifacts"), patch.object(pipeline, "DATA", root)]
        for item in self.patches:
            item.start()
        store.initialize()
        self.week = "2026-09-16_2026-09-22"
        store.execute("INSERT INTO weeks(id,start,end,label,status,updated_at) VALUES(?,?,?,?,?,?)", (self.week, "2026-09-16", "2026-09-22", "Test", "prepared", store.now()))
        self.record = {"id": "example", "company": "Example", "fields": {"geography": "New York, NY, USA"}, "evidence": [{"quote": "Based in New York, NY, USA"}], "sources": ["Term Sheet"], "block_ids": ["source"], "missing": {}, "portfolio_match": {}}
        for did, active in (("example", 1), ("inactive", 0)):
            store.execute("INSERT INTO deals(id,week_id,company,entity_key,fields,block_ids,sources,evidence,missing,active,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)", (did, self.week, "Example", "example", store.encode(self.record["fields"]), '["source"]', '["Term Sheet"]', store.encode(self.record["evidence"]), '{}', active, store.now()))
        self.previous = pipeline.freeze_prepared(self.week, [self.record])
        self.previous_text = (root / self.previous["path"]).read_text()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.folder.cleanup()

    def test_dry_run_and_backfill_preserve_history_and_evidence(self):
        dry = normalize_database()
        self.assertEqual(dry["changed_deals"], 2)
        self.assertFalse(dry["applied"])
        self.assertEqual(store.get_deals(self.week)[0]["fields"], self.record["fields"])
        run = store.new_run(self.week, "review", "local")
        store.update_run(run, status="local_completed", package={"geography": "New York, NY, USA"})
        result = normalize_database(apply=True)
        self.assertEqual(result["changed_active_deals"], 1)
        self.assertTrue(Path(result["backup"]).exists())
        for row in store.query("SELECT fields,evidence,active FROM deals"):
            self.assertEqual(json.loads(row["fields"])["geography"], "New York, New York, United States")
            self.assertEqual(json.loads(row["evidence"]), self.record["evidence"])
        records, manifest = pipeline.prepared_deals(self.week)
        self.assertNotEqual(manifest["sha256"], self.previous["sha256"])
        self.assertEqual(records[0]["fields"]["geography"], "New York, New York, United States")
        self.assertEqual((store.DATA / self.previous["path"]).read_text(), self.previous_text)
        self.assertEqual(store.get_run(run)["package"], {"geography": "New York, NY, USA"})
        event = store.query("SELECT detail FROM events")[0]
        self.assertEqual(json.loads(event["detail"])["changes"][0]["old"], "New York, NY, USA")
        self.assertEqual(normalize_database(apply=True)["changed_deals"], 0)

    def test_active_workflow_blocks_migration(self):
        store.new_run(self.week, "prepare", "local")
        with self.assertRaisesRegex(ValueError, "active workflow"):
            normalize_database(apply=True)
        self.assertEqual(store.get_deals(self.week)[0]["fields"], self.record["fields"])

    def test_snapshot_mismatch_rolls_back(self):
        pipeline.freeze_prepared(self.week, [{**self.record, "fields": {"geography": "Boston, MA, USA"}}])
        with self.assertRaisesRegex(ValueError, "Prepared fields"):
            normalize_database(apply=True)
        self.assertEqual(store.get_deals(self.week)[0]["fields"], self.record["fields"])
        self.assertEqual(store.query("SELECT id FROM runs"), [])


if __name__ == "__main__":
    unittest.main()
