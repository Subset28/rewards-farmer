"""Tests for the per-account time-of-day habits in the search scheduler.

	python -m unittest discover -s tests
"""

import os
import random
import sys
import unittest
from datetime import datetime, timedelta
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import search_scheduler

WEEKDAY = datetime(2026, 10, 5)  # a Monday
WEEKEND = datetime(2026, 10, 4)  # a Sunday
OWNERS = [f"account{n}@example.com" for n in range(40)]


def minutes(moment: datetime) -> float:
	return moment.hour * 60 + moment.minute + moment.second / 60


class HabitTestCase(unittest.TestCase):
	def setUp(self):
		random.seed(4321)
		# Habits are on by default, but a developer's shell must not change the tests.
		patcher = mock.patch.dict(os.environ)
		patcher.start()
		self.addCleanup(patcher.stop)
		os.environ.pop("REWARDS_SEARCH_HABITS", None)  # no longer read; the "habits" feature is the switch


class TestHabitWindows(HabitTestCase):
	def test_an_owner_has_two_or_three_windows_of_sensible_width(self):
		for owner in OWNERS:
			for weekend in (False, True):
				windows = search_scheduler.habit_windows(owner, weekend)

				self.assertIn(len(windows), (2, 3))

				for start, end in windows:
					low, high = (60, 120) if not weekend else (90, 180)
					self.assertTrue(low - 0.001 <= end - start <= high + 0.001)

	def test_windows_are_stable_for_an_owner(self):
		for owner in OWNERS:
			self.assertEqual(search_scheduler.habit_windows(owner, False), search_scheduler.habit_windows(owner, False))
			self.assertEqual(search_scheduler.habit_windows(owner, True), search_scheduler.habit_windows(owner, True))

	def test_the_owner_name_is_case_insensitive_like_pacing(self):
		self.assertEqual(search_scheduler.habit_windows("Some@Example.com", False), search_scheduler.habit_windows("some@example.com", False))

	def test_windows_are_sorted_and_stay_in_waking_hours(self):
		for owner in OWNERS:
			for weekend in (False, True):
				windows = search_scheduler.habit_windows(owner, weekend)

				self.assertEqual(windows, sorted(windows))
				# A weekend evening window may run past END_HOUR a little; draws are clipped to the window.
				self.assertGreaterEqual(windows[0][0], search_scheduler.START_HOUR * 60 - 120)

	def test_different_owners_get_different_habits(self):
		habits = {tuple(search_scheduler.habit_windows(owner, False)) for owner in OWNERS}

		self.assertGreater(len(habits), 35)

	def test_weekends_shift_later_and_spread_wider(self):
		for owner in OWNERS:
			weekday = search_scheduler.habit_windows(owner, False)
			weekend = search_scheduler.habit_windows(owner, True)

			self.assertEqual(len(weekday), len(weekend))

			for (a, b), (c, d) in zip(weekday, weekend):
				self.assertGreater((c + d) / 2, (a + b) / 2)
				self.assertGreater(d - c, b - a)

	def test_across_owners_all_three_parts_of_the_day_are_used(self):
		centres = [(a + b) / 2 / 60 for owner in OWNERS for a, b in search_scheduler.habit_windows(owner, False)]

		self.assertTrue(any(c < 11 for c in centres))
		self.assertTrue(any(12 <= c < 16 for c in centres))
		self.assertTrue(any(c >= 17 for c in centres))


