"""Slips fall where a person is quick and in everyday words; that is measured, and the bot's typos follow it."""

import os
import random
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import calibration
import human_model
import mimic_typing
import search_behavior
from unittest import mock

import behavior

RHYTHM = {
	"log_gap_mu": -1.85, "within_sigma": 0.34, "sigma_sd": 0.05, "tempo_sd": 0.12, "tempo_phi": 0.3, "gap_phi": 0.3,
	"offset_alternate": -0.12, "offset_same_hand": 0.05, "offset_same_finger": 0.3, "offset_other": 0.1, "offset_common_pair": -0.25,
}
LEANING = {**RHYTHM, "slip_fast_slope": 0.8, "slip_common_pair_ratio": 2.0, "slip_common_word_ratio": 2.5}


def phrase_with_slip(text, rhythm, slip_at):
	"""A PhraseRecord of `text` typed with one wrong key at `slip_at` (noticed at once and corrected), or none."""
	record = calibration.PhraseRecord(text, 100.0)
	rhythm.start_search()
	now = 101.2

	def press(char):
		record.events.append((now, "char", char))

	press(text[0])

	for index in range(1, len(text)):
		now += rhythm.next_gap(text[index - 1], text[index])

		if index == slip_at:
			wrong = search_behavior.NEIGHBORS.get(text[index], "x")[0]
			press(wrong)
			now += 0.6
			record.events.append((now, "back", ""))
			now += 0.2

		press(text[index])

	now += 0.4
	record.events.append((now, "enter", ""))
	record.final = text

	return record


def recorded(truth, count=140, seed=2, slip_share=0.5):
	rng = random.Random(seed)
	rhythm = human_model.TypingRhythm(RHYTHM, rng)
	records = []

	for n in range(count):
		text = calibration.PHRASES[n % len(calibration.PHRASES)]
		slip_at = None

		if rng.random() < slip_share:
			weights = human_model.slip_weights(text, truth)
			eligible = [i for i in range(1, len(text)) if text[i].isalpha() and text[i] in search_behavior.NEIGHBORS]
			slip_at = rng.choices(eligible, weights=[weights[i] for i in eligible])[0]

		records.append(phrase_with_slip(text, rhythm, slip_at))

	return records


class TestWords(unittest.TestCase):
	def test_the_word_at_a_position(self):
		self.assertEqual(human_model.word_at("best pizza near me", 6), "pizza")
		self.assertEqual(human_model.word_at("best pizza near me", 4), "")
		self.assertEqual(human_model.word_at("best pizza near me", 17), "me")

	def test_everyday_words(self):
		self.assertTrue(human_model.common_word("The"))
		self.assertTrue(human_model.common_word("near"))
		self.assertFalse(human_model.common_word("pharmacy"))


class TestWeights(unittest.TestCase):
	def test_a_person_with_no_leaning_slips_anywhere_equally(self):
		self.assertEqual(set(human_model.slip_weights("best pizza near me", {})), {1.0})

	def test_everyday_words_and_common_pairs_weigh_more_for_someone_who_leans_that_way(self):
		text = "the pharmacy hours"
		weights = human_model.slip_weights(text, LEANING)

		self.assertGreater(weights[2], weights[8])        # "the" against "pharmacy"

	def test_quick_keys_weigh_more_than_slow_ones(self):
		text = "fj fr"
		weights = human_model.slip_weights(text, {**RHYTHM, "slip_fast_slope": 1.0})

		# "j" after "f" changes hands (quick for this typist); "r" after "f" is the same finger (slow).
		self.assertGreater(weights[1], weights[4])

	def test_a_slope_the_other_way_flips_it(self):
		text = "abc def"
		up = human_model.slip_weights(text, {**RHYTHM, "slip_fast_slope": 1.0})
		down = human_model.slip_weights(text, {**RHYTHM, "slip_fast_slope": -1.0})

		self.assertTrue(all((u - 1) * (d - 1) <= 1e-9 or u != d for u, d in zip(up, down)))
		self.assertNotEqual(up, down)


