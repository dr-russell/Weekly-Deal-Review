import json
import sqlite3
import unittest
from pathlib import Path
from unittest.mock import patch
from contextlib import closing

from control_center import store, pipeline
from control_center.corrections import correct_decision, correct_fields
from control_center.history import historical_run, historical_deal, clear_history
from control_center.tests import test_corrections


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_corrections.CorrectionTests()
        self.fixture.setUp()
        self.run = self.fixture.run
        self.week = self.fixture.week
        store.update_run(self.run, finished_at=store.now(), timings={"review": 30, "verification": 10})
        store.event(self.run, "complete", "Original review finished", {"google_published": True})

    def tearDown(self):
        self.fixture.tearDown()

    def test_historical_results_preserve_original_decisions_fields_metrics_and_package(self):
        original = store.get_run(self.run)
        correct_fields("example", {"amount": "$12 million", "geography": "Toronto, Canada"}, "New evidence", "https://example.com/contact", "", self.fixture.deal["fields"], self.run)
        correct_decision(self.run, "example", "No", "Corrected mandate eligibility", self.fixture.final)
        history = historical_run(self.run)
        self.assertEqual(history["decisions"][0]["final"], self.fixture.final)
        self.assertEqual(history["package"], original["package"])
        self.assertEqual(history["metrics"]["count"], 1)
        self.assertEqual(history["metrics"]["capital_usd_m"], 10)
        self.assertEqual(history["elapsed"], 42)
        self.assertEqual(history["timings"], original["timings"])
        self.assertEqual(history["historical"]["result_version"], "original_completed")
        self.assertEqual(history["events"][0]["message"], "Original review finished")
        self.assertEqual(len(history["later_events"]), 2)
        with patch.object(pipeline, "source_evidence", return_value=[]):
            record = historical_deal(self.run, "example")
        self.assertEqual(record["fields"]["geography"], "New York")
        self.assertEqual(record["fields"]["amount"], "$10 million")
        self.assertFalse(record["editable"])
        self.assertFalse(record["editable_decision"])
        self.assertEqual(record["review_decision"]["final"]["fit"], "Yes")
        self.assertEqual(store.get_run(self.run)["decisions"][0]["final"]["fit"], "No")
        from control_center.app import runs
        self.assertEqual(runs()[0]["historical_selected_count"], 1)

    def test_missing_snapshot_never_uses_current_company_fields(self):
        history = historical_run(self.run)
        self.assertFalse(history["historical"]["snapshot_available"])
        self.assertIn("unavailable", history["historical"]["snapshot_message"])
        with self.assertRaisesRegex(KeyError, "snapshot unavailable"):
            historical_deal(self.run, "example")

    def test_preparation_history_uses_only_its_own_immutable_manifest(self):
        preparation = store.new_run(self.week, "prepare", "local")
        records = store.get_deals(self.week)
        manifest = pipeline.freeze_prepared(self.week, records)
        store.update_run(preparation, status="completed", metrics={"prepared_snapshot": manifest, "unique_deals": 1}, finished_at=store.now())
        store.execute("UPDATE deals SET fields=? WHERE id='example'", (store.encode({**records[0]["fields"], "geography": "Current location"}),))
        history = historical_run(preparation)
        self.assertEqual(history["snapshot_records"][0]["fields"]["geography"], "New York")
        self.assertEqual(history["decisions"], [])
        self.assertEqual(history["historical"]["snapshot_kind"], "prepared")
        (store.DATA / manifest["path"]).write_text('[]')
        self.assertFalse(historical_run(preparation)["historical"]["snapshot_available"])
        self.assertIn("integrity", historical_run(preparation)["historical"]["snapshot_message"])

    def test_snapshot_path_cannot_escape_data_directory(self):
        store.update_run(self.run, metrics={"input_snapshot": {"path": "../private.json", "sha256": "fake"}})
        self.assertFalse(historical_run(self.run)["historical"]["snapshot_available"])

    def test_failed_attempt_is_not_described_as_original_completed_results(self):
        store.update_run(self.run, status="failed", error="Publication failed")
        history = historical_run(self.run)
        self.assertEqual(history["status"], "failed")
        self.assertEqual(history["error"], "Publication failed")
        self.assertEqual(history["historical"]["result_version"], "saved")

    def test_clear_history_preserves_dataset_sources_and_user_corrections(self):
        correct_fields("example", {"amount": "$12 million"}, "Human correction", "", "", self.fixture.deal["fields"], self.run)
        correct_decision(self.run, "example", "No", "", self.fixture.final)
        store.execute("INSERT INTO messages VALUES(?,?,?,?,?)", ("email", "Term Sheet", "2026-09-18", "Test", '{}'))
        store.execute("INSERT INTO blocks(id,week_id,message_id,source,received_at,text) VALUES(?,?,?,?,?,?)", ("source", self.week, "email", "Term Sheet", "2026-09-18", "Original newsletter"))
        preserved = {table: store.query("SELECT * FROM " + table) for table in ["deals", "weeks", "messages", "blocks", "feedback", "field_overrides", "settings"]}
        self.assertEqual(clear_history()["runs"], 1)
        self.assertEqual(len(store.query("SELECT * FROM runs")), 1)
        result = clear_history(apply=True)
        with closing(sqlite3.connect(result["backup"])) as backup:
            self.assertEqual(backup.execute("SELECT COUNT(*) FROM runs").fetchone()[0], 1)
        for table, rows in preserved.items():
            self.assertEqual(store.query("SELECT * FROM " + table), rows)
        for table in ["runs", "decisions", "events", "review_records", "review_baselines"]:
            self.assertEqual(store.query("SELECT * FROM " + table), [])
        self.assertEqual(clear_history(apply=True)["runs"], 0)
        self.assertEqual(store.query("PRAGMA foreign_key_check"), [])

    def test_running_workflow_blocks_clear_without_removing_history(self):
        store.new_run(self.week, "prepare", "local")
        with self.assertRaisesRegex(ValueError, "running workflow"):
            clear_history(apply=True)
        self.assertEqual(len(store.query("SELECT * FROM runs")), 2)


if __name__ == "__main__":
    unittest.main()
