import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import weekly

SUNDAY_EVENING = datetime(2026, 10, 11, 19, 0)  # a Sunday


def reading(days_ago, lifetime, month=0, now=SUNDAY_EVENING):
	return {"time": (now - timedelta(days=days_ago)).strftime("%Y-%m-%d %H:%M:%S"), "lifetime": lifetime, "month": month}


class WeeklyCase(unittest.TestCase):
	def setUp(self):
		self.folder = tempfile.TemporaryDirectory()
		self.addCleanup(self.folder.cleanup)

		for patcher in (
			mock.patch.object(weekly, "STATE_FILE", os.path.join(self.folder.name, "weekly.json")),
			mock.patch.object(weekly.safety, "paused_for", return_value=None),
			mock.patch.object(weekly.journal, "events", return_value=[]),
			mock.patch.object(weekly.health, "findings", return_value=[]),
			mock.patch.object(weekly.points_log, "target_for", return_value=None),
		):
			patcher.start()
			self.addCleanup(patcher.stop)

		self.rows = {"default": [reading(8, 1000), reading(1, 1600, 400)]}
		patcher = mock.patch.object(weekly.points_log, "history", side_effect=lambda name=None: self.rows.get(name, []))
		patcher.start()
		self.addCleanup(patcher.stop)
		self.send = mock.Mock()


class TestGained(WeeklyCase):
	def test_a_weeks_gain_is_measured_from_the_reading_before_the_week(self):
		self.assertEqual(weekly.gained_in_week(self.rows["default"], SUNDAY_EVENING), 600)

	def test_one_reading_is_nothing_to_compare(self):
		self.assertIsNone(weekly.gained_in_week([reading(1, 500)], SUNDAY_EVENING))


class TestWeeklyNote(WeeklyCase):
	def test_it_is_sent_once_on_sunday_evening(self):
		weekly.run_if_due(["default"], SUNDAY_EVENING, self.send)
		weekly.run_if_due(["default"], SUNDAY_EVENING + timedelta(hours=2), self.send)

		self.assertEqual(self.send.call_count, 1)
		self.assertIn("+600 this week", self.send.call_args.args[1])
		self.assertIn("Nothing needs anyone", self.send.call_args.args[1])

	def test_it_is_not_sent_on_other_days_or_before_the_evening(self):
		weekly.run_if_due(["default"], SUNDAY_EVENING - timedelta(days=1), self.send)
		weekly.run_if_due(["default"], SUNDAY_EVENING.replace(hour=9), self.send)

		self.send.assert_not_called()

	def test_it_comes_again_the_next_week(self):
		weekly.run_if_due(["default"], SUNDAY_EVENING, self.send)
		weekly.run_if_due(["default"], SUNDAY_EVENING + timedelta(days=7), self.send)

		self.assertEqual(self.send.call_count, 2)

	def test_a_pause_and_a_problem_are_in_it(self):
		with mock.patch.object(weekly.safety, "paused_for", return_value={"kind": "x"}), \
			mock.patch.object(weekly.health, "findings", return_value=[mock.Mock(title="[NEEDS CLAUDE] default: broken")]):
			weekly.run_if_due(["default"], SUNDAY_EVENING, self.send)

		text = self.send.call_args.args[1]

		self.assertIn("PAUSED", text)
		self.assertIn("Needs attention", text)

	def test_an_account_with_no_points_yet_is_still_listed(self):
		weekly.run_if_due(["default", "third"], SUNDAY_EVENING, self.send)

		self.assertIn("third: no points on record yet", self.send.call_args.args[1])


class TestRedemptionReminder(WeeklyCase):
	def test_it_is_sent_once_when_an_account_passes_6500(self):
		self.rows["default"] = [reading(2, 6000), reading(0, 6600)]
		weekly.run_if_due(["default"], SUNDAY_EVENING - timedelta(days=2), self.send)
		weekly.run_if_due(["default"], SUNDAY_EVENING - timedelta(days=1), self.send)

		self.assertEqual(self.send.call_count, 1)
		self.assertEqual(self.send.call_args.kwargs["account"], "default")
		self.assertIn("mobile data", self.send.call_args.args[1])

	def test_it_comes_again_at_the_next_6500(self):
		self.rows["default"] = [reading(2, 6000), reading(0, 6600)]
		weekly.run_if_due(["default"], SUNDAY_EVENING - timedelta(days=2), self.send)
		self.rows["default"] = [reading(0, 13100)]
		weekly.run_if_due(["default"], SUNDAY_EVENING - timedelta(days=1), self.send)

		self.assertEqual(self.send.call_count, 2)

	def test_below_6500_nothing_is_sent(self):
		weekly.run_if_due(["default"], SUNDAY_EVENING - timedelta(days=2), self.send)

		self.send.assert_not_called()


class TestWhichAccounts(WeeklyCase):
	def test_an_account_with_no_points_yet_is_included_from_the_pacing_record(self):
		with mock.patch.object(weekly.pacing, "known_accounts", return_value=["default", "third"]), 			mock.patch("status.known_names", return_value=["default"]):
			weekly.run_if_due(None, SUNDAY_EVENING, self.send)

		self.assertIn("third: no points on record yet", self.send.call_args.args[1])


class TestNeverRaises(WeeklyCase):
	def test_a_failing_sender_does_not_raise(self):
		self.send.side_effect = RuntimeError("discord down")

		self.assertFalse(weekly.run_if_due(["default"], SUNDAY_EVENING, self.send))


if __name__ == "__main__":
	unittest.main()
