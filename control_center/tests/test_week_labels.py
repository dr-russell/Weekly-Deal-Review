import unittest

from control_center.google import ic_week_label, period_text, stats_for


class WeekLabelTests(unittest.TestCase):
    def test_stats_and_roundup_share_short_selected_week_labels(self):
        periods = [{"start": "2026-08-26", "end": "2026-09-01", "count": 2, "capital_usd_m": 3},
                   {"start": "2026-09-02", "end": "2026-09-08", "count": 4, "capital_usd_m": 5}]
        package = {"selection_weeks": periods, "week_stats": periods}
        self.assertEqual(stats_for(package, [])[0], [["8/26 - 9/1", 2, 3], ["9/2 - 9/8", 4, 5]])
        self.assertEqual(period_text(package), "Selected IC weeks: 8/26 - 9/1; 9/2 - 9/8\n")
        self.assertEqual(ic_week_label("2026-12-30", "2027-01-05"), "12/30 - 1/5")


if __name__ == "__main__":
    unittest.main()
