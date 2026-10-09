"""Rest days, partial quotas, a ramp for new accounts, account order and query overlap."""

import json
import os
import sys
import tempfile
import time
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pacing
import query_history

TODAY = date(2026, 10, 5)


class PacingTestCase(unittest.TestCase):
	"""Pacing as a real account sees it: the conftest's neutral settings are replaced."""

	def setUp(self):
		directory = tempfile.TemporaryDirectory()
		self.addCleanup(directory.cleanup)
		self.dir = Path(directory.name)

		for patcher in (
			mock.patch.object(pacing, "STATE_FILE", str(self.dir / "pacing.json")),
			mock.patch.object(pacing, "_today", return_value=TODAY),
			mock.patch.object(pacing.points_log, "LOG_FILE", str(self.dir / "points.jsonl")),
			mock.patch.dict(os.environ, {
				"REWARDS_REST_DAY_CHANCE": "0.15", "REWARDS_MIN_DAILY_FRACTION": "0.6",
				"REWARDS_RAMP_DAYS": "7", "REWARDS_KEEP_ORDER": "0",
			}),
		):
			patcher.start()
			self.addCleanup(patcher.stop)

	def seasoned(self, account, days=60):
		"""Say an account has been worked for `days` days already."""
		pacing.STATE_FILE and Path(pacing.STATE_FILE).write_text('{"%s": {"first_day": "%s"}}' % (account, (TODAY - timedelta(days=days)).isoformat()))


class TestTheRamp(PacingTestCase):
	def test_a_new_account_starts_today_and_the_day_is_remembered(self):
		self.assertEqual(pacing.first_day("fresh"), TODAY)
		self.assertIn("fresh", Path(pacing.STATE_FILE).read_text())

		with mock.patch.object(pacing, "_today", return_value=TODAY + timedelta(days=3)):
			self.assertEqual(pacing.first_day("fresh"), TODAY)
			self.assertEqual(pacing.age_days("fresh"), 3)

	def test_an_account_that_already_has_points_readings_starts_from_the_first_one(self):
		pacing.points_log.record("older", {"today": 1, "month": 1, "lifetime": 1})
		rows = pacing.points_log.history("older")
		rows[0]["time"] = (TODAY - timedelta(days=4)).isoformat() + " 10:00:00"
		Path(pacing.points_log.LOG_FILE).write_text(__import__("json").dumps(rows[0]) + "\n")

		self.assertEqual(pacing.age_days("older"), 4)

	def test_the_first_days_are_a_ramp_and_it_ends(self):
		self.assertTrue(pacing.in_ramp("fresh"))

		for day in range(1, 7):
			with mock.patch.object(pacing, "_today", return_value=TODAY + timedelta(days=day)):
				self.assertTrue(pacing.in_ramp("fresh"), day)

		with mock.patch.object(pacing, "_today", return_value=TODAY + timedelta(days=7)):
			self.assertFalse(pacing.in_ramp("fresh"))

	def test_during_the_ramp_only_the_daily_set_and_searches_are_done(self):
		self.assertEqual(pacing.steps_allowed("fresh"), pacing.RAMP_STEPS)
		self.assertIn("Required searches", pacing.RAMP_STEPS)
		self.assertIn("Quests", pacing.RAMP_STEPS)  # the new-member quest, see pacing.py

		self.seasoned("old")
		self.assertIsNone(pacing.steps_allowed("old"))

	def test_the_ramp_asks_for_less_and_less_each_day_less(self):
		shares = []

		for day in range(7):
			with mock.patch.object(pacing, "_today", return_value=TODAY + timedelta(days=day)), mock.patch.dict(os.environ, {"REWARDS_MIN_DAILY_FRACTION": "1"}):
				shares.append(pacing.fraction("fresh"))

		self.assertEqual(shares, sorted(shares))
		self.assertLess(shares[0], 0.35)
		self.assertGreater(shares[-1], 0.85)

	def test_there_are_no_rest_days_in_the_ramp(self):
		with mock.patch.dict(os.environ, {"REWARDS_REST_DAY_CHANCE": "0.5"}):
			for day in range(7):
				with mock.patch.object(pacing, "_today", return_value=TODAY + timedelta(days=day)):
					self.assertFalse(pacing.is_rest_day("fresh"))

	def test_the_ramp_can_be_turned_off(self):
		with mock.patch.dict(os.environ, {"REWARDS_RAMP_DAYS": "0"}):
			self.assertFalse(pacing.in_ramp("fresh"))
			self.assertIsNone(pacing.steps_allowed("fresh"))


