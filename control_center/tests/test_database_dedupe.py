import copy
import unittest

from control_center.database import database_matches, dedupe_database


def record(did, day, round='Series A', amount='$40 million', source='Pro Rata', fit=None):
    return {
        'id': did, 'week_id': 'a' if day <= '2026-07-28' else 'b',
        'company': 'Act Security' if did != 'seed-b' else 'ActSecurity',
        'fields': {'deal_date': day, 'round': round, 'amount': amount,
                   'website': 'https://act.security/', 'announcement_link': 'https://act.security/news'},
        'sources': [source],
        'decision': {'run_id': did + '-review', 'final': {'fit': fit}} if fit else None,
    }


class DatabaseDedupeTests(unittest.TestCase):
    def test_cross_week_coverage_combines_but_seed_and_series_a_remain_separate(self):
        values = [record('a', '2026-07-28'), record('b', '2026-07-29', source='StrictlyVC'),
                  record('seed-a', '2026-07-28', 'Seed', '$20 million'),
                  record('seed-b', '2026-07-29', 'Seed', '$20 million', 'Term Sheet')]
        original = copy.deepcopy(values)
        result = dedupe_database(values)
        self.assertEqual(len(result), 2)
        self.assertEqual({value['fields']['round'] for value in result}, {'Seed', 'Series A'})
        self.assertTrue(all(len(value['coverage_records']) == 2 for value in result))
        series = next(value for value in result if value['fields']['round'] == 'Series A')
        self.assertEqual(series['sources'], ['Pro Rata', 'StrictlyVC'])
        self.assertEqual(values, original)

    def test_reviewed_representative_filters_after_combining_coverage(self):
        values = [record('reviewed', '2026-07-28', fit='No'), record('unreviewed', '2026-07-29', source='Term Sheet')]
        result = dedupe_database(values)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['id'], 'reviewed')
        self.assertEqual(result[0]['decision']['run_id'], 'reviewed-review')
        self.assertTrue(database_matches(result[0], 'Term Sheet', 'No'))
        self.assertFalse(database_matches(result[0], '', 'Unreviewed'))

    def test_conflicting_decisions_and_different_financings_are_not_hidden(self):
        cases = [
            [record('a', '2026-07-28', fit='Yes'), record('b', '2026-07-29', fit='No')],
            [record('a', '2026-07-28'), record('b', '2026-07-29', amount='$60 million')],
            [record('a', '2026-07-28'), record('b', '2026-08-20')],
        ]
        for values in cases:
            with self.subTest(values=values):
                self.assertEqual(len(dedupe_database(values)), 2)

    def test_matching_uses_original_currency_instead_of_weekly_usd_rates(self):
        values = [record('a', '2026-07-28', amount='$40 million'), record('b', '2026-07-29', amount='$45 million')]
        for value in values:
            value['amount_normalization'] = {'original_amount': 'EUR 36 million'}
        self.assertEqual(len(dedupe_database(values)), 1)


if __name__ == '__main__':
    unittest.main()
