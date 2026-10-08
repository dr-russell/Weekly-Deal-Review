import copy
import unittest

from control_center import app, pipeline, store
from control_center.dataset import dataset_summary
from control_center.logic import remaining_missing_fields
from control_center.missing_review import missing_summary, set_addressed
from control_center.tests import test_corrections


class MissingReviewTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_corrections.CorrectionTests()
        self.fixture.setUp()
        self.week = self.fixture.week
        record = store.get_deals(self.week)[0]
        record['fields'].update(amount='', geography='')
        pipeline.save_deal(self.week, record)

    def tearDown(self):
        self.fixture.tearDown()

    def test_address_persists_reduces_count_and_reopens_without_changing_values(self):
        before = store.get_deals(self.week)
        set_addressed('example', 'amount', True, [self.week])
        set_addressed('example', 'amount', True, [self.week])
        summary = dataset_summary([self.week])
        self.assertEqual(summary['remaining_missing_count'], 1)
        self.assertEqual(summary['addressed_missing_fields'][0]['field'], 'amount')
        self.assertTrue(summary['addressed_missing_fields'][0]['addressed'])
        current = missing_summary(store.get_deals(self.week))
        self.assertEqual(current['remaining_missing_count'], 1)
        self.assertEqual(len(current['addressed_missing_fields']), 1)
        self.assertEqual(store.get_deals(self.week), before)
        self.assertEqual(len(remaining_missing_fields(before)), 2)
        self.assertEqual(len(store.query('SELECT * FROM addressed_missing_fields')), 1)
        set_addressed('example', 'amount', False, [self.week])
        self.assertEqual(dataset_summary([self.week])['remaining_missing_count'], 2)

    def test_invalid_or_filled_field_cannot_be_acknowledged(self):
        for field in ('invalid', 'website'):
            with self.assertRaises(ValueError):
                set_addressed('example', field, True, [self.week])
        self.assertEqual(store.query('SELECT * FROM addressed_missing_fields'), [])

    def test_duplicate_coverage_is_addressed_together_but_other_round_is_separate(self):
        repeat = copy.deepcopy(store.get_deals(self.week)[0])
        repeat['id'] = 'repeat'
        pipeline.save_deal(self.week, repeat)
        merged = dataset_summary([self.week])['remaining_missing_fields'][0]['deal_id']
        set_addressed(merged, 'amount', True, [self.week])
        self.assertEqual(len(store.query('SELECT * FROM addressed_missing_fields')), 2)
        other = copy.deepcopy(repeat)
        other['id'] = 'other-round'
        other['fields']['round'] = 'Series B'
        pipeline.save_deal(self.week, other)
        summary = dataset_summary([self.week])
        self.assertEqual(len(summary['addressed_missing_fields']), 1)
        self.assertTrue(any(item['deal_id'] == 'other-round' and item['field'] == 'amount' for item in summary['remaining_missing_fields']))

    def test_overview_uses_same_progress_as_single_week_dataset(self):
        set_addressed('example', 'amount', True, [self.week])
        overview = next(week for week in app.overview()['weeks'] if week['id'] == self.week)
        self.assertEqual(overview['remaining_missing_count'], 1)
        self.assertEqual(len(overview['addressed_missing_fields']), 1)
