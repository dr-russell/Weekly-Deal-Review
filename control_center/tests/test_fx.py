import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from control_center import store
from control_center.fx import freeze_rates, prepare_rates, prepared_rates


class ExchangeRateTests(unittest.TestCase):
    def test_rate_snapshot_is_immutable_and_hash_checked(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch("control_center.fx.DATA", root), patch.object(store, "DATA", root), patch.object(store, "DB", root / "test.sqlite"), patch.object(store, "ARTIFACTS", root / "artifacts"):
                store.initialize()
                old = freeze_rates("test-week", {"observations": [{"d": "2026-09-18", "FXUSDCAD": {"v": "1.4"}}]})
                original = (root / old["path"]).read_bytes()
                new = freeze_rates("test-week", {"observations": [{"d": "2026-09-18", "FXUSDCAD": {"v": "1.5"}}]})
                self.assertNotEqual(old["sha256"], new["sha256"])
                self.assertEqual((root / old["path"]).read_bytes(), original)
                book, manifest = prepared_rates("test-week")
                self.assertEqual(manifest, new)
                self.assertEqual(book["observations"][0]["FXUSDCAD"]["v"], "1.5")
                (root / new["path"]).write_text("{}")
                with self.assertRaisesRegex(ValueError, "changed"):
                    prepared_rates("test-week")

    def test_rate_snapshot_path_cannot_escape_workflow_data(self):
        with patch.object(store, "query", return_value=[{"value": '{"path":"../../../outside.json","sha256":"invalid"}'}]):
            with self.assertRaisesRegex(ValueError, "stay in local"):
                prepared_rates("test-week")

    def test_provider_request_covers_four_stats_weeks_and_prior_business_day(self):
        response = Mock(url="https://www.bankofcanada.ca/valet/observations/group/FX_RATES_DAILY/json?start_date=2026-08-19&end_date=2026-09-22")
        response.json.return_value = {"observations": [{"d": "2026-09-18", "FXUSDCAD": {"v": "1.4"}}], "seriesDetail": {}}
        with patch.object(store, "query", return_value=[{"start": "2026-09-16", "end": "2026-09-22"}]), patch("control_center.fx.requests.get", return_value=response) as get, patch("control_center.fx.freeze_rates", return_value={"sha256": "frozen"}) as freeze, patch.object(store, "event") as event:
            prepare_rates("test-run", "test-week")
            self.assertEqual(get.call_args.kwargs["params"], {"start_date": "2026-08-19", "end_date": "2026-09-22"})
            self.assertEqual(freeze.call_args.args[1]["provider"], "Bank of Canada")
            self.assertTrue(event.call_args.args[3]["outside_demo_timer"])

    def test_empty_or_future_provider_observations_stop_preparation(self):
        for observations in [[], [{"d": "2026-09-23"}]]:
            response = Mock()
            response.json.return_value = {"observations": observations}
            with patch.object(store, "query", return_value=[{"start": "2026-09-16", "end": "2026-09-22"}]), patch("control_center.fx.requests.get", return_value=response), patch("control_center.fx.freeze_rates") as freeze:
                with self.assertRaisesRegex(ValueError, "missing or out-of-window"):
                    prepare_rates("test-run", "test-week")
                freeze.assert_not_called()
