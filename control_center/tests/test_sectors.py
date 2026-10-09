import hashlib
import json
import unittest
from unittest.mock import patch

from control_center import store,pipeline
from control_center.config import TAXONOMY
from control_center.logic import normalize_fields
from control_center.sectors import SECTORS,VERSION
from control_center.sector_migration import apply_sector_plan
from control_center.tests import test_corrections


class SectorTests(unittest.TestCase):
    def test_starthere_labels_and_ids_are_canonical_and_separate_from_fit(self):
        self.assertEqual(len(TAXONOMY),20)
        self.assertEqual(TAXONOMY,list(SECTORS.values()))
        self.assertNotIn('Outside Mandate',TAXONOMY)
        self.assertNotIn('Adjacent Built World',TAXONOMY)
        self.assertEqual(normalize_fields({'sector':'construction_proptech'})['sector'],'Construction & Proptech')
        self.assertEqual(normalize_fields({'sector':'HEALTHTECH & LIFE SCIENCES'})['sector'],'Healthtech & Life Sciences')

    def test_migration_changes_only_sector_and_preserves_fit_history_and_evidence(self):
        fixture=test_corrections.CorrectionTests();fixture.setUp()
        try:
            original_deals=store.query('SELECT * FROM deals ORDER BY id')
            original_run=store.get_run(fixture.run)
            old_fields=json.loads(original_deals[0]['fields']);old_fields['sector']='Outside Mandate'
            store.execute('UPDATE deals SET fields=?',(store.encode(old_fields),))
            before=store.query('SELECT * FROM deals ORDER BY id')
            previous=pipeline.freeze_prepared(fixture.week,store.get_deals(fixture.week))
            plan={'version':VERSION,'before_sha256':hashlib.sha256(store.encode(before).encode()).hexdigest(),'assignments':{'example':{'sector':'Construction & Proptech','reason':'Contractor estimating software','method':'public_company_business'}}}
            result=apply_sector_plan(plan)
            after=store.query('SELECT * FROM deals ORDER BY id')[0]
            current=json.loads(after['fields'])
            self.assertEqual(current['sector'],'Construction & Proptech')
            self.assertEqual({k:v for k,v in current.items() if k!='sector'},{k:v for k,v in old_fields.items() if k!='sector'})
            self.assertEqual(after['evidence'],before[0]['evidence'])
            self.assertEqual(store.get_run(fixture.run),original_run)
            self.assertFalse(result['fit_decisions_changed'])
            self.assertFalse(result['google_updated'])
            self.assertNotEqual(pipeline.prepared_deals(fixture.week)[1]['sha256'],previous['sha256'])
            with self.assertRaisesRegex(ValueError,'changed during classification'):
                apply_sector_plan(plan)
        finally:fixture.tearDown()
