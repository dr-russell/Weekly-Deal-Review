import copy
import json
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from control_center import app, pipeline, store
from control_center.dataset import combined_records, dataset_summary, prepare_selected
from control_center.history import historical_run
from control_center.tests import test_corrections


class DatasetSelectionTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_corrections.CorrectionTests()
        self.fixture.setUp()
        self.first = self.fixture.week
        self.second = "2026-09-23_2026-09-29"
        store.execute("UPDATE weeks SET report=? WHERE id=?", ('{"deal_mentions":2,"duplicate_mentions":1}', self.first))
        store.execute("INSERT INTO weeks(id,start,end,label,status,report,updated_at) VALUES(?,?,?,?,?,?,?)", (self.second, "2026-09-23", "2026-09-29", "Second week", "prepared", '{"deal_mentions":2,"duplicate_mentions":1}', store.now()))
        record = copy.deepcopy(store.get_deals(self.first)[0])
        record.update(id="repeat", week_id=self.second)
        record["fields"].update(deal_date="2026-09-24", geography="")
        record["missing"] = {"geography": "Not provided in this source"}
        pipeline.save_deal(self.second, record)

    def tearDown(self):
        self.fixture.tearDown()

    def test_combined_counts_merge_repeated_financing_and_use_supported_fields(self):
        summary = dataset_summary([self.first, self.second, self.first])
        self.assertEqual(summary["status"], "prepared")
        self.assertEqual(summary["deal_count"], 1)
        self.assertEqual(summary["duplicate_mentions"], 3)
        self.assertEqual(summary["remaining_missing_count"], 0)
        self.assertEqual(store.get_deals(self.second)[0]["fields"]["geography"], "")

    def test_database_week_selection_dedupes_only_selected_weeks(self):
        record = copy.deepcopy(store.get_deals(self.second)[0])
        record.update(id="other-company", company="Other Company")
        record["fields"]["website"] = "https://other.example.com"
        pipeline.save_deal(self.second, record)
        with patch("control_center.fx.prepared_rates", return_value=({}, None)):
            self.assertEqual(app.dataset_deals([], "", "All"), [])
            self.assertEqual(len(app.dataset_deals([self.first], "", "All")), 1)
            self.assertEqual(len(app.dataset_deals([self.first, self.second], "", "All")), 2)
            self.assertEqual([item["company"] for item in app.dataset_deals([self.second], "Other Company", "All")], ["Other Company"])
            with self.assertRaises(HTTPException):
                app.dataset_deals(["unknown"], "", "All")

    def test_separate_rounds_remain_distinct_and_unresolved_cells_count_once(self):
        record = store.get_deals(self.second)[0]
        record.update(id="new-round")
        record["fields"].update(round="Series B", deal_date="2026-09-25")
        pipeline.save_deal(self.second, record)
        store.execute("UPDATE weeks SET report=? WHERE id=?", ('{"deal_mentions":3}', self.second))
        summary = dataset_summary([self.first, self.second])
        self.assertEqual(summary["deal_count"], 2)
        self.assertEqual(summary["duplicate_mentions"], 3)
        self.assertEqual(summary["remaining_missing_count"], 1)
        self.assertEqual(summary["remaining_missing_fields"][0]["deal_id"], "new-round")

    def test_incomplete_selection_never_returns_provisional_counts(self):
        store.execute("UPDATE weeks SET status='raw' WHERE id=?", (self.second,))
        summary = dataset_summary([self.first, self.second])
        self.assertEqual(summary["pending_weeks"], [self.second])
        self.assertIsNone(summary["deal_count"])
        self.assertIsNone(summary["duplicate_mentions"])
        self.assertIsNone(summary["remaining_missing_count"])
        for ids in ([], ["unknown"]):
            with self.assertRaises(ValueError):
                dataset_summary(ids)

    def test_batch_prepares_only_pending_weeks_and_preserves_single_week_snapshots(self):
        before = pipeline.freeze_prepared(self.first, store.get_deals(self.first))
        store.execute("UPDATE weeks SET status='raw' WHERE id=?", (self.second,))
        run = store.new_run(self.second, "prepare_batch", "local")
        calls = []
        def prepare(rid, wid, collect, finalize):
            calls.append((wid, collect, finalize))
            self.assertEqual(store.get_run(rid)["status"], "running")
            store.execute("UPDATE weeks SET status='prepared' WHERE id=?", (wid,))
        with patch.object(pipeline, "prepare", side_effect=prepare):
            prepare_selected(run, [self.first, self.second], collect=True)
        self.assertEqual(calls, [(self.second, True, False)])
        self.assertEqual(store.get_run(run)["status"], "completed")
        self.assertEqual(historical_run(run)["historical"]["snapshot_count"], 1)
        self.assertEqual(pipeline.prepared_deals(self.first)[1], before)

    def test_batch_failure_keeps_prepared_weeks_and_records_retryable_pending_weeks(self):
        store.execute("UPDATE weeks SET status='raw' WHERE id=?", (self.second,))
        run = store.new_run(self.second, "prepare_batch", "local")
        with patch.object(pipeline, "prepare", side_effect=RuntimeError("Collection unavailable")):
            prepare_selected(run, [self.first, self.second])
        result = store.get_run(run)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["metrics"]["pending_weeks"], [self.second])
        self.assertEqual(dataset_summary([self.first])["status"], "prepared")

    def test_ready_selection_creates_no_new_run_and_read_only_workspace_rejects_batch(self):
        before = len(store.query("SELECT id FROM runs"))
        result = app.prepare_dataset(app.DatasetJob(week_ids=[self.first, self.second]))
        self.assertEqual(result, {"run_id": None, "prepared": True})
        self.assertEqual(len(store.query("SELECT id FROM runs")), before)
        self.assertFalse(app.JOB_LOCK.locked())
        with patch.object(app.app.state, "workspace", {"allow_preparation": False}), self.assertRaises(HTTPException) as error:
            app.prepare_dataset(app.DatasetJob(week_ids=[self.first]))
        self.assertEqual(error.exception.status_code, 403)

    def test_combined_review_and_publication_use_all_selected_weeks(self):
        from control_center import google
        from control_center.corrections import correct_decision
        from control_center.config import HEADERS
        third = "2026-09-30_2026-10-06"
        store.execute("INSERT INTO weeks(id,start,end,label,status,report,updated_at) VALUES(?,?,?,?,?,?,?)", (third,"2026-09-30","2026-10-06","Third","prepared",'{}',store.now()))
        record = copy.deepcopy(store.get_deals(self.first)[0])
        record.update(id="different",company="Different")
        record["fields"].update(website="https://different.example/",announcement_link="https://different.example/news",deal_date="2026-09-30")
        pipeline.save_deal(third,record)
        for wid in [self.first,third]:
            pipeline.freeze_prepared(wid,store.get_deals(wid))
        run = store.new_run(self.first,"review","test")
        def review(payload,*args,**kwargs):
            self.assertEqual({row["id"] for row in payload},{"example","different"})
            values={row["id"]:{**self.fixture.final,"id":row["id"],"field_corrections":[]} for row in payload}
            return values,{},[],0,{"review":1,"verification":0}
        with patch.object(pipeline,"review_batches",side_effect=review),patch("control_center.google.Publisher") as publisher,patch("control_center.google.linked_charts",return_value={}):
            publisher.return_value.publish.return_value={"written_rows":2}
            pipeline.review_and_publish(run,self.first,"test",[self.first,third])
            package=publisher.return_value.publish.call_args.args[0]
            publisher.return_value.verify.assert_called_once()
        self.assertEqual(len(package["rows"]),2)
        self.assertEqual([period["id"] for period in package["selection_weeks"]],[self.first,third])
        self.assertEqual([period["count"] for period in package["week_stats"]],[1,1])
        self.assertEqual(store.get_run(run)["metrics"]["week_ids"],[self.first,third])
        self.assertEqual(app.latest_dataset_review([third,self.first])["id"],run)
        self.assertEqual(app.latest_review(self.first)["id"],self.fixture.run)
        self.assertEqual(google.stats_for(package,[])[0][1][1],1)
        gap=list(package["rows"][0]);gap[2]="2026-09-24";gap[3]="Gap Company"
        old=[HEADERS,package["rows"][1],gap,package["rows"][0]]
        snapshot={"meta":{"sheets":[{"properties":{"title":"List","sheetId":0}},{"properties":{"title":"Stats","sheetId":7}}]},"values":{"values":old}}
        requests,_=google.build_sheet(package,snapshot)
        deleted=[request["deleteDimension"]["range"]["startIndex"] for request in requests if "deleteDimension" in request]
        self.assertEqual(deleted,[3,1])
        current=store.get_run(run)
        corrected=correct_decision(run,"different","No","",next(row["final"] for row in current["decisions"] if row["deal_id"]=="different"))
        self.assertEqual(corrected["metrics"]["count"],1)
        self.assertEqual([period["count"] for period in corrected["package"]["week_stats"]],[1,0])