class TestTheTypoFollowsTheHabit(unittest.TestCase):
	def positions(self, weights, tries=3000):
		rng = random.Random(1)
		query = "the pharmacy hours"
		hit = {}

		for _ in range(tries):
			slipped = search_behavior.with_typo(query, 1.0, rng, 0.0, weights)
			changed = [i for i, (a, b) in enumerate(zip(query, slipped)) if a != b]

			if changed:
				hit[changed[0]] = hit.get(changed[0], 0) + 1

		return hit

	def test_without_weights_a_three_letter_word_is_left_alone(self):
		hit = self.positions(None)

		self.assertFalse([i for i in hit if i < 3])

	def test_with_weights_an_everyday_three_letter_word_can_slip(self):
		hit = self.positions(human_model.slip_weights("the pharmacy hours", LEANING))

		self.assertTrue([i for i in hit if i < 3])

	def test_the_slips_land_in_everyday_words_more_often_for_someone_who_does_that(self):
		leaning = self.positions(human_model.slip_weights("the pharmacy hours", {**LEANING, "slip_common_word_ratio": 3.0}))
		even = self.positions(None)
		share = lambda hit: sum(n for i, n in hit.items() if i >= 13) / sum(hit.values())    # in "hours", an everyday word? no: "hours" is not common

		self.assertGreater(sum(n for i, n in leaning.items() if i < 3) / sum(leaning.values()), sum(n for i, n in even.items() if i < 3) / sum(even.values()))
		self.assertIsNotNone(share)

	def test_a_wrong_sized_weight_list_is_ignored(self):
		self.assertEqual(self.positions([1.0, 2.0]), self.positions(None))


class TestMeasuring(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		cls.leaning = calibration.analyze_typing([recorded(LEANING)])["detail"]
		cls.even = calibration.analyze_typing([recorded({**RHYTHM}, seed=4)])["detail"]

	def test_a_typist_who_slips_in_everyday_words_is_measured_that_way(self):
		self.assertGreater(self.leaning["slip_common_word_ratio"], 1.25)

	def test_one_who_does_not_is_measured_near_even(self):
		self.assertLess(abs(self.even["slip_common_word_ratio"] - 1.0), 0.45)

	def test_the_difference_between_them_is_seen(self):
		self.assertGreater(self.leaning["slip_common_word_ratio"], self.even["slip_common_word_ratio"] + 0.2)

	def test_quick_keys_show_up_as_a_positive_slope_for_the_one_who_leans(self):
		self.assertGreater(self.leaning["slip_fast_slope"], self.even.get("slip_fast_slope", 0.0))

	def test_no_slips_means_no_habits_reported(self):
		detail = calibration.analyze_typing([recorded(RHYTHM, count=60, slip_share=0.0)])["detail"]

		self.assertNotIn("slip_common_word_ratio", detail)

	def test_the_figures_fit_the_profile_ranges(self):
		cleaned = behavior.clean_detail(self.leaning, behavior.TYPING_DETAIL_RANGES, "noticed_weights")

		for key in ("slip_common_word_ratio", "slip_common_pair_ratio", "slip_fast_slope"):
			self.assertIn(key, cleaned)


class TestTheKeyboardUsesIt(unittest.TestCase):
	def setUp(self):
		patcher = mock.patch.dict(os.environ, {"REWARDS_FEATURES": "typing"})
		patcher.start()
		self.addCleanup(patcher.stop)

	def keyboard(self, detail):
		profile = behavior.Behavior("mom", "recorded", 0.4, 0.4, 0.2, 0.13, typing_detail=detail)

		return mimic_typing.KeyboardUtils(mock.Mock(), profile, account="mom")

	def test_weights_come_back_once_the_habits_are_known_and_the_feature_is_on(self):
		self.assertEqual(len(self.keyboard(LEANING).slip_weights("hello there")), 11)

	def test_none_without_recorded_habits(self):
		self.assertIsNone(self.keyboard({}).slip_weights("hello there"))
		self.assertIsNone(self.keyboard(RHYTHM).slip_weights("hello there"))

	def test_none_with_the_feature_off(self):
		with mock.patch.dict(os.environ, {"REWARDS_FEATURES": ""}):
			self.assertIsNone(self.keyboard(LEANING).slip_weights("hello there"))


if __name__ == "__main__":
	unittest.main()
