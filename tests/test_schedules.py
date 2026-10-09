"""Tests for the schedulers' plans surviving a restart.

	python -m unittest discover -s tests
"""

import os
import random
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import daily_loop
import schedule_plan
import search_scheduler


class PlanFileTestCase(unittest.TestCase):
	def setUp(self):
		directory = tempfile.TemporaryDirectory()
		self.addCleanup(directory.cleanup)
		self.plan_file = os.path.join(directory.name, "schedule_plan.json")
		patcher = mock.patch.object(schedule_plan, "PLAN_FILE", self.plan_file)
		patcher.start()
		self.addCleanup(patcher.stop)
		random.seed(1234)


class TestSchedulePlan(PlanFileTestCase):
	def test_nothing_saved_is_an_empty_plan(self):
		self.assertEqual(schedule_plan.read("search", "default"), {})

	def test_a_plan_round_trips(self):
		schedule_plan.write("search", "default", {"day": "2026-10-04", "times": ["a"]})

		self.assertEqual(schedule_plan.read("search", "default"), {"day": "2026-10-04", "times": ["a"]})

	def test_each_scheduler_and_kind_has_its_own_plan(self):
		schedule_plan.write("search", "default", {"n": 1})
		schedule_plan.write("search", "second", {"n": 2})
		schedule_plan.write("daily", "default", {"n": 3})

		self.assertEqual(schedule_plan.read("search", "default"), {"n": 1})
		self.assertEqual(schedule_plan.read("search", "second"), {"n": 2})
		self.assertEqual(schedule_plan.read("daily", "default"), {"n": 3})

	def test_a_damaged_file_is_no_plan_and_can_be_written_over(self):
		with open(self.plan_file, "w") as handle:
			handle.write("{not json")

		self.assertEqual(schedule_plan.read("search", "default"), {})

		schedule_plan.write("search", "default", {"n": 1})

		self.assertEqual(schedule_plan.read("search", "default"), {"n": 1})

	def test_a_file_of_the_wrong_shape_is_no_plan(self):
		for content in ("[1, 2]", '{"search": "nope"}', '{"search": {"default": 5}}'):
			with open(self.plan_file, "w") as handle:
				handle.write(content)

			self.assertEqual(schedule_plan.read("search", "default"), {})

	def test_an_unwritable_location_does_not_raise(self):
		with mock.patch.object(schedule_plan, "PLAN_FILE", os.path.join(os.devnull, "nope", "plan.json")):
			schedule_plan.write("search", "default", {"n": 1})

			self.assertEqual(schedule_plan.read("search", "default"), {})


class TestDrawTimes(PlanFileTestCase):
	DAY = datetime(2026, 10, 4)

	def window(self):
		return self.DAY.replace(hour=search_scheduler.START_HOUR), self.DAY.replace(hour=search_scheduler.END_HOUR)

	def test_a_day_that_has_not_started_gets_every_run_inside_the_window(self):
		start, end = self.window()

		for seed in range(30):
			random.seed(seed)
			times = search_scheduler.draw_times(self.DAY.replace(hour=0, minute=5))

			self.assertEqual(len(times), search_scheduler.RUNS_PER_DAY)
			self.assertEqual(times, sorted(times))
			self.assertTrue(all(start <= t < end for t in times))

	def test_a_plan_made_part_way_through_has_proportionally_fewer_runs_all_ahead(self):
		now = self.DAY.replace(hour=15, minute=0)
		end = self.window()[1]

		for seed in range(30):
			random.seed(seed)
			times = search_scheduler.draw_times(now)

			self.assertTrue(1 <= len(times) < search_scheduler.RUNS_PER_DAY)
			self.assertTrue(all(now + timedelta(minutes=2) <= t < end for t in times))

	def test_a_late_plan_still_has_at_least_one_run_while_there_is_room(self):
		now = self.DAY.replace(hour=21, minute=30)

		self.assertGreaterEqual(len(search_scheduler.draw_times(now)), 1)

	def test_a_plan_with_under_half_an_hour_left_has_none(self):
		self.assertEqual(search_scheduler.draw_times(self.DAY.replace(hour=22, minute=40)), [])

	def test_after_the_window_there_is_nothing(self):
		self.assertEqual(search_scheduler.draw_times(self.DAY.replace(hour=23, minute=30)), [])