class TestPerAccountRamp(PacingTestCase):
	def set_state(self, text):
		Path(pacing.STATE_FILE).write_text(text)

	def test_an_account_can_have_a_longer_ramp_than_the_rest(self):
		started = (TODAY - timedelta(days=10)).isoformat()
		self.set_state('{"long": {"first_day": "%s", "ramp_days": 21}, "plain": {"first_day": "%s"}}' % (started, started))

		self.assertTrue(pacing.in_ramp("long"))
		self.assertFalse(pacing.in_ramp("plain"))
		self.assertEqual(pacing.ramp_days("long"), 21)
		self.assertEqual(pacing.ramp_days("plain"), 7)
		self.assertIn("ramp day 11 of 21", pacing.describe("long"))

	def test_a_longer_ramp_set_before_the_first_day_is_kept_when_the_day_is_saved(self):
		self.set_state('{"mom": {"ramp_days": 21}}')

		self.assertEqual(pacing.age_days("mom"), 0)
		self.assertEqual(pacing.ramp_days("mom"), 21)
		self.assertEqual(json.loads(Path(pacing.STATE_FILE).read_text())["mom"]["first_day"], TODAY.isoformat())

	def test_a_bad_value_falls_back_to_the_global_ramp(self):
		self.set_state('{"x": {"first_day": "2026-01-01", "ramp_days": "soon"}}')

		self.assertEqual(pacing.ramp_days("x"), 7)

	def test_the_longer_ramp_climbs_more_slowly(self):
		self.set_state('{"long": {"first_day": "%s", "ramp_days": 21}}' % (TODAY - timedelta(days=3)).isoformat())

		with mock.patch.dict(os.environ, {"REWARDS_MIN_DAILY_FRACTION": "1"}):
			self.assertLess(pacing.fraction("long"), 0.5)


class TestRestDays(PacingTestCase):
	def days(self, account, count=400):
		return [pacing.is_rest_day(account, TODAY + timedelta(days=n)) for n in range(count)]

	def test_the_same_account_and_day_always_give_the_same_answer(self):
		self.seasoned("old")

		self.assertEqual(self.days("old", 60), self.days("old", 60))

	def test_about_one_day_in_seven_is_a_rest_day(self):
		self.seasoned("old")
		share = sum(self.days("old")) / 400

		self.assertGreater(share, 0.06)
		self.assertLess(share, 0.18)

	def test_never_two_rest_days_in_a_row(self):
		self.seasoned("old")
		days = self.days("old")

		self.assertFalse(any(a and b for a, b in zip(days, days[1:])))

	def test_accounts_rest_on_different_days(self):
		self.seasoned("old")
		Path(pacing.STATE_FILE).write_text('{"old": {"first_day": "2026-01-01"}, "other": {"first_day": "2026-01-01"}}')

		self.assertNotEqual(self.days("old", 120), self.days("other", 120))

	def test_a_chance_of_zero_means_no_rest_days(self):
		self.seasoned("old")

		with mock.patch.dict(os.environ, {"REWARDS_REST_DAY_CHANCE": "0"}):
			self.assertFalse(any(self.days("old", 100)))


