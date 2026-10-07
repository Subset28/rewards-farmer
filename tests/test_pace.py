"""The pauses between actions: skewed like a person's when switched on, the old flat range otherwise."""

import math
import os
import random
import statistics
import sys
import unittest
from datetime import date
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pace
import rewards_tasks


class Case(unittest.TestCase):
	def setUp(self):
		for patcher in (
			mock.patch.dict(os.environ, {"REWARDS_FEATURES": "typing"}),
			mock.patch.object(pace.clock, "today", return_value=date(2026, 10, 8)),
		):
			patcher.start()
			self.addCleanup(patcher.stop)

	def draws(self, count=4000, low=2.0, high=4.0, detail=None, seed=1):
		pacer = pace.Pace("mom", detail or {}, random.Random(seed))

		return [pacer.draw(low, high) for _ in range(count)]


class TestTheSpread(Case):
	def test_the_body_sits_where_the_old_range_did(self):
		draws = self.draws(detail={"day_sd": 0.0, "tempo_sd": 0.0001})

		self.assertAlmostEqual(statistics.median(draws), 3.0, delta=0.2)

	def test_it_is_not_flat_across_the_range(self):
		draws = self.draws(detail={"day_sd": 0.0})
		middle = sum(1 for d in draws if 2.5 <= d <= 3.5) / len(draws)
		edges = sum(1 for d in draws if 2.0 <= d < 2.25 or 3.75 < d <= 4.0) / len(draws)

		self.assertGreater(middle, 3 * edges)

	def test_it_runs_long_now_and_then(self):
		draws = self.draws(detail={"day_sd": 0.0})

		self.assertGreater(sum(1 for d in draws if d > 4.5) / len(draws), 0.02)

	def test_it_stays_inside_what_a_person_would_wait(self):
		draws = self.draws()

		self.assertGreaterEqual(min(draws), 2.0 * 0.6)
		self.assertLessEqual(max(draws), 4.0 * 3.5)

	def test_a_slow_day_makes_every_wait_longer(self):
		with mock.patch.object(pace.human_model, "normal_for", return_value=2.0):
			slow = statistics.median(self.draws(detail={"day_sd": 0.2, "tempo_sd": 0.0001}))

		with mock.patch.object(pace.human_model, "normal_for", return_value=0.0):
			normal = statistics.median(self.draws(detail={"day_sd": 0.2, "tempo_sd": 0.0001}))

		self.assertGreater(slow, normal * 1.3)

	def test_the_tempo_wanders_from_pause_to_pause(self):
		pacer = pace.Pace("mom", {"day_sd": 0.0, "tempo_sd": 0.2}, random.Random(3))
		factors = [math.log(pacer.factor()) for _ in range(3000)]
		lag = sum(a * b for a, b in zip(factors, factors[1:])) / sum(a * a for a in factors)

		self.assertGreater(lag, 0.6)
		self.assertAlmostEqual(statistics.pstdev(factors), 0.2, delta=0.05)

	def test_a_degenerate_range_is_handled(self):
		pacer = pace.Pace("mom", {}, random.Random(1))

		self.assertEqual(pacer.draw(2.0, 2.0), 2.0)
		self.assertGreater(pacer.draw(0.0, 0.0), 0.0)


class TestSwitchedOff(Case):
	def test_without_the_switch_it_is_the_old_flat_range(self):
		with mock.patch.dict(os.environ, {"REWARDS_FEATURES": ""}), mock.patch.object(pace.time, "sleep") as sleep:
			pacer = pace.Pace("mom", {}, random.Random(1))

			for _ in range(500):
				pacer.sleep(2, 4)

		waits = [call.args[0] for call in sleep.call_args_list]

		self.assertTrue(all(2 <= w <= 4 for w in waits))

	def test_with_the_switch_it_sleeps_the_drawn_time(self):
		with mock.patch.object(pace.time, "sleep") as sleep:
			pace.Pace("mom", {}, random.Random(1)).sleep(2, 4)

		self.assertEqual(len(sleep.call_args_list), 1)

	def test_an_account_with_no_name_is_never_paced(self):
		self.assertFalse(pace.Pace(None, {}).active())


class TestTheTaskCodeUsesIt(Case):
	def test_an_object_without_a_pacer_gets_the_old_wait(self):
		with mock.patch.object(rewards_tasks.time, "sleep") as sleep:
			rewards_tasks._wait(object(), 2, 4)

		self.assertTrue(2 <= sleep.call_args.args[0] <= 4)

	def test_an_object_with_a_pacer_uses_it(self):
		owner = mock.Mock()
		rewards_tasks._wait(owner, 2, 4)

		owner.pace.sleep.assert_called_once_with(2, 4)

	def test_no_fixed_random_range_is_left_in_the_task_code(self):
		with open(os.path.join(os.path.dirname(__file__), "..", "src", "rewards_tasks.py"), encoding="utf-8") as handle:
			text = handle.read()

		self.assertNotIn("time.sleep(random.uniform(", text.replace("time.sleep(random.uniform(low, high))", ""))


if __name__ == "__main__":
	unittest.main()