class TestPlannedTimes(PlanFileTestCase):
	DAY = datetime(2026, 10, 4)

	def test_the_first_call_of_the_day_draws_and_saves(self):
		times = search_scheduler.planned_times(self.DAY.replace(hour=0, minute=1), "default")

		saved = schedule_plan.read("search", "default")

		self.assertEqual(len(times), search_scheduler.RUNS_PER_DAY)
		self.assertEqual(saved["day"], "2026-10-04")
		self.assertEqual(len(saved["times"]), search_scheduler.RUNS_PER_DAY)

	def test_a_restart_keeps_the_same_plan_instead_of_redrawing(self):
		first = search_scheduler.planned_times(self.DAY.replace(hour=0, minute=1), "default")

		random.seed(99999)
		after_restart = search_scheduler.planned_times(self.DAY.replace(hour=0, minute=2), "default")

		self.assertEqual(first, after_restart)

	def test_a_mid_day_restart_keeps_only_the_runs_still_ahead_of_the_same_plan(self):
		first = search_scheduler.planned_times(self.DAY.replace(hour=0, minute=1), "default")
		middle = first[1] + timedelta(minutes=1)

		random.seed(424242)
		after = search_scheduler.planned_times(middle, "default")

		self.assertEqual(after, first[2:])

	def test_many_restarts_never_add_or_move_a_run(self):
		first = search_scheduler.planned_times(self.DAY.replace(hour=0, minute=1), "default")
		seen = set()

		for minutes in range(60, 1300, 90):
			random.seed(minutes)

			for t in search_scheduler.planned_times(self.DAY.replace(hour=0) + timedelta(minutes=minutes), "default"):
				seen.add(t)

		self.assertTrue(seen <= set(first))

	def test_a_new_day_draws_a_new_plan(self):
		search_scheduler.planned_times(self.DAY.replace(hour=0, minute=1), "default")
		tomorrow = search_scheduler.planned_times((self.DAY + timedelta(days=1)).replace(hour=0, minute=1), "default")

		self.assertEqual(len(tomorrow), search_scheduler.RUNS_PER_DAY)
		self.assertTrue(all(t.day == 5 for t in tomorrow))
		self.assertEqual(schedule_plan.read("search", "default")["day"], "2026-10-05")

	def test_a_late_first_start_gets_a_proportionate_plan_not_a_lopsided_one(self):
		times = search_scheduler.planned_times(self.DAY.replace(hour=13, minute=11), "default")

		self.assertTrue(len(times) >= 1)
		self.assertTrue(all(t > self.DAY.replace(hour=13, minute=11) for t in times))

	def test_a_damaged_or_odd_saved_plan_is_redrawn(self):
		schedule_plan.write("search", "default", {"day": "2026-10-04", "times": ["not a time", 5, None]})

		times = search_scheduler.planned_times(self.DAY.replace(hour=0, minute=1), "default")

		self.assertEqual(len(times), search_scheduler.RUNS_PER_DAY)

	def test_one_bad_entry_does_not_throw_away_the_rest(self):
		good = (self.DAY.replace(hour=14)).isoformat()
		schedule_plan.write("search", "default", {"day": "2026-10-04", "times": ["junk", good]})

		self.assertEqual(search_scheduler.planned_times(self.DAY.replace(hour=1), "default"), [datetime.fromisoformat(good)])

	def test_each_account_scheduler_has_its_own_plan(self):
		one = search_scheduler.planned_times(self.DAY.replace(hour=0, minute=1), "default")
		two = search_scheduler.planned_times(self.DAY.replace(hour=0, minute=1), "second")

		self.assertNotEqual(one, two)
		self.assertEqual(search_scheduler.planned_times(self.DAY.replace(hour=0, minute=2), "default"), one)


