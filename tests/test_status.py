"""The one-command status: it reads what is there and never changes anything."""

import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import journal
import points_log
import safety
import status


class StatusTestCase(unittest.TestCase):
	def setUp(self):
		directory = tempfile.TemporaryDirectory()
		self.addCleanup(directory.cleanup)

		for patcher in (
			mock.patch.object(points_log, "LOG_FILE", os.path.join(directory.name, "points.jsonl")),
			mock.patch.object(journal, "JOURNAL_FILE", os.path.join(directory.name, "journal.jsonl")),
			mock.patch.dict(os.environ, {"REWARDS_ACCOUNTS": "default,second", "REWARDS_LEVEL_TARGETS": "default=750,second=500"}),
			mock.patch.object(safety, "blocked", return_value=None),
		):
			patcher.start()
			self.addCleanup(patcher.stop)


class TestStatus(StatusTestCase):
	def test_with_nothing_recorded_it_says_so_for_each_account(self):
		text = status.report()

		self.assertIn("no points reading yet", text)
		self.assertIn("no search report yet today", text)
		self.assertIn("monthly bonuses: not read yet", text)
		self.assertIn("brake: running normally", text)

	def test_it_shows_points_bonuses_and_the_days_quota_per_account(self):
		points_log.record("default", {"today": 260, "month": 1430, "lifetime": 2573, "level_up_last_month": "300/700", "bing_star_last_month": "5/3,500"})
		points_log.record("second", {"today": 90, "month": 305, "lifetime": 305})
		journal.record("default", "search", "quota", account="default", points=40, cap=100, target=91, complete=False)

		text = status.report()
		first, second = text.split("\n\n")[:2]

		self.assertIn("default", first.splitlines()[0])
		self.assertIn("month 1430", first)
		self.assertIn("level up 300/700", first)
		self.assertIn("searches 40/100, aiming for 91: more to do", first)
		self.assertIn("month 305", second)
		self.assertIn("195 to the next level", second)

	def test_every_account_with_data_is_shown_not_just_the_ones_this_container_is_set_up_for(self):
		points_log.record("second", {"today": 90, "month": 305, "lifetime": 305})

		with mock.patch.dict(os.environ, {"REWARDS_ACCOUNTS": "default"}):
			text = status.report()

		self.assertIn("second", text)
		self.assertIn("month 305", text)

	def test_a_configured_account_comes_first_and_none_is_listed_twice(self):
		points_log.record("default", {"today": 1, "month": 2, "lifetime": 3})
		points_log.record("second", {"today": 1, "month": 2, "lifetime": 3})

		with mock.patch.dict(os.environ, {"REWARDS_ACCOUNTS": "second"}):
			self.assertEqual(status.known_names(), ["second", "default"])

	def test_a_paused_account_is_shown_loudly(self):
		with mock.patch.object(safety, "blocked", return_value={"kind": "captcha", "reason": "a human check"}):
			self.assertIn("PAUSED (captcha: a human check)", status.report())

	def test_an_unusable_account_list_is_reported_not_raised(self):
		with mock.patch.dict(os.environ, {"REWARDS_ACCOUNTS": "../bad"}):
			self.assertIn("cannot read the accounts", status.report())

	def test_it_writes_nothing(self):
		status.report()

		self.assertFalse(os.path.exists(points_log.LOG_FILE))
		self.assertFalse(os.path.exists(journal.JOURNAL_FILE))


if __name__ == "__main__":
	unittest.main()
