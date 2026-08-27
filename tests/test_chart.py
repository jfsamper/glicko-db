import unittest

from services.helpers import looks_like_player_name
from services.common import build_rating_chart_data


class ChartDataTests(unittest.TestCase):
    def test_first_snapshot_is_used_as_the_chart_baseline(self):
        snapshots = [
            {"snapshot_date": "2024-01-01", "rating": 2351.07},
            {"snapshot_date": "2024-02-01", "rating": 2351.07},
            {"snapshot_date": "2024-03-01", "rating": 2351.07},
        ]

        chart = build_rating_chart_data(snapshots)

        self.assertTrue(chart["path"])
        self.assertEqual(chart["baseline_rating"], 2351.1)
        self.assertGreaterEqual(chart["baseline_y"], 24)
        self.assertLessEqual(chart["baseline_y"], 196)

    def test_baseline_uses_the_first_snapshot_not_a_fixed_rating(self):
        chart = build_rating_chart_data([
            {"snapshot_date": "2024-01-01", "rating": 1700.0},
            {"snapshot_date": "2024-02-01", "rating": 1600.0},
        ])

        self.assertEqual(chart["baseline_rating"], 1700.0)
        self.assertEqual(chart["baseline_y"], chart["points"][0]["y"])

    def test_player_name_filter_rejects_time_and_metadata(self):
        self.assertFalse(looks_like_player_name("Hora/Ronda"))
        self.assertFalse(looks_like_player_name("21:00:00"))
        self.assertTrue(looks_like_player_name("Juan Felipe"))
        self.assertTrue(looks_like_player_name("Cortes, Alan"))


if __name__ == "__main__":
    unittest.main()
