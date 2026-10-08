import unittest
from unittest.mock import patch

from control_center import app
from control_center.config import FIELDS
from control_center.logic import remaining_missing_fields


def record():
    fields = {key: "Verified value" for key in FIELDS}
    fields.update(website="https://example.com", announcement_link="https://example.com/news", round="", growth_stage="Early")
    return {"id": "example", "company": "Example", "fields": fields, "missing": {"round": "Named round undisclosed"}}


class DatasetTests(unittest.TestCase):
    def test_supported_fallback_and_stale_missing_metadata_do_not_count(self):
        item = record()
        item["missing"]["website"] = "Previously unavailable"
        self.assertEqual(remaining_missing_fields([item]), [])
        self.assertEqual(item["fields"]["round"], "")

    def test_counts_actual_blanks_even_without_documented_reason(self):
        item = record()
        item["fields"].update(geography="   ", website="not-a-url")
        item["missing"]["geography"] = "Conflicting headquarters sources"
        missing = remaining_missing_fields([item])
        self.assertEqual({value["field"] for value in missing}, {"geography", "website"})
        self.assertEqual(next(value for value in missing if value["field"] == "geography")["reason"], "Conflicting headquarters sources")
        self.assertEqual(next(value for value in missing if value["field"] == "website")["reason"], "No verified value recorded")

    def test_overview_uses_current_rows_not_preparation_count(self):
        week = {"id": "week", "report": '{"missing_fields":16}'}
        item = record()
        item["fields"]["geography"] = ""
        with patch.object(app.store, "query", side_effect=[[week], [], [], [], [{"n": 1}], [{"n": 0}], [{"n": 0}]]), patch.object(app.store, "get_deals", return_value=[item]) as get_deals:
            result = app.overview()["weeks"][0]
        get_deals.assert_called_once_with("week")
        self.assertEqual(result["remaining_missing_count"], 1)
        self.assertEqual(result["remaining_missing_fields"][0]["field"], "geography")
        self.assertEqual(result["report"]["missing_fields"], 16)
