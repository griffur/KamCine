"""Timezone boundaries without devices or network."""
import datetime
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import planning
import attente


class TimezoneTests(unittest.TestCase):
    def zone(self, name):
        for key, value in (("FUSEAU", ZoneInfo(name)), ("TIMEZONE_NAME", name)):
            p = patch.object(planning, key, value)
            p.start()
            self.addCleanup(p.stop)

    def test_roundtrip_offsets(self):
        stamp = datetime.datetime(2026, 1, 15, 12, tzinfo=datetime.timezone.utc).timestamp()
        for name, expected in (("UTC", "12:00"), ("Europe/Zurich", "13:00"),
                               ("America/New_York", "07:00"), ("Asia/Kathmandu", "17:45"),
                               ("Australia/Adelaide", "22:30")):
            with self.subTest(zone=name), patch.object(planning, "FUSEAU", ZoneInfo(name)):
                local = planning.local_iso(stamp)
                self.assertEqual(local, "2026-01-15T" + expected)
                self.assertEqual(planning.epoch_depuis_local(local), stamp)

    def test_swiss_dst_and_gap(self):
        self.zone("Europe/Zurich")
        with self.assertRaises(ValueError):
            planning.epoch_depuis_local("2026-03-29T02:30")
        stamp = planning.epoch_depuis_local("2026-10-25T02:30")
        self.assertEqual(datetime.datetime.fromtimestamp(stamp, datetime.timezone.utc).hour, 1)
        self.assertEqual(planning.local_iso(stamp), "2026-10-25T02:30")

    def test_southern_hemisphere(self):
        self.zone("Australia/Sydney")
        with self.assertRaises(ValueError):
            planning.epoch_depuis_local("2026-10-04T02:30")
        winter = planning.epoch_depuis_local("2026-07-01T12:00")
        summer = planning.epoch_depuis_local("2026-01-01T12:00")
        self.assertEqual(planning.decalage_s(winter), 36000)
        self.assertEqual(planning.decalage_s(summer), 39600)

    def test_waiting_window_and_date_rollover(self):
        self.zone("Asia/Kathmandu")
        t = planning.epoch_depuis_local("2026-09-29T23:30")
        opening = attente.prochaine_ouverture(t, 18 * 60, 22 * 60)
        self.assertEqual(planning.local_iso(opening), "2026-09-30T18:00")
        self.assertTrue(attente.dans_fenetre(t, 22 * 60, 2 * 60))

    def test_waiting_window_in_dst_gap(self):
        self.zone("Europe/Zurich")
        t = planning.epoch_depuis_local("2026-03-29T01:00")
        opening = attente.prochaine_ouverture(t, 150, 240)
        self.assertEqual(planning.local_iso(opening), "2026-03-29T03:00")
        opening = attente.prochaine_ouverture(t, 150, 165)
        self.assertEqual(planning.local_iso(opening), "2026-03-30T02:30")

    def test_environment_priority_and_invalid_value(self):
        env = {k: v for k, v in os.environ.items() if k not in ("KAMCINE_TIMEZONE", "TZ")}
        for settings, expected in (({}, "Europe/Zurich"), ({"TZ": "UTC"}, "UTC"),
                                    ({"TZ": "UTC", "KAMCINE_TIMEZONE": "Asia/Kathmandu"}, "Asia/Kathmandu")):
            result = subprocess.run([sys.executable, "-c", "import planning; print(planning.TIMEZONE_NAME)"],
                                    cwd=ROOT, env=dict(env, **settings), capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), expected)
        result = subprocess.run([sys.executable, "-c", "import planning"], cwd=ROOT,
                                env=dict(env, KAMCINE_TIMEZONE="Invalid/Zone"), capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Fuseau IANA invalide", result.stderr)


if __name__ == "__main__":
    unittest.main()
