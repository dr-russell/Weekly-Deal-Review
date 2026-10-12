import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from google.auth.exceptions import RefreshError

from control_center import auth, google, pipeline
from control_center import auth_guard as guard


class AuthenticationGuardTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.gmail = Path(self.folder.name) / "gmail.pickle"
        self.writer = Path(self.folder.name) / "write.json"
        # Any accidental unmocked HTTP call must fail locally.
        self.network = patch("requests.sessions.Session.request", side_effect=AssertionError("Network forbidden"))
        self.network.start()
        self.addCleanup(self.network.stop)

    def test_refresh_rejection_blocks_future_refreshes_and_requests(self):
        creds = MagicMock()
        creds.refresh.side_effect = RefreshError("invalid_grant", {"secret": "must not persist"})
        with self.assertRaises(guard.ReconnectRequired):
            guard.refresh_credentials(creds, MagicMock(), self.gmail, "gmail")
        with self.assertRaises(guard.ReconnectRequired):
            guard.refresh_credentials(creds, MagicMock(), self.gmail, "gmail")
        self.assertEqual(creds.refresh.call_count, 1)
        self.assertNotIn("secret", guard.marker_path(self.gmail).read_text())
        session = guard.AuthorizedSession(creds, token_path=self.gmail, connection="gmail")
        self.addCleanup(session.close)
        with patch.object(guard.GoogleSession, "request") as request:
            with self.assertRaises(guard.ReconnectRequired):
                session.get("https://gmail.googleapis.com/fake")
            request.assert_not_called()

    def test_401_blocks_queued_requests_and_session_recreation(self):
        response = MagicMock(status_code=401)
        with patch.object(guard.GoogleSession, "request", return_value=response) as request:
            for _ in range(2):
                with guard.AuthorizedSession(MagicMock(), token_path=self.writer, connection="google_write") as session:
                    self.assertEqual(session._max_refresh_attempts, 0)
                    with self.assertRaises(guard.ReconnectRequired):
                        session.get("https://sheets.googleapis.com/fake")
            self.assertEqual(request.call_count, 1)

    def test_block_survives_token_replacement_and_is_connection_specific(self):
        guard.block_connection(self.gmail, "gmail", "token_refresh_rejected")
        self.gmail.write_text("replacement token")
        with self.assertRaises(guard.ReconnectRequired):
            guard.check_connection(self.gmail, "gmail")
        guard.check_connection(self.writer, "google_write")
        self.assertEqual(guard.marker_path(self.gmail).stat().st_mode & 0o777, 0o600)

    def test_corrupt_marker_fails_closed(self):
        guard.marker_path(self.gmail).write_text("not JSON")
        with self.assertRaises(guard.ReconnectRequired):
            guard.check_connection(self.gmail, "gmail")

    def test_service_quota_and_file_permissions_do_not_block_authentication(self):
        for status, error in [(503, {}), (429, {}), (403, {"errors": [{"reason": "rateLimitExceeded"}]}),
                              (403, {"status": "PERMISSION_DENIED"})]:
            response = MagicMock(status_code=status)
            response.json.return_value = {"error": error}
            with patch.object(guard.GoogleSession, "request", return_value=response):
                with guard.AuthorizedSession(MagicMock(), token_path=self.gmail, connection="gmail") as session:
                    self.assertIs(session.get("https://gmail.googleapis.com/fake"), response)
            guard.check_connection(self.gmail, "gmail")
        creds = MagicMock()
        creds.refresh.side_effect = RefreshError("temporary failure", retryable=True)
        with self.assertRaises(RefreshError):
            guard.refresh_credentials(creds, MagicMock(), self.gmail, "gmail")
        guard.check_connection(self.gmail, "gmail")

    def test_invalid_scope_response_blocks(self):
        response = MagicMock(status_code=403)
        response.json.return_value = {"error": {"details": [{"reason": "ACCESS_TOKEN_SCOPE_INSUFFICIENT"}]}}
        with patch.object(guard.GoogleSession, "request", return_value=response):
            with guard.AuthorizedSession(MagicMock(), token_path=self.writer, connection="google_write") as session:
                with self.assertRaises(guard.ReconnectRequired):
                    session.get("https://docs.googleapis.com/fake")

    def test_blocked_pipeline_and_publisher_do_not_load_or_refresh_credentials(self):
        guard.block_connection(self.gmail, "gmail", "token_refresh_rejected")
        guard.block_connection(self.writer, "google_write", "token_refresh_rejected")
        with patch.object(pipeline, "GMAIL_TOKEN", self.gmail), patch.object(pipeline.pickle, "load") as load:
            with self.assertRaises(guard.ReconnectRequired):
                pipeline.fetch_gmail("week", "2026-10-01", "2026-10-07")
            load.assert_not_called()
        with patch.object(google, "GOOGLE_TOKEN", self.writer), patch.object(google.Credentials, "from_authorized_user_file") as load:
            with self.assertRaises(guard.ReconnectRequired):
                google.credentials()
            load.assert_not_called()

    def test_explicit_reconnect_bypasses_block_but_does_not_clear_it_on_profile_read(self):
        guard.block_connection(self.gmail, "gmail", "token_refresh_rejected")
        response = MagicMock(status_code=200)
        with patch.object(guard.GoogleSession, "request", return_value=response):
            with guard.AuthorizedSession(MagicMock(), token_path=self.gmail, connection="gmail", reconnecting=True) as session:
                self.assertIs(session.get("https://gmail.googleapis.com/fake"), response)
        with self.assertRaises(guard.ReconnectRequired):
            guard.check_connection(self.gmail, "gmail")

    def test_successful_explicit_gmail_reconnection_clears_only_gmail(self):
        guard.block_connection(self.gmail, "gmail", "token_refresh_rejected")
        guard.block_connection(self.writer, "google_write", "token_refresh_rejected")
        from types import SimpleNamespace
        creds = SimpleNamespace(account="reviewer@example.org")
        with patch.object(auth, "GMAIL_TOKEN", self.gmail), patch.object(auth, "authorize", return_value=creds), \
                patch.object(auth, "verified_account", return_value=creds) as verify, patch("sys.argv", ["auth", "--gmail"]):
            auth.main()
        verify.assert_called_once_with(creds, gmail=True, reconnecting=True)
        guard.check_connection(self.gmail, "gmail")
        with self.assertRaises(guard.ReconnectRequired):
            guard.check_connection(self.writer, "google_write")

    def test_failed_reconnect_does_not_clear_block_or_replace_token(self):
        guard.block_connection(self.gmail, "gmail", "token_refresh_rejected")
        self.gmail.write_text("existing")
        with patch.object(auth, "GMAIL_TOKEN", self.gmail), patch.object(auth, "authorize"), \
                patch.object(auth, "verified_account", side_effect=RuntimeError("Wrong account")), \
                patch("sys.argv", ["auth", "--gmail"]):
            with self.assertRaisesRegex(RuntimeError, "Wrong account"):
                auth.main()
        self.assertEqual(self.gmail.read_text(), "existing")
        with self.assertRaises(guard.ReconnectRequired):
            guard.check_connection(self.gmail, "gmail")

    def test_successful_explicit_writer_reconnection_clears_only_writer(self):
        guard.block_connection(self.gmail, "gmail", "token_refresh_rejected")
        guard.block_connection(self.writer, "google_write", "token_refresh_rejected")
        creds = MagicMock(account="reviewer@example.org")
        creds.to_json.return_value = json.dumps({"account": creds.account})
        with patch.object(auth, "GOOGLE_TOKEN", self.writer), patch.object(auth, "authorize", return_value=creds), \
                patch.object(auth, "verified_account", return_value=creds), patch("sys.argv", ["auth", "--reauthorize"]):
            auth.main()
        guard.check_connection(self.writer, "google_write")
        with self.assertRaises(guard.ReconnectRequired):
            guard.check_connection(self.gmail, "gmail")


if __name__ == "__main__":
    unittest.main()
