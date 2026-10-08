import unittest

from control_center.logic import canonical_company_name, specific_round_amount


class RecordNormalizationTests(unittest.TestCase):
    def test_combined_rounds_use_the_explicit_amount_for_each_round(self):
        amount = '$30 million combined ($7 million pre-seed; $23 million seed)'
        self.assertEqual(specific_round_amount(amount, 'Pre-seed'), '$7 million')
        self.assertEqual(specific_round_amount(amount, 'Seed'), '$23 million')
        self.assertEqual(specific_round_amount(amount, 'Series A'), amount)

    def test_ambiguous_and_qualified_totals_are_preserved(self):
        for amount in ['$30 million combined funding', '$30 million combined ($7 million pre-seed; $23 million conditional seed)', '$30 million combined ($7 million seed; $23 million seed)', '$23 million seed']:
            with self.subTest(amount=amount):
                self.assertEqual(specific_round_amount(amount, 'Seed'), amount)

    def test_company_spacing_requires_the_verified_identity(self):
        self.assertEqual(canonical_company_name('ActSecurity', 'https://act.security/'), 'Act Security')
        self.assertEqual(canonical_company_name('ActSecurity', 'https://other.example/'), 'ActSecurity')
        self.assertEqual(canonical_company_name('Abstract Security', 'https://abstract.security/'), 'Abstract Security')


if __name__ == '__main__':
    unittest.main()