class TestLightDays(PacingTestCase):
	"""A rest day keeps every streak alive: the daily set and one small search, never nothing."""

	def light_day(self, account):
		for n in range(400):
			day = TODAY + timedelta(days=n)

			if pacing.is_rest_day(account, day):
				return day

		self.fail("no light day in 400 days")

	def test_a_light_day_does_only_the_daily_set_a_small_search_and_the_claim(self):
		self.seasoned("old")
		day = self.light_day("old")

		self.assertEqual(pacing.steps_allowed("old", day), pacing.LIGHT_STEPS)
		self.assertIn("Bing daily set", pacing.LIGHT_STEPS)
		self.assertIn("Required searches", pacing.LIGHT_STEPS)
		self.assertNotIn("Quests", pacing.LIGHT_STEPS)

	def test_a_light_day_still_searches_enough_to_keep_the_streak_and_the_14_day_count(self):
		self.seasoned("old")
		day = self.light_day("old")

		for cap in (25, 100):
			target = pacing.search_target("old", cap, day)

			self.assertGreaterEqual(target, 5)
			self.assertLessEqual(target, pacing.LIGHT_SEARCH_POINTS)

	def test_a_light_day_never_asks_for_more_than_the_cap(self):
		self.seasoned("old")

		self.assertEqual(pacing.search_target("old", 4, self.light_day("old")), 4)

	def test_a_working_day_is_unchanged(self):
		self.seasoned("old")
		day = next(TODAY + timedelta(days=n) for n in range(400) if not pacing.is_rest_day("old", TODAY + timedelta(days=n)))

		self.assertIsNone(pacing.steps_allowed("old", day))
		self.assertGreater(pacing.search_target("old", 100, day), pacing.LIGHT_SEARCH_POINTS)

	def test_the_description_calls_it_a_light_day(self):
		self.seasoned("old")

		with mock.patch.object(pacing, "_today", return_value=self.light_day("old")):
			self.assertIn("light day", pacing.describe("old"))


class TestDailyShare(PacingTestCase):
	def shares(self, account, count=200):
		return [pacing.fraction(account, TODAY + timedelta(days=n)) for n in range(count)]

	def test_a_working_day_fills_between_the_minimum_and_all_of_the_quota(self):
		self.seasoned("old")
		shares = self.shares("old")

		self.assertGreaterEqual(min(shares), 0.6)
		self.assertLessEqual(max(shares), 1.0)
		self.assertGreater(max(shares) - min(shares), 0.25)

	def test_it_is_not_always_the_maximum(self):
		self.seasoned("old")

		self.assertLess(sum(1 for s in self.shares("old") if s > 0.999), 5)

	def test_a_minimum_of_one_means_always_all_of_it(self):
		self.seasoned("old")

		with mock.patch.dict(os.environ, {"REWARDS_MIN_DAILY_FRACTION": "1"}):
			self.assertEqual(set(self.shares("old", 30)), {1.0})

	def test_the_target_is_a_share_of_the_cap_never_over_it_and_never_nothing(self):
		self.seasoned("old")

		for cap in (25, 50, 100, 150):
			for n in range(40):
				day = TODAY + timedelta(days=n)
				target = pacing.search_target("old", cap, day)
				self.assertTrue(1 <= target <= cap)

				if not pacing.is_rest_day("old", day):
					self.assertGreaterEqual(target, round(cap * 0.6))

	def test_a_cap_of_zero_stays_zero(self):
		self.assertEqual(pacing.search_target("old", 0), 0)

	def test_the_same_day_gives_the_same_target_so_every_run_of_it_agrees(self):
		self.seasoned("old")

		self.assertEqual(pacing.search_target("old", 100), pacing.search_target("old", 100))


class TestOrder(PacingTestCase):
	def test_the_accounts_come_in_different_orders(self):
		orders = {tuple(pacing.ordered(["a", "b", "c", "d"], rng=__import__("random").Random(seed))) for seed in range(30)}

		self.assertGreater(len(orders), 5)

	def test_nothing_is_lost_or_added(self):
		self.assertEqual(sorted(pacing.ordered(["a", "b", "c"])), ["a", "b", "c"])

	def test_the_order_can_be_kept(self):
		with mock.patch.dict(os.environ, {"REWARDS_KEEP_ORDER": "1"}):
			self.assertEqual(pacing.ordered(["a", "b", "c"]), ["a", "b", "c"])

	def test_the_list_given_is_not_changed(self):
		items = ["a", "b", "c", "d", "e", "f"]
		pacing.ordered(items)

		self.assertEqual(items, ["a", "b", "c", "d", "e", "f"])


