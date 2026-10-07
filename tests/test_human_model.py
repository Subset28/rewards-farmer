"""The model of how a person varies, and that measuring a person gets the same numbers back.

The round-trip tests are the ones that matter: a typist is made from known settings, their typing is
recorded as if at a keyboard, the calibration measures it, and the settings it reports have to be the
ones the typist was made from. If measuring and generating disagreed, the bot would be inconsistent
by the wrong amount.
"""

import math
import os
import random
import statistics
import sys
import unittest
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import calibration
import human_model

TRUE = {
	"log_gap_mu": math.log(0.16),
	"within_sigma": 0.34,
	"sigma_sd": 0.05,
	"tempo_sd": 0.14,
	"tempo_phi": 0.35,
	"gap_phi": 0.30,
	"offset_alternate": -0.10,
	"offset_same_hand": 0.04,
	"offset_same_finger": 0.30,
	"offset_other": 0.12,
	"offset_common_pair": -0.22,
	"day_sd": 0.0,
}

TEXTS = calibration.PHRASES


def typed_record(text, rhythm, shown_at=100.0):
	"""A PhraseRecord of a typist (a TypingRhythm) typing `text`, with the times a keyboard would see."""
	record = calibration.PhraseRecord(text, shown_at)
	rhythm.start_search()
	now = shown_at + 1.2
	record.events.append((now, "char", text[0]))

	for previous, char in zip(text, text[1:]):
		now += rhythm.next_gap(previous, char)
		record.events.append((now, "char", char))

	now += 0.4
	record.events.append((now, "enter", ""))
	record.final = text

	return record


def sitting(detail, count=40, seed=1, day_z=0.0):
	rng = random.Random(seed)
	rhythm = human_model.TypingRhythm(detail, rng, day_z)

	return [typed_record(TEXTS[n % len(TEXTS)], rhythm) for n in range(count)]


class TestTransitions(unittest.TestCase):
	def test_hands_and_fingers(self):
		self.assertEqual(human_model.transition("f", "j"), "alternate")
		self.assertEqual(human_model.transition("a", "s"), "same_hand")
		self.assertEqual(human_model.transition("f", "r"), "same_finger")
		self.assertEqual(human_model.transition("e", "e"), "same_finger")
		self.assertEqual(human_model.transition(" ", "a"), "other")
		self.assertEqual(human_model.transition("a", "1"), "other")

	def test_case_does_not_matter(self):
		self.assertEqual(human_model.transition("F", "J"), "alternate")

	def test_common_pairs(self):
		self.assertTrue(human_model.common_pair("t", "h"))
		self.assertTrue(human_model.common_pair("E", "R"))
		self.assertFalse(human_model.common_pair("q", "z"))


