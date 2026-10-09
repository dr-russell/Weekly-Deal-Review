import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from control_center import store, pipeline
from control_center.corrections import CorrectionError, apply_saved_fields, correct_decision, correct_fields, correct_record_decision, publish_corrections, recheck_company, user_decision
from control_center.logic import make_package


class CorrectionTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        root = Path(self.folder.name)
        self.patches = [patch.object(store, "DATA", root), patch.object(store, "DB", root / "test.sqlite"), patch.object(store, "ARTIFACTS", root / "artifacts"), patch.object(pipeline, "DATA", root)]
        for item in self.patches:
            item.start()
        store.initialize()
        self.at = store.now()
        self.week = "2026-09-16_2026-09-22"
        store.execute("INSERT INTO weeks(id,start,end,label,status,updated_at) VALUES(?,?,?,?,?,?)", (self.week, "2026-09-16", "2026-09-22", "Test week", "prepared", self.at))
        self.deal = {"id": "example", "company": "Example", "fields": {"growth_stage": "Early", "deal_date": "2026-09-18", "website": "https://example.com", "round": "Series A", "amount": "$10 million", "investors": "Alpha", "sector": "Construction & Proptech", "description": "Contractor estimating software", "geography": "New York", "announcement_link": "https://example.com/news"}, "block_ids": ["source"], "sources": ["Term Sheet"], "evidence": [], "missing": {}, "portfolio_match": {}}
        store.execute("INSERT INTO deals(id,week_id,company,entity_key,fields,block_ids,sources,evidence,missing,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)", (self.deal["id"], self.week, "Example", "example", store.encode(self.deal["fields"]), '["source"]', '["Term Sheet"]', '[]', '{}', self.at))
        self.first = {"fit": "Maybe", "reason": "Uncertain fit", "confidence": .8, "known_investment": False, "evidence_ids": ["source"]}
        self.final = {**self.first, "fit": "Yes", "reason": "Contractor software in the US", "confidence": .95}
        self.run = store.new_run(self.week, "review", "test")
        package = make_package([self.deal], {"example": self.final}, "2026-09-16", "2026-09-22")
        store.update_run(self.run, status="completed", stage="complete", elapsed=42, package=package, metrics={**package["stats"], "prepared_at": self.at, "uncertain": 1, "verified": 1, "changes": 1, "known_investments": 0})
        store.execute("INSERT INTO decisions(run_id,deal_id,first_pass,verification,final) VALUES(?,?,?,?,?)", (self.run, "example", store.encode(self.first), store.encode(self.final), store.encode(self.final)))

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.folder.cleanup()

    def correct(self, fit="No", expected=None):
        return correct_decision(self.run, "example", fit, "User confirmed mandate eligibility", expected or self.final)

    def test_correction_updates_final_package_and_totals_preserving_ai_checks(self):
        result = self.correct()
        decision = result["decisions"][0]
        self.assertEqual(decision["first_pass"], self.first)
        self.assertEqual(decision["verification"], self.final)
        self.assertEqual(decision["final"]["fit"], "No")
        self.assertEqual(decision["final"]["manual_override"]["model_final"], self.final)
        self.assertEqual(result["package"]["rows"], [])
        self.assertEqual(result["metrics"]["count"], 0)
        self.assertEqual(result["metrics"]["capital_usd_m"], 0)
        self.assertEqual(result["metrics"]["excluded"], 1)
        self.assertEqual(result["metrics"]["row_completeness"]["selected_rows"], 0)
        self.assertTrue(result["metrics"]["outputs_dirty"])
        self.assertEqual(result["elapsed"], 42)
        self.assertEqual(result["events"][-1]["detail"]["previous"], self.final)
        self.assertEqual(store.query("SELECT fit FROM feedback WHERE active=1")[0]["fit"], "No")

    def test_optional_decision_reason_and_source_are_retained(self):
        result = correct_decision(self.run, "example", "No", "", self.final, "https://example.com/contact", "source")
        final = result["decisions"][0]["final"]
        self.assertFalse(final["manual_override"]["reason_provided"])
        self.assertEqual(final["manual_override"]["source_url"], "https://example.com/contact")
        self.assertEqual(final["manual_override"]["evidence_id"], "source")
        self.assertEqual(final["manual_override"]["model_final"], self.final)
        feedback = store.query("SELECT * FROM feedback WHERE active=1")[0]
        self.assertEqual(feedback["source_url"], "https://example.com/contact")
        self.assertEqual(user_decision(final, feedback), final)

    def test_inline_fields_save_without_inventing_a_source(self):
        correct_fields("example", {"amount": "$12 million", "geography": "New York, NY, US"}, "", "", "", self.deal["fields"], self.run)
        fields = store.get_deals(self.week)[0]["fields"]
        self.assertEqual(fields["geography"], "New York, New York, United States")
        self.assertEqual(fields["amount"], "$12 million")
        history = store.query("SELECT * FROM field_overrides")
        self.assertEqual(len(history), 2)
        self.assertTrue(all(h["source_url"] == "" and h["evidence_id"] == "" for h in history))
        self.assertTrue(all(h["reason"] == "User edited company details." for h in history))

    def test_current_record_decision_rebuilds_current_review(self):
        result = correct_record_decision("example", "No", "", "", "", self.final, self.deal["fields"])
        self.assertEqual(result["run_id"], self.run)
        self.assertFalse(result["review_required"])
        self.assertEqual(store.get_run(self.run)["package"]["rows"], [])

    def test_stale_record_decision_preserves_historical_publication(self):
        from control_center.app import deals, deal_detail
        original = store.get_run(self.run)
        store.execute("UPDATE weeks SET updated_at='new-inputs'")
        result = correct_record_decision("example", "No", "New customer evidence", "https://example.com/new", "source", self.final, self.deal["fields"])
        self.assertIsNone(result["run_id"])
        self.assertTrue(result["review_required"])
        current = store.get_run(self.run)
        self.assertEqual(current["package"], original["package"])
        self.assertEqual(current["decisions"], original["decisions"])
        with patch("control_center.fx.prepared_rates", return_value=({}, None)), patch.object(pipeline, "source_evidence", return_value=[]):
            value = deal_detail("example")
            self.assertTrue(value["editable_decision"])
            self.assertEqual(value["review_decision"]["final"]["fit"], "No")
            self.assertEqual(deals(self.week, fit="No")[0]["decision"]["final"]["fit"], "No")
            historical = deal_detail("example", self.run)
            self.assertFalse(historical["editable_decision"])
            self.assertEqual(historical["review_decision"]["final"]["fit"], "Yes")
        with self.assertRaisesRegex(CorrectionError, "decision changed"):
            correct_record_decision("example", "Yes", "", "", "", self.final, self.deal["fields"])

    def test_unreviewed_decision_has_history_and_can_be_changed(self):
        store.execute("DELETE FROM decisions")
        store.execute("DELETE FROM runs")
        result = correct_record_decision("example", "Yes", "", "", "", None, self.deal["fields"])
        self.assertTrue(result["review_required"])
        feedback = store.query("SELECT * FROM feedback WHERE active=1")[0]
        previous = user_decision(None, feedback)
        self.assertEqual(previous["fit"], "Yes")
        correct_record_decision("example", "No", "Changed mandate fit", "", "source", previous, self.deal["fields"])
        self.assertEqual(len(store.query("SELECT * FROM feedback")), 2)
        self.assertEqual(len(store.query("SELECT * FROM feedback WHERE active=1")), 1)

    def test_decision_source_and_record_guards_reject_without_writes(self):
        for url, evidence, fields, pattern in [
            ("not-a-url", "", self.deal["fields"], "valid source URL"),
            ("", "other-company", self.deal["fields"], "belonging"),
            ("", "", {}, "fields changed"),
        ]:
            with self.subTest(pattern=pattern), self.assertRaisesRegex(CorrectionError, pattern):
                correct_record_decision("example", "No", "", url, evidence, self.final, fields)
        self.assertEqual(store.query("SELECT * FROM feedback"), [])

    def test_reason_can_be_updated_without_changing_fit(self):
        result = correct_decision(self.run, "example", "Yes", "Confirmed customer evidence", self.final)
        self.assertEqual(result["decisions"][0]["final"]["reason"], "Confirmed customer evidence")

    def test_second_correction_retains_original_model_answer_and_audit(self):
        previous = self.correct()["decisions"][0]["final"]
        result = self.correct("Yes", previous)
        self.assertEqual(result["metrics"]["count"], 1)
        self.assertEqual(result["metrics"]["capital_usd_m"], 10)
        self.assertEqual(result["decisions"][0]["final"]["manual_override"]["model_final"], self.final)
        self.assertEqual(len(result["events"]), 2)
        self.assertEqual(len(store.query("SELECT * FROM feedback")), 2)
        self.assertEqual(len(store.query("SELECT * FROM feedback WHERE active=1")), 1)

    def test_stale_edit_and_same_fit_are_rejected_without_writes(self):
        stale = {**self.final, "confidence": .2}
        with self.assertRaisesRegex(CorrectionError, "changed since"):
            self.correct(expected=stale)
        with self.assertRaisesRegex(CorrectionError, "different final"):
            correct_decision(self.run, "example", "Yes", "", self.final)
        self.assertFalse(store.get_run(self.run)["events"])
        self.assertFalse(store.query("SELECT * FROM feedback"))

    def test_portfolio_and_running_reviews_are_not_editable(self):
        known = {**self.final, "known_investment": True}
        store.execute("UPDATE decisions SET final=?", (store.encode(known),))
        with self.assertRaisesRegex(CorrectionError, "Portfolio exclusions"):
            self.correct(expected=known)
        store.update_run(self.run, status="running")
        with self.assertRaisesRegex(CorrectionError, "Only completed"):
            self.correct()

    def test_older_review_and_changed_preparation_are_rejected(self):
        newer = store.new_run(self.week, "review", "local")
        store.update_run(newer, status="local_completed")
        with self.assertRaisesRegex(CorrectionError, "latest completed"):
            self.correct()
        store.update_run(newer, status="failed")
        store.execute("UPDATE weeks SET updated_at='changed'")
        with self.assertRaisesRegex(CorrectionError, "Prepared inputs changed"):
            self.correct()

    def test_validation_failure_rolls_back_correction(self):
        previous = self.correct()["decisions"][0]["final"]
        bad_fields = {**self.deal["fields"], "description": ""}
        store.execute("UPDATE deals SET fields=?", (store.encode(bad_fields),))
        with self.assertRaisesRegex(CorrectionError, "required description missing"):
            self.correct("Yes", previous)
        result = store.get_run(self.run)
        self.assertEqual(result["decisions"][0]["final"], previous)
        self.assertEqual(len(result["events"]), 1)
        self.assertEqual(len(store.query("SELECT * FROM feedback")), 1)

    def test_local_correction_does_not_mark_google_outputs_dirty(self):
        store.execute("UPDATE runs SET target='local',status='local_completed' WHERE id=?", (self.run,))
        result = self.correct()
        self.assertFalse(result["metrics"]["outputs_dirty"])
        with self.assertRaisesRegex(CorrectionError, "local-only"):
            publish_corrections(self.run)

    def test_refresh_publishes_corrected_package_without_ai_and_preserves_run_timer(self):
        self.correct()
        with patch("control_center.corrections.Publisher") as publisher:
            publisher.return_value.publish.return_value = {"writes": {"sheet": True, "doc": True}}
            result = publish_corrections(self.run)
            publisher.assert_called_once_with("test")
            publisher.return_value.verify.assert_called_once()
            package = publisher.return_value.publish.call_args.args[0]
            self.assertEqual(package["rows"], [])
            self.assertIn("/corrections/", publisher.return_value.publish.call_args.args[2])
        self.assertFalse(result["metrics"]["outputs_dirty"])
        self.assertEqual(result["elapsed"], 42)
        self.assertFalse(result["events"][-1]["detail"]["ai_rerun"])

    def test_publication_failure_leaves_retryable_pending_corrections(self):
        self.correct()
        with patch("control_center.corrections.Publisher") as publisher:
            publisher.return_value.verify.side_effect = RuntimeError("Readback mismatch")
            with self.assertRaisesRegex(RuntimeError, "Readback mismatch"):
                publish_corrections(self.run)
        result = store.get_run(self.run)
        self.assertTrue(result["metrics"]["outputs_dirty"])
        self.assertEqual(result["metrics"]["correction_publication_error"], "Readback mismatch")
        self.assertTrue(result["events"][-1]["detail"]["google_may_be_partially_updated"])

    def test_missing_or_invalid_decision_is_rejected(self):
        with self.assertRaisesRegex(CorrectionError, "Decision not found"):
            correct_decision(self.run, "missing", "No", "Outside mandate", self.final)
        with self.assertRaisesRegex(CorrectionError, "Choose Yes or No"):
            correct_decision(self.run, "example", "Maybe", "Uncertain", self.final)
        result = correct_decision(self.run, "example", "No", "     ", self.final)
        self.assertFalse(result["decisions"][0]["final"]["manual_override"]["reason_provided"])

    def edit_field(self, field="amount", value="$12 million", **kwargs):
        current = store.get_deals(self.week)[0]
        return correct_fields("example", {field: value}, "Confirmed from the company announcement", "https://example.com/news", "", current["fields"], self.run, **kwargs)

    def test_field_edit_updates_package_frozen_inputs_history_and_baseline(self):
        self.edit_field()
        run = store.get_run(self.run)
        self.assertEqual(run["metrics"]["capital_usd_m"], 12)
        self.assertEqual(run["elapsed"], 42)
        self.assertTrue(run["metrics"]["outputs_dirty"])
        baseline = json.loads(store.query("SELECT payload FROM review_baselines")[0]["payload"])
        self.assertEqual(baseline["deals"][0]["fields"]["amount"], "$10 million")
        frozen, _ = pipeline.prepared_deals(self.week)
        self.assertEqual(frozen[0]["fields"]["amount"], "$12 million")
        clean = {**self.deal, "fields": dict(self.deal["fields"]), "evidence": [], "missing": {}}
        apply_saved_fields(clean)
        self.assertEqual(clean["fields"]["amount"], "$12 million")
        self.assertEqual(store.query("SELECT field FROM field_overrides WHERE active=1")[0]["field"], "amount")

    def test_fit_affecting_edit_requires_recheck_or_explicit_confirmation(self):
        self.edit_field("geography", "Toronto, Canada")
        self.assertEqual(store.get_run(self.run)["metrics"]["pending_rechecks"], ["example"])
        with self.assertRaisesRegex(CorrectionError, "Confirm or recheck"):
            publish_corrections(self.run)
        result = correct_decision(self.run, "example", "Yes", "Confirmed eligibility with corrected geography", self.final)
        self.assertEqual(result["metrics"]["pending_rechecks"], [])

    def test_targeted_recheck_preserves_original_checks_and_user_override(self):
        self.correct()
        self.edit_field("geography", "Toronto, Canada")
        suggested = {**self.final, "fit": "Yes", "field_corrections": []}
        with patch.object(pipeline, "classify_batch", return_value=([suggested], {"seconds": 1})):
            result = recheck_company(self.run, "example")
        row = result["decisions"][0]
        self.assertEqual(row["final"]["fit"], "No")
        self.assertEqual(row["final"]["latest_recheck"]["fit"], "Yes")
        self.assertEqual(row["first_pass"], self.first)
        self.assertEqual(row["verification"], self.final)
        self.assertEqual(result["metrics"]["pending_rechecks"], [])
        self.assertEqual(result["elapsed"], 42)

    def test_field_edit_rejects_stale_unsupported_and_cross_week_changes(self):
        for changes, expected, url, evidence, pattern in [
            ({"amount": "$12 million"}, {}, "https://example.com", "", "changed since"),
            ({"amount": "$12 million"}, self.deal["fields"], "", "other-company", "belonging"),
            ({"amount": "$12 million"}, self.deal["fields"], "invalid-source", "", "valid source URL"),
            ({"deal_date": "2026-09-30"}, self.deal["fields"], "https://example.com", "", "within this IC week"),
            ({"website": "https://click.mail.fortune.com/?qs=123"}, self.deal["fields"], "https://example.com", "", "tracking link"),
            ({"amount": "lots of money"}, self.deal["fields"], "https://example.com", "", "currency")
        ]:
            with self.subTest(changes=changes, pattern=pattern), self.assertRaisesRegex(CorrectionError, pattern):
                correct_fields("example", changes, "Confirmed detail", url, evidence, expected, self.run)
        self.assertFalse(store.query("SELECT * FROM field_overrides"))

    def test_known_identity_cannot_be_human_confirmed_into_outputs(self):
        self.edit_field("website", "https://another.example.com")
        store.execute("UPDATE review_records SET record=? WHERE run_id=? AND deal_id=?", (store.encode({**store.get_deals(self.week)[0], "portfolio_match": {"status": "known"}}), self.run, "example"))
        with self.assertRaisesRegex(CorrectionError, "existing investment"):
            correct_decision(self.run, "example", "Yes", "Still appears relevant", self.final)

    def test_field_correction_before_review_is_saved_without_publishing(self):
        store.execute("DELETE FROM decisions")
        store.execute("DELETE FROM runs")
        result = correct_fields("example", {"geography": "Toronto, Canada"}, "Official contact page", "https://example.com/contact", "", self.deal["fields"])
        self.assertIsNone(result["run_id"])
        self.assertEqual(store.get_deals(self.week)[0]["fields"]["geography"], "Toronto, Canada")
        self.assertFalse(store.query("SELECT * FROM runs"))

    def test_excluded_factual_edit_does_not_require_unchanged_outputs_to_be_republished(self):
        self.correct()
        with patch("control_center.corrections.Publisher") as publisher:
            publisher.return_value.publish.return_value = {}
            publish_corrections(self.run)
        self.edit_field("investors", "Example Capital")
        result = store.get_run(self.run)
        self.assertFalse(result["metrics"]["outputs_dirty"])
        self.assertEqual(result["metrics"]["count"], 0)
