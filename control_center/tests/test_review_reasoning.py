import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from jsonschema import validate, ValidationError

from control_center import pipeline
from control_center.models import review_output_schema, DECISION_SCHEMA, usage_totals


class ReviewReasoningTests(unittest.TestCase):
    def test_schema_binds_ids_and_exact_batch_size_without_mutating_shared_schema(self):
        schema=review_output_schema(['D1','D2'],independent=True)
        items=schema['properties']['decisions']
        self.assertEqual(items['minItems'],2)
        self.assertEqual(items['maxItems'],2)
        self.assertEqual(items['items']['properties']['id']['enum'],['D1','D2'])
        self.assertEqual(items['items']['properties']['fit']['enum'],['Yes','No'])
        self.assertNotIn('enum',DECISION_SCHEMA['properties']['decisions']['items']['properties']['id'])
        answer={'id':'D1','fit':'No','confidence':.99,'reason':'Source-backed exclusion','evidence_ids':['E1'],'known_investment':False,'field_corrections':[]}
        with self.assertRaises(ValidationError):
            validate({'decisions':[answer,{**answer,'id':'D23'}]},schema)

    def test_extra_id_retries_instead_of_dropping_or_reassigning_decisions(self):
        record={'id':'original','company':'Example','fields':{},'evidence':[{'id':'source','quote':'Product evidence'}]}
        answer={'id':'D1','fit':'No','confidence':.99,'reason':'Example is outside the mandate','evidence_ids':['E1'],'known_investment':False,'field_corrections':[]}
        with tempfile.TemporaryDirectory() as directory:
            metadata={'artifact':directory,'model':'test','seconds':1,'usage':{'input_tokens':10,'output_tokens':5}}
            outputs=[({'decisions':[copy.deepcopy(answer),{**answer,'id':'D23'}]},metadata),({'decisions':[copy.deepcopy(answer)]},metadata)]
            with patch.object(pipeline,'run_model',side_effect=outputs) as model,patch.object(pipeline.store,'event') as event:
                decisions,meta=pipeline.classify_batch([record],'test',mandate='Test mandate')
            self.assertEqual([decision['id'] for decision in decisions],['original'])
            self.assertEqual(model.call_count,2)
            self.assertEqual(meta['attempt_count'],2)
            self.assertEqual(meta['usage']['input_tokens'],20)
            self.assertEqual(usage_totals([meta])['by_model']['test']['jobs'],2)
            self.assertIn('D23',event.call_args.args[3]['reason'])
            self.assertEqual(json.loads(model.call_args.args[0].split('\nDATA:\n',1)[1])[0]['id'],'D1')

    def test_repeat_contract_failure_stops_after_one_retry(self):
        record={'id':'original','company':'Example','fields':{},'evidence':[{'id':'source'}]}
        with tempfile.TemporaryDirectory() as directory,patch.object(pipeline,'run_model',return_value=({'decisions':[]},{'artifact':directory})) as model,patch.object(pipeline.store,'event'):
            with self.assertRaisesRegex(ValueError,'after one retry'):
                pipeline.classify_batch([record],'test',mandate='Test mandate')
            self.assertEqual(model.call_count,2)

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