class TestTheDrawing(unittest.TestCase):
	def gaps(self, detail, count=4000, seed=3, day_z=0.0):
		rng = random.Random(seed)
		rhythm = human_model.TypingRhythm(detail, rng, day_z)
		rhythm.start_search()

		return [rhythm.next_gap("a", "s") for _ in range(count)]

	def test_gaps_stay_inside_what_a_person_can_do(self):
		for g in self.gaps({**TRUE, "within_sigma": 1.0}):
			self.assertTrue(human_model.MIN_GAP <= g <= human_model.MAX_GAP)

	def test_the_typical_gap_is_the_persons_own(self):
		median = statistics.median(self.gaps({**TRUE, "tempo_sd": 0.0, "sigma_sd": 0.0}))

		# "a" then "s" is a move along one hand, and "as" is one of the common pairs.
		expected = 0.16 * math.exp(TRUE["offset_same_hand"] + TRUE["offset_common_pair"])

		self.assertAlmostEqual(median, expected, delta=0.012)

	def test_a_slower_transition_is_slower(self):
		rng = random.Random(4)
		rhythm = human_model.TypingRhythm({**TRUE, "tempo_sd": 0.0, "sigma_sd": 0.0}, rng)
		rhythm.start_search()
		same_finger = statistics.median(rhythm.next_gap("f", "r") for _ in range(2000))
		alternate = statistics.median(rhythm.next_gap("f", "j") for _ in range(2000))

		self.assertGreater(same_finger, alternate * 1.3)

	def test_a_run_of_quick_keys_is_followed_by_another(self):
		gaps = [math.log(g) for g in self.gaps({**TRUE, "gap_phi": 0.5, "tempo_sd": 0.0, "sigma_sd": 0.0})]
		centre = statistics.mean(gaps)
		lag = sum((a - centre) * (b - centre) for a, b in zip(gaps, gaps[1:])) / sum((a - centre) ** 2 for a in gaps)

		self.assertAlmostEqual(lag, 0.5, delta=0.06)

	def test_the_tempo_is_different_in_different_searches(self):
		rng = random.Random(5)
		rhythm = human_model.TypingRhythm(TRUE, rng)
		means = []

		for _ in range(300):
			rhythm.start_search()
			means.append(statistics.mean(math.log(rhythm.next_gap("a", "s")) for _ in range(40)))

		self.assertGreater(statistics.pstdev(means), 0.10)

	def test_a_bot_with_no_tempo_spread_would_be_the_same_every_search(self):
		rng = random.Random(5)
		rhythm = human_model.TypingRhythm({**TRUE, "tempo_sd": 0.0, "sigma_sd": 0.0, "within_sigma": 0.0001}, rng)
		means = []

		for _ in range(50):
			rhythm.start_search()
			means.append(statistics.mean(math.log(rhythm.next_gap("a", "s")) for _ in range(10)))

		# Only the small floor on a search's steadiness is left; far below a person's own spread.
		self.assertLess(statistics.pstdev(means), 0.05)

	def test_a_day_factor_is_stable_through_the_day_and_different_between_days(self):
		today, tomorrow = date(2026, 10, 7), date(2026, 10, 8)

		self.assertEqual(human_model.day_factor("mom", 0.1, today), human_model.day_factor("MOM", 0.1, today))
		self.assertNotEqual(human_model.day_factor("mom", 0.1, today), human_model.day_factor("mom", 0.1, tomorrow))
		self.assertNotEqual(human_model.day_factor("mom", 0.1, today), human_model.day_factor("dad", 0.1, today))

	def test_days_are_spread_as_the_person_says(self):
		factors = [math.log(human_model.day_factor("mom", 0.1, date(2026, 1, 1).replace(year=2000 + n % 20, month=1 + n % 12, day=1 + n % 27))) for n in range(400)]

		self.assertAlmostEqual(statistics.pstdev(factors), 0.1, delta=0.03)

	def test_a_slow_day_shifts_every_gap(self):
		normal = statistics.median(self.gaps({**TRUE, "day_sd": 0.2}, day_z=0.0))
		slow = statistics.median(self.gaps({**TRUE, "day_sd": 0.2}, day_z=1.5))

		self.assertGreater(slow, normal * 1.25)


