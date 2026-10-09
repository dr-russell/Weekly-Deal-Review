import copy
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from control_center import mandate_flow as flow


def answer(record, **values):
    return {"id": record["id"], "fit": "Yes", "known_investment": False,
            "reason": record["company"] + " supplies a documented built-world workflow.",
            "evidence_ids": ["E1"],
            "decisive_evidence_supported": True, "mandate_boundary_uncertain": False,
            "missing_fact_queries": [], **values}


class CompactFlowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)
        self.payload = [{"id": str(i), "company": "Company " + str(i), "fields": {"website": "https://example.com"}, "evidence": [{"id": "source-" + str(i), "quote": "Property workflow"}], "portfolio_match": {"status": "not_matched"}} for i in range(4)]

    def metadata(self):
        artifact = self.root / uuid4().hex
        artifact.mkdir()
        return {"artifact": str(artifact), "model": "mock", "seconds": .01, "usage": {}, "web_calls": 0}

    def test_parallel_blind_reviews_adjudication_research_and_original_disagreement(self):
        barrier = threading.Barrier(2)
        calls = []

        def model(prompt, schema, model, label, research=False, **kwargs):
            calls.append((prompt, label, research))
            if research:
                return {"findings": [{"question": "Who uses the product?", "answer": "Construction firms", "supported": True, "source_url": "https://example.com/customers", "quote": "Construction firms"}]}, self.metadata()
            data = json.JSONDecoder().raw_decode(prompt.split("\nDATA:\n", 1)[1])[0]
            if "/reviewer_" in label:
                # Each reviewer must start without waiting for the other's answer.
                barrier.wait(timeout=3)
                self.assertEqual(json.loads(prompt.split("\nCONTEXT:\n", 1)[1]), {})
            values = []
            for record in data:
                fields = {}
                if record["company"] == "Company 1" and "/reviewer_b/" in label:
                    fields["fit"] = "No"
                if record["company"] == "Company 2" and "/final_resolution/" not in label:
                    fields.update(decisive_evidence_supported=False, missing_fact_queries=["Who uses the product?"])
                if record["company"] == "Company 3" and "/reviewer_" in label:
                    fields["mandate_boundary_uncertain"] = True
                if "/final_resolution/" in label:
                    fields["evidence_ids"] = [record["evidence"][-1]["id"]]
                values.append(answer(record, **fields))
            return {"decisions": values}, self.metadata()

        original = copy.deepcopy(self.payload)
        with patch.object(flow.config, "BATCH_SIZE", 22), patch.object(flow.config, "REVIEW_WORKERS", 2), patch.object(flow.models, "run_model", side_effect=model), patch.object(flow.store, "update_run") as update, patch.object(flow.store, "event"):
            a, b, final, jobs, metrics, timings, evidence = flow.run_flow(self.payload, "run", "Include built-world customers")
        self.assertEqual(self.payload, original)
        self.assertEqual(metrics["accepted_agreements"], 1)
        self.assertEqual(metrics["disagreements"], 1)
        self.assertEqual(metrics["adjudicated"], 3)
        self.assertEqual(metrics["researched"], 1)
        self.assertEqual(b["1"]["fit"], "No")
        self.assertEqual(final["1"]["fit"], "Yes")
        json.dumps(final)  # Unresearched adjudications must not reference themselves.
        self.assertNotIn("review_resolution", final["1"]["review_resolution"]["adjudication"])
        self.assertTrue(final["1"]["review_resolution"]["disagreed"])
        self.assertEqual(final["2"]["review_resolution"]["result"], "researched_and_adjudicated")
        self.assertEqual(final["2"]["evidence_ids"], [evidence["2"][0]["id"]])
        self.assertFalse(any("confidence" in value or "field_corrections" in value for value in final.values()))
        self.assertTrue(metrics["final_resolution_complete"])
        self.assertIn("dual_review", timings)
        self.assertEqual(len(jobs), 5)
        self.assertGreater(update.call_count, 5)
        progress = [call.kwargs for call in update.call_args_list]
        review_counts = [item["metrics"]["reviewed_deals"] for item in progress if item["stage"] == "dual_review"]
        self.assertEqual(review_counts, [0, 0, 4])
        research = next(item["metrics"] for item in progress if item["stage"] == "research")
        self.assertEqual(research["adjudication_ready"], 3)
        self.assertEqual(research["adjudication_resolved"], 2)
        self.assertEqual(metrics["adjudication_resolved"], 3)

    def test_agreement_skips_adjudication_and_research_and_rechecks_do_not_modify_run_progress(self):
        def model(prompt, schema, model, label, **kwargs):
            data = json.JSONDecoder().raw_decode(prompt.split("\nDATA:\n", 1)[1])[0]
            return {"decisions": [answer(record) for record in data]}, self.metadata()
        with patch.object(flow.models, "run_model", side_effect=model), patch.object(flow.store, "update_run") as update, patch.object(flow.store, "event") as event:
            _, _, final, jobs, metrics, timings, evidence = flow.run_flow(self.payload, "run", "mandate", record_progress=False)
        update.assert_not_called()
        event.assert_not_called()
        self.assertEqual(len(jobs), 2)
        self.assertEqual(metrics["adjudicated"], 0)
        self.assertEqual(metrics["researched"], 0)
        self.assertEqual(timings["adjudication"], 0)
        self.assertEqual(evidence, {})
        self.assertTrue(all(d["review_resolution"]["result"] == "supported_agreement" for d in final.values()))

    def test_wrong_citations_retry_and_then_fail_instead_of_accepting_agreement(self):
        def model(prompt, schema, model, label, **kwargs):
            return {"decisions": [answer({"id": "D1", "company": "Company"}, evidence_ids=["E99"])]}, self.metadata()
        with patch.object(flow.models, "run_model", side_effect=model) as call:
            with self.assertRaisesRegex(ValueError, "remained invalid"):
                flow.decision_job(self.payload[:1], "run", "reviewer_a/1", "mandate")
        self.assertEqual(call.call_count, 2)

    def test_routine_corrections_and_financing_caveats_do_not_route_fit_agreements(self):
        a = answer(self.payload[0]); b = copy.deepcopy(a)
        b["field_corrections"] = [{"field": "round", "value": "", "reason": "No round named", "evidence_id": "E1"}]
        self.assertEqual(flow.routing_reasons(a, b, self.payload[0]), [])
        self.assertEqual(flow.routing_reasons(a, a, {**self.payload[0], "data_quality_flags": ["conditional"]}), [])

    def test_supported_research_requires_real_url_and_quote(self):
        invalid = {"findings": [{"question": "Who?", "answer": "Construction", "supported": True, "source_url": "", "quote": ""}]}
        with patch.object(flow.models, "run_model", return_value=(invalid, {})):
            with self.assertRaisesRegex(ValueError, "source URL"):
                flow.research_job(self.payload[0], ["Who?"], "run")

    def test_selected_duplicate_coverage_is_counted_once_but_separate_rounds_remain(self):
        from control_center.logic import make_package, publication_records, validate_selected
        from control_center.tests.test_workflow import deal, decision
        first = deal("Example")
        duplicate = copy.deepcopy(first)
        duplicate.update(id="repeat", block_ids=["second-source"], sources=["Pro Rata"])
        distinct = deal("Example", amount="$5 million", round="Seed")
        distinct["id"] = "seed"
        decisions = {d["id"]: decision() for d in [first, duplicate, distinct]}
        original = copy.deepcopy([first, duplicate, distinct])
        records, changes = publication_records(original, decisions)
        self.assertEqual(len(records), 2)
        self.assertEqual(len(changes), 1)
        self.assertEqual(original, [first, duplicate, distinct])
        self.assertFalse(validate_selected(records, decisions))
        package = make_package(original, decisions, "2026-09-16", "2026-09-22")
        self.assertEqual(package["stats"]["count"], 2)
        self.assertEqual(package["stats"]["capital_usd_m"], 15)
        self.assertTrue(any("Term Sheet; Pro Rata" == row[11] for row in package["rows"]))

    def test_live_pipeline_persists_reviewer_answers_research_and_original_history(self):
        from control_center import pipeline, store
        from control_center.history import historical_run
        from control_center.corrections import correct_decision
        from control_center.tests.test_corrections import CorrectionTests
        fixture = CorrectionTests()
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        record = copy.deepcopy(fixture.deal)
        payload = [{"id": "example", "company": "Example", "fields": record["fields"], "evidence": [{"id": "source", "quote": "Construction customers"}], "portfolio_match": {}}]

        def model(prompt, schema, model, label, research=False, **kwargs):
            if research:
                return {"findings": [{"question": "Who?", "answer": "Construction firms", "supported": True, "source_url": "https://example.com/customers", "quote": "Construction firms"}]}, self.metadata()
            data = json.JSONDecoder().raw_decode(prompt.split("\nDATA:\n", 1)[1])[0]
            values = [answer(item, fit="No" if "/reviewer_b/" in label else "Yes", missing_fact_queries=["Who?"] if "/adjudication/" in label else [], evidence_ids=[item["evidence"][-1]["id"]] if "/final_resolution/" in label else ["E1"]) for item in data]
            return {"decisions": values}, self.metadata()

        run = store.new_run(fixture.week, "review", "local")
        with patch.object(flow.models, "run_model", side_effect=model), patch.object(pipeline, "prepared_deals", return_value=([record], {})), patch.object(pipeline, "review_inputs", return_value=payload), patch("control_center.fx.prepared_rates", return_value=({}, None)), patch("control_center.google.Publisher") as publisher:
            pipeline.review_and_publish(run, fixture.week, "local")
        publisher.assert_not_called()
        self.assertEqual(store.get_deals(fixture.week)[0]["fields"], record["fields"])
        saved = store.get_run(run)
        decision = saved["decisions"][0]
        self.assertEqual(saved["status"], "local_completed")
        self.assertEqual(decision["first_pass"]["fit"], "Yes")
        self.assertEqual(decision["verification"]["fit"], "No")
        self.assertEqual(decision["final"]["fit"], "Yes")
        self.assertEqual(saved["metrics"]["disagreements"], 1)
        self.assertEqual(saved["metrics"]["researched"], 1)
        self.assertTrue(decision["final"]["evidence_ids"][0].startswith("research-"))
        self.assertTrue(any(e.get("url") == "https://example.com/customers" for e in store.get_deals(fixture.week)[0]["evidence"]))
        correct_decision(run, "example", "No", "Human correction", decision["final"])
        current = store.get_run(run)["decisions"][0]
        self.assertEqual(current["verification"]["fit"], "No")
        self.assertTrue(current["final"]["review_resolution"]["disagreed"])
        self.assertEqual(historical_run(run)["decisions"][0]["final"]["fit"], "Yes")
        from control_center.corrections import recheck_company
        with patch.object(flow.models, "run_model", side_effect=model), patch.object(pipeline, "review_inputs", return_value=payload), patch("control_center.fx.prepared_rates", return_value=({}, None)):
            rechecked = recheck_company(run, "example")
        self.assertEqual(rechecked["timings"], saved["timings"])
        self.assertEqual(rechecked["stage"], "complete")
        self.assertEqual(rechecked["decisions"][0]["first_pass"], decision["first_pass"])
        self.assertEqual(rechecked["decisions"][0]["verification"], decision["verification"])
        self.assertEqual(rechecked["decisions"][0]["final"]["fit"], "No")
        self.assertEqual(rechecked["decisions"][0]["final"]["latest_recheck"]["fit"], "Yes")


if __name__ == "__main__":
    unittest.main()
