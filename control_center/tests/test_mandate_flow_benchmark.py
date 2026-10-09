import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from control_center.experiments import mandate_flow_benchmark as benchmark


class MandateFlowBenchmarkTests(unittest.TestCase):
    def test_compact_schema_keeps_evidence_and_routing_without_expanded_assessments(self):
        schema = benchmark.batch_schema(['D1'], 'compact')
        properties = schema['properties']['decisions']['items']['properties']
        self.assertEqual(set(properties), {'id', 'fit', 'known_investment', 'evidence_ids', 'reason', 'decisive_evidence_supported', 'missing_fact_queries'})
        self.assertEqual(properties['id']['enum'], ['D1'])
        self.assertIn('criteria', benchmark.DECISION['properties'])

    def test_blind_reviews_route_disagreement_and_unsupported_agreement_through_research(self):
        records = [{"id": str(i), "company": 'Company '+str(i), "fields": {"website": 'https://example.com'}, "evidence": [{"id": 'source-'+str(i), "quote": 'A built-world product'}]} for i in range(4)]
        calls = []
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            mandate = folder/'mandate.md'
            mandate.write_text('Include built-world products')

            def fake_model(prompt, schema, model, label, research=False, **kwargs):
                calls.append((prompt,label,research))
                artifact = folder/uuid4().hex
                artifact.mkdir()
                metadata = {"artifact": str(artifact), "seconds": .001, "model": model, "web_calls": int(research)}
                if research:
                    return {"findings": [{"question": 'Who are the customers?', "answer": 'Construction firms', "supported": True, "source_url": 'https://example.com/customers', "quote": 'Our customers are construction firms'}]}, metadata
                data = json.loads(prompt.split('\nDATA:\n')[1].split('\nCONTEXT:\n')[0])
                values = []
                for record in data:
                    fit = 'No' if '/reviewer_b/' in label and record['company']=='Company 0' else 'Yes'
                    gap = record['company']=='Company 1' and '/final_resolution/' not in label
                    values.append({"id":record['id'],"fit":fit,"known_investment":False,"product":"Built-world software","customers_use_case":"Construction firms","geography":"United States","criteria":[{"criterion":"product_customer_fit","assessment":"supported","evidence_ids":["E1"],"explanation":"Source-backed product"}],"evidence_ids":["E1"],"reason":record['company']+' sells built-world software',"decisive_evidence_supported":not gap,"missing_fact_queries":['Who are the customers?'] if gap else []})
                return {"decisions":values},metadata

            with patch.object(benchmark.config,'ARTIFACTS',folder),patch.object(benchmark.config,'MANDATE',mandate),patch.object(benchmark.pipeline,'prepared_deals',return_value=(records,{})),patch.object(benchmark.pipeline,'review_inputs',return_value=records),patch.object(benchmark.models,'run_model',side_effect=fake_model),patch.object(benchmark.store,'execute') as writes,patch.object(sys,'argv',['benchmark','--week','week']):
                benchmark.main()
                writes.assert_not_called()
            report = json.loads(next(folder.glob('mandate-flow-benchmark-*/report.json')).read_text())
            self.assertEqual(report['status'],'completed')
            self.assertEqual(report['agreements_accepted'],2)
            self.assertEqual(report['disagreements'],1)
            self.assertEqual(report['unsupported_agreements'],1)
            self.assertEqual(report['adjudicated'],2)
            self.assertEqual(report['researched'],1)
            self.assertEqual(report['yes'],4)
            self.assertEqual(report['no'],0)
            for prompt,label,_ in calls:
                if '/reviewer_' in label:
                    self.assertNotIn('independent_reviews',prompt)
            self.assertEqual(set(report['timings']),{'dual_review','agreement_routing','adjudication','targeted_research','final_resolution','total_wall'})