class TestHabitDraws(HabitTestCase):
	def window(self, day):
		return day.replace(hour=search_scheduler.START_HOUR), day.replace(hour=search_scheduler.END_HOUR)

	def test_a_full_day_gets_every_run_sorted_and_in_bounds(self):
		for day in (WEEKDAY, WEEKEND):
			start, end = self.window(day)

			for owner in OWNERS[:10]:
				for seed in range(20):
					random.seed(seed)
					times = search_scheduler.draw_times(day.replace(hour=0, minute=5), owner)

					self.assertEqual(len(times), search_scheduler.RUNS_PER_DAY)
					self.assertEqual(times, sorted(times))
					self.assertTrue(all(start <= t < end for t in times))

	def test_a_part_way_plan_stays_ahead_of_now_and_before_the_end(self):
		end = self.window(WEEKDAY)[1]

		for hour, minute in ((9, 17), (13, 0), (15, 45), (19, 10), (22, 0), (22, 25)):
			now = WEEKDAY.replace(hour=hour, minute=minute)

			for seed in range(40):
				random.seed(seed)
				times = search_scheduler.draw_times(now, OWNERS[seed % len(OWNERS)])

				self.assertEqual(times, sorted(times))
				self.assertTrue(all(now + timedelta(minutes=2) <= t < end for t in times))

	def test_the_count_rule_is_the_same_with_and_without_an_owner(self):
		for hour, minute in ((0, 5), (8, 0), (12, 0), (15, 0), (20, 0), (21, 30), (22, 29), (22, 40), (23, 30)):
			now = WEEKDAY.replace(hour=hour, minute=minute)

			self.assertEqual(len(search_scheduler.draw_times(now, "someone")), len(search_scheduler.draw_times(now)))

	def test_a_full_day_has_exactly_the_configured_runs(self):
		self.assertEqual(len(search_scheduler.draw_times(WEEKDAY.replace(hour=1), "someone")), search_scheduler.RUNS_PER_DAY)

	def test_at_least_one_run_while_half_an_hour_remains_and_none_after(self):
		self.assertGreaterEqual(len(search_scheduler.draw_times(WEEKDAY.replace(hour=21, minute=30), "someone")), 1)
		self.assertEqual(search_scheduler.draw_times(WEEKDAY.replace(hour=22, minute=40), "someone"), [])
		self.assertEqual(search_scheduler.draw_times(WEEKDAY.replace(hour=23, minute=30), "someone"), [])

	def test_the_same_owner_keeps_windows_but_not_exact_times_across_days(self):
		owner = OWNERS[0]
		tuesday = WEEKDAY + timedelta(days=1)

		self.assertEqual(search_scheduler.habit_windows(owner, WEEKDAY.weekday() >= 5), search_scheduler.habit_windows(owner, tuesday.weekday() >= 5))

		monday_times = [minutes(t) for t in search_scheduler.draw_times(WEEKDAY.replace(hour=0, minute=5), owner)]
		tuesday_times = [minutes(t) for t in search_scheduler.draw_times(tuesday.replace(hour=0, minute=5), owner)]

		self.assertNotEqual(monday_times, tuesday_times)

	def share_in_windows(self, owner, day, samples=1500):
		windows = search_scheduler.habit_windows(owner, day.weekday() >= 5)
		slack = 3 * search_scheduler.HABIT_JITTER_MINUTES
		inside = total = 0

		for _ in range(samples):
			for t in search_scheduler.draw_times(day.replace(hour=0, minute=5), owner):
				total += 1
				inside += any(a - slack <= minutes(t) <= b + slack for a, b in windows)

		return inside / total

	def test_about_three_quarters_of_draws_land_in_the_favoured_windows(self):
		for owner in OWNERS[:5]:
			# Uniform draws add some chance hits on top of the 75%, so the share sits a little above it.
			for day in (WEEKDAY, WEEKEND):
				share = self.share_in_windows(owner, day)

				self.assertTrue(0.74 <= share <= 0.92, f"{owner} {day:%A}: {share:.3f}")

	def test_without_a_habit_the_share_is_just_the_chance_coverage(self):
		owner = OWNERS[0]
		windows = search_scheduler.habit_windows(owner, False)
		covered = sum(b - a for a, b in windows) / ((search_scheduler.END_HOUR - search_scheduler.START_HOUR) * 60)
		times = [minutes(t) for _ in range(1500) for t in search_scheduler.draw_times(WEEKDAY.replace(hour=0, minute=5))]
		share = sum(any(a <= m <= b for a, b in windows) for m in times) / len(times)

		self.assertLess(abs(share - covered), 0.05)

	def test_weekday_and_weekend_draws_differ_in_when_they_land(self):
		owner = OWNERS[3]

		def mean_minute(day):
			random.seed(99)
			times = [minutes(t) for _ in range(1500) for t in search_scheduler.draw_times(day.replace(hour=0, minute=5), owner)]

			return sum(times) / len(times)

		self.assertGreater(mean_minute(WEEKEND), mean_minute(WEEKDAY))

	def test_different_owners_draw_from_different_places(self):
		def histogram(owner):
			random.seed(7)
			counts = [0] * 15

			for _ in range(800):
				for t in search_scheduler.draw_times(WEEKDAY.replace(hour=0, minute=5), owner):
					counts[t.hour - search_scheduler.START_HOUR] += 1

			return counts

		self.assertNotEqual(histogram(OWNERS[0]), histogram(OWNERS[1]))

	def test_draws_are_reproducible_under_a_seed(self):
		random.seed(5)
		first = search_scheduler.draw_times(WEEKDAY.replace(hour=0, minute=5), "someone")
		random.seed(5)

		self.assertEqual(first, search_scheduler.draw_times(WEEKDAY.replace(hour=0, minute=5), "someone"))


class TestHabitsOff(HabitTestCase):
	def test_no_owner_is_uniform_and_matches_the_old_draw(self):
		now = WEEKDAY.replace(hour=0, minute=5)
		start = now.replace(hour=search_scheduler.START_HOUR, minute=0)
		span = (now.replace(hour=search_scheduler.END_HOUR, minute=0) - start).total_seconds()
		random.seed(11)
		expected = sorted(start + timedelta(seconds=random.uniform(0, span)) for _ in range(search_scheduler.RUNS_PER_DAY))
		random.seed(11)

		self.assertEqual(search_scheduler.draw_times(now), expected)

	def test_the_switch_turns_habits_off(self):
		now = WEEKDAY.replace(hour=0, minute=5)

		# The "habits" feature (features.py) is the switch; the suite turns it on for everyone.
		with mock.patch.dict(os.environ, {"REWARDS_FEATURES": ""}):
			random.seed(11)
			with_owner = search_scheduler.draw_times(now, "someone")
			random.seed(11)

			self.assertEqual(with_owner, search_scheduler.draw_times(now))

	def test_the_switch_is_the_features_file_not_a_default(self):
		self.assertTrue(search_scheduler.habits_enabled("someone"))

		with mock.patch.dict(os.environ, {"REWARDS_FEATURES": ""}):
			self.assertFalse(search_scheduler.habits_enabled("someone"))

	def test_day_plan_passes_the_owner_through(self):
		with mock.patch.object(search_scheduler.schedule_plan, "read", return_value={}), mock.patch.object(search_scheduler.schedule_plan, "write"), mock.patch.object(search_scheduler, "draw_times", return_value=[]) as draw:
			search_scheduler.day_plan(WEEKDAY, "someone")

		draw.assert_called_once_with(WEEKDAY, "someone")


if __name__ == "__main__":
	unittest.main()