class TestDailyPlan(PlanFileTestCase):
	NOW = datetime(2026, 10, 4, 13, 11)

	def test_a_first_start_plans_the_next_anchor_plus_jitter_and_saves_it(self):
		at = daily_loop.plan_next_run(self.NOW, "default")

		anchor = datetime(2026, 10, 5, daily_loop.ANCHOR_HOUR)

		self.assertTrue(anchor <= at <= anchor + timedelta(seconds=daily_loop.JITTER_SECONDS))
		self.assertEqual(schedule_plan.read("daily", "default")["at"], at.isoformat())
		self.assertFalse(schedule_plan.read("daily", "default")["ran"])

	def test_a_restart_keeps_the_planned_moment_instead_of_redrawing_the_jitter(self):
		first = daily_loop.plan_next_run(self.NOW, "default")

		random.seed(31337)

		self.assertEqual(daily_loop.plan_next_run(self.NOW + timedelta(hours=3), "default"), first)

	def test_a_restart_between_the_anchor_and_the_drawn_start_no_longer_skips_the_day(self):
		# Planned for 10:45; restarted at 09:30, after the 09:00 anchor. The old code
		# looked for the next anchor, which is tomorrow's, and skipped today.
		planned = datetime(2026, 10, 5, 10, 45)
		schedule_plan.write("daily", "default", {"at": planned.isoformat(), "ran": False})

		self.assertEqual(daily_loop.plan_next_run(datetime(2026, 10, 5, 9, 30), "default"), planned)

	def test_a_run_missed_while_down_is_run_at_once_if_not_too_late(self):
		planned = datetime(2026, 10, 5, 10, 45)
		schedule_plan.write("daily", "default", {"at": planned.isoformat(), "ran": False})
		now = planned + timedelta(hours=2)

		self.assertEqual(daily_loop.plan_next_run(now, "default"), now)

	def test_a_run_missed_by_more_than_the_catch_up_window_is_replanned(self):
		planned = datetime(2026, 10, 5, 10, 45)
		schedule_plan.write("daily", "default", {"at": planned.isoformat(), "ran": False})
		now = planned + daily_loop.CATCH_UP + timedelta(minutes=1)

		at = daily_loop.plan_next_run(now, "default")

		self.assertGreater(at, now)
		self.assertNotEqual(at, planned)

	def test_a_run_that_already_happened_is_not_run_again(self):
		planned = datetime(2026, 10, 5, 10, 45)
		schedule_plan.write("daily", "default", {"at": planned.isoformat(), "ran": True})
		now = planned + timedelta(hours=1)

		at = daily_loop.plan_next_run(now, "default")

		self.assertGreater(at, now + timedelta(hours=1))

	def test_marking_a_run_done_means_the_next_plan_is_a_new_one(self):
		first = daily_loop.plan_next_run(self.NOW, "default")
		daily_loop.mark_done(first, "default")

		self.assertTrue(schedule_plan.read("daily", "default")["ran"])
		self.assertGreater(daily_loop.plan_next_run(first + timedelta(minutes=5), "default"), first + timedelta(hours=1))

	def test_each_account_scheduler_has_its_own_plan(self):
		one = daily_loop.plan_next_run(self.NOW, "default")
		two = daily_loop.plan_next_run(self.NOW, "second")

		self.assertNotEqual(one, two)

	def test_a_damaged_saved_time_is_replanned(self):
		for value in ("garbage", 12345, None, ""):
			schedule_plan.write("daily", "default", {"at": value, "ran": False})

			at = daily_loop.plan_next_run(self.NOW, "default")

			self.assertGreater(at, self.NOW)

	def test_a_damaged_plan_file_is_replanned(self):
		with open(self.plan_file, "w") as handle:
			handle.write("{nope")

		self.assertGreater(daily_loop.plan_next_run(self.NOW, "default"), self.NOW)

	def test_before_todays_anchor_the_plan_is_for_today(self):
		early = datetime(2026, 10, 4, 6, 0)
		at = daily_loop.plan_next_run(early, "default")

		self.assertEqual(at.day, 4)


if __name__ == "__main__":
	unittest.main()


class TestPlanningTurns(unittest.TestCase):
	def setUp(self):
		directory = tempfile.TemporaryDirectory()
		self.addCleanup(directory.cleanup)
		patcher = mock.patch.object(schedule_plan, "PLAN_FILE", os.path.join(directory.name, "schedule_plan.json"))
		patcher.start()
		self.addCleanup(patcher.stop)
		self.lock = schedule_plan.PLAN_FILE + ".lock"

	def test_the_lock_is_taken_and_released(self):
		with schedule_plan.planning():
			self.assertTrue(os.path.exists(self.lock))

		self.assertFalse(os.path.exists(self.lock))

	def test_a_crashed_schedulers_old_lock_is_cleared(self):
		with open(self.lock, "w"):
			pass

		old = time.time() - schedule_plan.LOCK_STALE_SECONDS - 5
		os.utime(self.lock, (old, old))

		with schedule_plan.planning(timeout=1):
			self.assertTrue(os.path.exists(self.lock))

		self.assertFalse(os.path.exists(self.lock))

	def test_it_goes_ahead_after_the_timeout_when_the_lock_is_held(self):
		with open(self.lock, "w"):
			pass

		started = time.monotonic()

		with schedule_plan.planning(timeout=0.3):
			pass

		self.assertLess(time.monotonic() - started, 2)
		self.assertTrue(os.path.exists(self.lock))  # someone else's, left alone

	def test_planners_starting_together_take_turns_and_end_up_apart(self):
		import threading
		from datetime import datetime, timedelta
		import search_scheduler

		now = datetime(2026, 10, 9, 0, 0, 42)
		names = ["default", "second", "third"]
		results = {}

		def plan(name):
			with mock.patch.object(search_scheduler, "account_names", return_value=[name]):
				results[name] = search_scheduler.day_plan(now, name)

		threads = [threading.Thread(target=plan, args=(n,)) for n in names]

		for t in threads:
			t.start()

		for t in threads:
			t.join()

		self.assertEqual(sorted(results), sorted(names))

		# Whichever planned first could not see the others; every later one kept 15 minutes or more from all before it.
		everyone = sorted((t, n) for n, times in results.items() for t in times)
		cross = [(b[0] - a[0]) for a, b in zip(everyone, everyone[1:]) if a[1] != b[1]]

		self.assertTrue(all(gap >= timedelta(minutes=15) for gap in cross), cross)
