"""A classifier trying to tell a person from the bot built out of that person's own numbers."""

import importlib.util
import math
import os
import random
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import calibration
import human_model
import indistinguishable
import mimic_typing

HAVE_NUMPY = importlib.util.find_spec("numpy") is not None

PERSON = {
	"log_gap_mu": math.log(0.15), "within_sigma": 0.34, "sigma_sd": 0.05, "tempo_sd": 0.14, "tempo_phi": 0.3, "gap_phi": 0.3,
	"offset_alternate": -0.1, "offset_same_hand": 0.05, "offset_same_finger": 0.3, "offset_other": 0.1, "offset_common_pair": -0.2,
	"day_sd": 0.0, "hold_mu": math.log(88), "hold_sigma": 0.25, "rollover_rate": 0.30,
}


def recorded_phrase(text, timeline, events):
	record = calibration.PhraseRecord(text, 0.0)
	record.events = [(when, "char", key) for when, key in timeline]
	held = {}

	for when, down, key in events:
		if down:
			held[key] = when
		else:
			record.holds.append((key, held[key], when))

	record.final = text

	return record


def person(count, seed, texts=calibration.PHRASES):
	"""Phrases typed by a person, with key holds, as the recording would hold them."""
	rng = random.Random(seed)
	rhythm = human_model.TypingRhythm(PERSON, rng, 0.0)
	records = []

	for n in range(count):
		text = texts[n % len(texts)]
		rhythm.start_search()
		now, timeline = 1.0, [(1.0, text[0])]

		for previous, char in zip(text, text[1:]):
			now += rhythm.next_gap(previous, char)
			timeline.append((now, char))

		records.append(recorded_phrase(text, timeline, mimic_typing.KeyboardUtils.key_events(timeline, PERSON, rng)))

	return records


def crude_bot(count, seed):
	"""The tell this whole effort is about: a flat random gap and a key that goes down and up at once."""
	rng = random.Random(seed)
	records = []

	for n in range(count):
		text = calibration.PHRASES[n % len(calibration.PHRASES)]
		now, timeline, events = 1.0, [], []

		for char in text:
			now += rng.uniform(0.08, 0.25)
			timeline.append((now, char))
			events += [(now, True, char), (now + 0.004, False, char)]

		records.append(recorded_phrase(text, timeline, events))

	return records


@unittest.skipUnless(HAVE_NUMPY, "numpy is not installed")
class TestTheDiscriminator(unittest.TestCase):
	def test_it_can_tell_a_crude_bot_from_a_person(self):
		real = indistinguishable.real_features(person(30, 1))
		bot = indistinguishable.real_features(crude_bot(120, 2))

		self.assertGreater(indistinguishable.auc(real, bot), 0.9)

	def test_two_samples_of_the_same_person_score_near_chance(self):
		a = indistinguishable.real_features(person(30, 1))
		b = indistinguishable.real_features(person(150, 5))

		self.assertLess(indistinguishable.auc(a, b), 0.68)

	def test_it_does_not_matter_which_set_is_called_real(self):
		a = indistinguishable.real_features(person(30, 1))
		b = indistinguishable.real_features(crude_bot(60, 2))

		self.assertAlmostEqual(indistinguishable.auc(a, b), indistinguishable.auc(b, a), delta=0.001)

	def test_the_true_process_against_itself_is_about_chance_not_inflated_by_overfitting(self):
		a = indistinguishable.real_features(person(30, 1))
		b = indistinguishable.real_features(person(150, 5))

		self.assertLess(indistinguishable.auc(a, b), 0.68)


@unittest.skipUnless(HAVE_NUMPY, "numpy is not installed")
class TestTheBotBuiltFromAPersonsNumbers(unittest.TestCase):
	def test_a_bot_built_from_the_numbers_measured_off_a_person_cannot_be_told_from_them(self):
		records = person(40, 3)
		measured = calibration.analyze_typing(records)["detail"]
		outcome = indistinguishable.tell_apart(records, measured, random.Random(1))

		self.assertIsNotNone(outcome)
		self.assertLess(outcome["auc"], 0.66, indistinguishable.verdict(outcome["auc"]))

	def test_a_bot_with_the_spread_left_out_is_easier_to_tell_apart(self):
		records = person(40, 3)
		measured = calibration.analyze_typing(records)["detail"]
		flat = {**measured, "tempo_sd": 0.0, "sigma_sd": 0.0, "within_sigma": 0.02, "gap_phi": 0.0}
		real = indistinguishable.tell_apart(records, measured, random.Random(1))["auc"]
		without = indistinguishable.tell_apart(records, flat, random.Random(1))["auc"]

		self.assertGreater(without, real + 0.1)

	def test_too_little_to_judge_says_so(self):
		records = person(6, 3)

		self.assertIsNone(indistinguishable.tell_apart(records, PERSON, random.Random(1)))

	def test_verdict_words(self):
		self.assertEqual(indistinguishable.verdict(0.5), "cannot be told apart")
		self.assertEqual(indistinguishable.verdict(0.99), "obviously a bot")


if __name__ == "__main__":
	unittest.main()


class TestTuningSlipWeights(unittest.TestCase):
	TEXTS = ["the best way to learn about the weather", "how to make bread at home with flour", "what is the capital of australia today"]

	def test_no_ratios_means_nothing_is_added(self):
		detail = {"slip_per_char": 0.03}

		self.assertEqual(indistinguishable.tune_slip_weights(self.TEXTS, detail), detail)

	def test_the_weight_moves_so_the_slips_made_match_the_ratio_measured(self):
		detail = {"slip_per_char": 0.05, "slip_common_word_ratio": 0.5, "slip_common_pair_ratio": 1.0}
		tuned = indistinguishable.tune_slip_weights(self.TEXTS, detail, rounds=4, draws=60)

		self.assertIn("slip_word_weight", tuned)
		self.assertGreater(tuned["slip_word_weight"], 0.1)
		self.assertLessEqual(tuned["slip_word_weight"], 8.0)