class TestMeasuringGetsTheSettingsBack(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		cls.measured = calibration.analyze_typing([sitting(TRUE, 44, seed=11)])["detail"]

	def test_the_typical_gap(self):
		self.assertAlmostEqual(self.measured["log_gap_mu"], TRUE["log_gap_mu"], delta=0.07)

	def test_the_key_to_key_spread(self):
		self.assertAlmostEqual(self.measured["within_sigma"], TRUE["within_sigma"], delta=0.06)

	def test_how_much_quick_runs_carry_over(self):
		self.assertAlmostEqual(self.measured["gap_phi"], TRUE["gap_phi"], delta=0.15)

	def test_the_search_to_search_spread(self):
		self.assertAlmostEqual(self.measured["tempo_sd"], TRUE["tempo_sd"], delta=0.07)

	def test_slower_and_faster_transitions(self):
		self.assertLess(self.measured["offset_alternate"], self.measured["offset_same_hand"])
		self.assertLess(self.measured["offset_same_hand"], self.measured["offset_same_finger"])
		self.assertAlmostEqual(self.measured["offset_same_finger"] - self.measured["offset_alternate"], 0.40, delta=0.2)

	def test_common_pairs_are_found_quicker(self):
		self.assertLess(self.measured["offset_common_pair"], -0.08)

	def test_a_single_sitting_says_the_day_spread_is_assumed(self):
		self.assertEqual(self.measured["day_sd_measured"], 0.0)
		self.assertEqual(self.measured["sittings"], 1)

	def test_a_typist_made_from_what_was_measured_types_like_the_original(self):
		records = sitting(TRUE, 44, seed=11)
		fit = calibration.rhythm_check([records], self.measured, random.Random(2))

		if fit is None:
			self.skipTest("scipy is not installed")

		self.assertLess(fit["ks"], 0.10)
		self.assertAlmostEqual(fit["tempo_sd_simulated"], fit["tempo_sd_recorded"], delta=0.08)


class TestDayToDay(unittest.TestCase):
	def test_sittings_on_different_days_reveal_how_much_the_days_differ(self):
		days = [sitting({**TRUE, "day_sd": 0.15}, 30, seed=20 + n, day_z=z) for n, z in enumerate((-1.2, 0.1, 1.3))]
		measured = calibration.analyze_typing(days)["detail"]

		self.assertEqual(measured["day_sd_measured"], 1.0)
		self.assertEqual(measured["sittings"], 3)
		self.assertGreater(measured["day_sd"], 0.08)

	def test_one_day_alone_uses_a_modest_assumption(self):
		measured = calibration.analyze_typing([sitting(TRUE, 30, seed=2)])["detail"]

		self.assertEqual(measured["day_sd"], calibration.PRIOR_DAY_SD)


def move_trials(a, b, rel_sd, phi, count=60, seed=9):
	"""Trials whose time scatters around the Fitts line by rel_sd, carrying over by phi."""
	import calibrate

	rng = random.Random(seed)
	targets = calibration.make_targets(count, 1920, 1080, rng)
	trials, wobble = [], 0.0

	for n, (x, y, w, h) in enumerate(targets):
		cx, cy = 960, 540
		tx, ty = x + w / 2, y + h / 2
		distance = math.hypot(tx - cx, ty - cy)
		index = math.log2(max(2 * distance / ((w + h) / 2), 1.0))
		wobble = phi * wobble + math.sqrt(1 - phi ** 2) * rel_sd * rng.gauss(0, 1)
		movement_time = (a + b * index) * math.exp(wobble)
		start = 1000.0 + n * 5
		trial = calibration.Trial((cx, cy), (x, y, w, h), start)
		trial.move(start + 0.3, cx + 10, cy + 10)
		steps = 8

		for k in range(1, steps + 1):
			trial.move(start + 0.3 + movement_time * k / steps, cx + (tx - cx) * k / steps, cy + (ty - cy) * k / steps)

		trial.press(start + 0.3 + movement_time, tx, ty)
		trial.release(start + 0.3 + movement_time + 0.09)
		trials.append(trial)

	assert calibrate  # the module the trials are made for must import cleanly

	return trials


class TestMouseScatter(unittest.TestCase):
	def test_the_scatter_around_the_line_is_recovered(self):
		measured = calibration.analyze_mouse([move_trials(0.25, 0.14, 0.18, 0.1, count=80)])

		self.assertAlmostEqual(measured["fitts_a"], 0.25, delta=0.08)
		self.assertAlmostEqual(measured["fitts_b"], 0.14, delta=0.04)
		self.assertAlmostEqual(measured["detail"]["move_rel_sd"], 0.18, delta=0.05)

	def test_moves_with_no_scatter_measure_as_nearly_none(self):
		measured = calibration.analyze_mouse([move_trials(0.25, 0.14, 0.0, 0.0, count=40)])

		self.assertLess(measured["detail"]["move_rel_sd"], 0.05)

	def test_the_drawn_moves_scatter_as_measured(self):
		rng = random.Random(7)
		tempo = human_model.MoveTempo({"move_rel_sd": 0.2, "move_phi": 0.25, "day_sd": 0.0}, rng)
		factors = [math.log(tempo.next_factor()) for _ in range(6000)]

		self.assertAlmostEqual(statistics.pstdev(factors), 0.2, delta=0.04)

	def test_a_slow_day_slows_every_move(self):
		normal = human_model.MoveTempo({"move_rel_sd": 0.0001, "move_phi": 0.0, "day_sd": 0.2}, random.Random(1), 0.0).next_factor()
		slow = human_model.MoveTempo({"move_rel_sd": 0.0001, "move_phi": 0.0, "day_sd": 0.2}, random.Random(1), 1.5).next_factor()

		self.assertGreater(slow, normal * 1.25)


if __name__ == "__main__":
	unittest.main()
