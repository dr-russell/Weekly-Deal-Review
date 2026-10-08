import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from control_center import pipeline


class ReviewReasoningTests(unittest.TestCase):
    def test_both_review_passes_receive_company_identity_and_reason_contract(self):
        record = {
            "id": "altis-record",
            "company": "Altis Labs",
            "fields": {"geography": "Toronto, Ontario, Canada"},
            "evidence": [{"id": "altis-source", "quote": "AI imaging for cancer trials"}],
        }
        answer = {"decisions": [{
            "id": "D1", "fit": "No", "confidence": .99,
            "reason": "Altis Labs serves oncology trials, outside built-world markets; Toronto is within the eligible geography.",
            "evidence_ids": ["E1"], "known_investment": False,
            "field_corrections": [],
        }]}
        with tempfile.TemporaryDirectory() as directory:
            for independent in (False, True):
                with self.subTest(independent=independent), patch.object(
                    pipeline, "run_model", return_value=(copy.deepcopy(answer), {"artifact": directory})
                ) as model:
                    decisions, _ = pipeline.classify_batch([record], "test", independent=independent, mandate="US/Canada built-world companies")
                prompt = model.call_args.args[0]
                self.assertIn("start with the record's exact company name", prompt)
                self.assertIn("state whether it affects eligibility", prompt)
                supplied = json.loads(prompt.split("\nDATA:\n", 1)[1])
                self.assertEqual(supplied[0]["company"], "Altis Labs")
                self.assertEqual(supplied[0]["fields"]["geography"], "Toronto, Ontario, Canada")
                self.assertEqual(decisions[0]["id"], record["id"])
                self.assertEqual(decisions[0]["evidence_ids"], ["altis-source"])
                self.assertEqual(decisions[0]["fit"], "No")
                self.assertTrue((Path(directory) / "evidence_map.json").exists())


if __name__ == "__main__":
    unittest.main()
