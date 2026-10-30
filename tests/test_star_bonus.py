"""Tests for Microsoft Rewards Bing Star Bonus parsing and behavioral scoring."""

import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import control_api
import points_log
import star_bonus
import status


class TestStarBonusParsing(unittest.TestCase):
	def test_parse_heavily_flagged_score(self):
		res = star_bonus.parse_star_score("5/3,500")
		self.assertEqual(res["earned"], 5)
		self.assertEqual(res["max"], 3500)
		self.assertAlmostEqual(res["percent"], 0.14, places=2)
		self.assertEqual(res["category"], "flagged_bot")
		self.assertEqual(res["rating"], "FLAGGED BY HEURISTIC")
		self.assertIn("Severe bot penalty", res["verdict"])

	def test_parse_perfect_human_score(self):
		res = star_bonus.parse_star_score("3,500/3,500")
		self.assertEqual(res["earned"], 3500)
		self.assertEqual(res["max"], 3500)
		self.assertEqual(res["percent"], 100.0)
		self.assertEqual(res["category"], "human_high")
		self.assertEqual(res["rating"], "HUMAN (PASSED)")
		self.assertIn("Full organic trust", res["verdict"])

	def test_parse_partial_trust_score(self):
		res = star_bonus.parse_star_score("1,500/3,500")
		self.assertEqual(res["earned"], 1500)
		self.assertEqual(res["max"], 3500)
		self.assertAlmostEqual(res["percent"], 42.86, places=2)
		self.assertEqual(res["category"], "partial_trust")
		self.assertEqual(res["rating"], "PARTIAL TRUST")

	def test_parse_pending(self):
		res = star_bonus.parse_star_score("Pending")
		self.assertIsNone(res["earned"])
		self.assertEqual(res["category"], "pending")
		self.assertEqual(res["rating"], "EVALUATING")
		self.assertIn("active evaluation", res["verdict"])

	def test_parse_not_eligible(self):
		res = star_bonus.parse_star_score("Not eligible")
		self.assertIsNone(res["earned"])
		self.assertEqual(res["category"], "not_eligible")
		self.assertEqual(res["rating"], "NEW_ACCOUNT")

	def test_parse_none_or_empty(self):
		res = star_bonus.parse_star_score(None)
		self.assertEqual(res["rating"], "UNKNOWN")
		res_empty = star_bonus.parse_star_score("")
		self.assertEqual(res_empty["rating"], "UNKNOWN")


class TestStarBonusReport(unittest.TestCase):
	def setUp(self):
		self.directory = tempfile.TemporaryDirectory()
		self.addCleanup(self.directory.cleanup)

		patcher = mock.patch.object(points_log, "LOG_FILE", os.path.join(self.directory.name, "points.jsonl"))
		patcher.start()
		self.addCleanup(patcher.stop)

		env_patcher = mock.patch.dict(os.environ, {"REWARDS_ACCOUNTS": "default,second"})
		env_patcher.start()
		self.addCleanup(env_patcher.stop)

	def test_report_generation_with_history(self):
		points_log.record("default", {
			"today": 200, "month": 5000, "lifetime": 7000,
			"bing_star_last_month": "5/3,500", "level_up_last_month": "300/700", "default_search_last_month": "Pending"
		})
		points_log.record("default", {
			"today": 220, "month": 5220, "lifetime": 7220,
			"bing_star_last_month": "Pending", "level_up_last_month": "300/700", "default_search_last_month": "Pending"
		})
		points_log.record("second", {
			"today": 100, "month": 1000, "lifetime": 1000,
			"bing_star_last_month": "Not eligible", "level_up_last_month": "Not eligible", "default_search_last_month": "Not eligible"
		})

		report = star_bonus.format_star_bonus_report()
		self.assertIn("MICROSOFT REWARDS: BING STAR BONUS", report)
		self.assertIn("Next Monthly Payout", report)
		self.assertIn("[DEFAULT]", report)
		self.assertIn("EVALUATING", report)
		self.assertIn("[SECOND]", report)
		self.assertIn("NEW_ACCOUNT", report)

	def test_control_api_bonus_route(self):
		points_log.record("default", {
			"today": 200, "month": 5000, "lifetime": 7000,
			"bing_star_last_month": "Pending", "level_up_last_month": "300/700", "default_search_last_month": "Pending"
		})
		status_code, data = control_api.handle("GET", "/star-bonus")
		self.assertEqual(status_code, 200)
		self.assertIn("accounts", data)
		self.assertIn("default", data["accounts"])
		self.assertEqual(data["accounts"]["default"]["star_bonus"]["category"], "pending")


if __name__ == "__main__":
	unittest.main()