class TestBadInput(PacingTestCase):
	def test_settings_that_are_not_numbers_fall_back(self):
		with mock.patch.dict(os.environ, {"REWARDS_REST_DAY_CHANCE": "lots", "REWARDS_MIN_DAILY_FRACTION": "x", "REWARDS_RAMP_DAYS": "?"}):
			self.assertEqual(pacing.rest_chance(), 0.10)
			self.assertEqual(pacing.min_fraction(), 0.8)
			self.assertEqual(pacing.ramp_days(), 7)

	def test_a_damaged_state_file_means_a_new_account_not_an_error(self):
		Path(pacing.STATE_FILE).write_text("{not json")

		self.assertEqual(pacing.first_day("x"), TODAY)

	def test_the_description_says_what_the_day_holds(self):
		self.assertIn("ramp day 1 of 7", pacing.describe("fresh"))
		self.seasoned("old")
		self.assertNotIn("ramp", pacing.describe("old"))


class TestQueryOverlap(unittest.TestCase):
	def setUp(self):
		directory = tempfile.TemporaryDirectory()
		self.addCleanup(directory.cleanup)
		patcher = mock.patch.object(query_history, "HISTORY_FILE", os.path.join(directory.name, "q.jsonl"))
		patcher.start()
		self.addCleanup(patcher.stop)
		self.now = time.time()

	def test_an_account_avoids_what_another_account_searched_this_week(self):
		query_history.record("default", "Best Hiking Boots", now=self.now - 86400)
		query_history.record("second", "cheap flights", now=self.now - 86400)

		self.assertEqual(query_history.avoid_for("second", now=self.now), {"best hiking boots", "cheap flights"})
		self.assertEqual(query_history.avoid_for("default", now=self.now), {"best hiking boots", "cheap flights"})

	def test_other_accounts_queries_are_forgotten_after_a_week_but_its_own_are_not(self):
		query_history.record("default", "old other query", now=self.now - 10 * 86400)
		query_history.record("second", "old own query", now=self.now - 10 * 86400)

		avoid = query_history.avoid_for("second", now=self.now)

		self.assertNotIn("old other query", avoid)
		self.assertIn("old own query", avoid)

	def test_the_accounts_only_difference_is_whose_it_is(self):
		query_history.record("default", "mine", now=self.now)

		self.assertEqual(query_history.recent_by_others("default", now=self.now), set())
		self.assertEqual(query_history.recent_by_others("second", now=self.now), {"mine"})

	def test_with_no_history_there_is_nothing_to_avoid(self):
		self.assertEqual(query_history.avoid_for("second", now=self.now), set())


if __name__ == "__main__":
	unittest.main()


class TestLazyCards(unittest.TestCase):
	def test_most_days_skip_none_and_never_more_than_two(self):
		counts = [pacing.lazy_skips("someone", date(2026, 10, 1) + timedelta(days=n)) for n in range(400)]

		self.assertTrue(set(counts) <= {0, 1, 2})
		self.assertTrue(0.40 < counts.count(0) / 400 < 0.60)
		self.assertGreater(counts.count(1), 60)
		self.assertGreater(counts.count(2), 20)

	def test_a_day_always_gives_the_same_answer(self):
		day = date(2026, 10, 9)

		self.assertEqual(
			[pacing.lazy_card_skipped("someone", i, 5, day) for i in range(5)],
			[pacing.lazy_card_skipped("someone", i, 5, day) for i in range(5)],
		)

	def test_not_every_card_is_ever_skipped(self):
		for n in range(200):
			day = date(2026, 10, 1) + timedelta(days=n)

			self.assertLess(sum(pacing.lazy_card_skipped("someone", i, 2, day) for i in range(2)), 2)
			self.assertFalse(pacing.lazy_card_skipped("someone", 0, 1, day))

	def test_the_number_skipped_is_the_days_count(self):
		for n in range(100):
			day = date(2026, 10, 1) + timedelta(days=n)

			self.assertEqual(sum(pacing.lazy_card_skipped("someone", i, 6, day) for i in range(6)), pacing.lazy_skips("someone", day))
