import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from control_center.config import GMAIL_TOKEN, GOOGLE_CLIENT, GOOGLE_TOKEN, MANDATE, NEWSLETTER_CONFIG, PROJECT, ROOT, load_google_settings


class LocalConfigTests(unittest.TestCase):
    def test_missing_settings_have_no_implicit_account_or_targets(self):
        with tempfile.TemporaryDirectory() as folder:
            settings = load_google_settings(Path(folder) / 'missing.json')
        self.assertEqual(settings['account'], '')
        self.assertEqual(settings['targets']['test'], {'sheet':'', 'doc':''})

    def test_google_settings_preserve_explicit_targets_and_normalize_account(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'settings.json'
            path.write_text(json.dumps({'account':' OWNER@sample.org ', 'targets':{
                'test':{'sheet':'demo-sheet','doc':'demo-doc'},
                'production':{'sheet':'production-sheet','doc':'production-doc'}}}))
            settings = load_google_settings(path)
        self.assertEqual(settings['account'], 'owner@sample.org')
        self.assertEqual(settings['targets']['test']['sheet'], 'demo-sheet')

    def test_example_settings_cannot_be_used_for_google_writes(self):
        with self.assertRaises(ValueError):
            load_google_settings(PROJECT / 'control_center/google.local.example.json')

    def test_missing_google_settings_stop_before_accessing_credentials(self):
        from control_center import google
        with patch.object(google, 'GOOGLE_ACCOUNT', ''), patch.object(google, 'Credentials') as credentials:
            with self.assertRaisesRegex(RuntimeError, 'settings missing'):
                google.credentials()
            credentials.from_authorized_user_file.assert_not_called()

    def test_runtime_dependencies_are_owned_by_control_center(self):
        for path in [MANDATE, NEWSLETTER_CONFIG, GMAIL_TOKEN, GOOGLE_CLIENT, GOOGLE_TOKEN]:
            self.assertTrue(path.is_relative_to(ROOT))
        self.assertEqual(set(json.loads(NEWSLETTER_CONFIG.read_text())['newsletter_sources']),
                         {'strictlyvc', 'term_sheet', 'pro_rata'})
