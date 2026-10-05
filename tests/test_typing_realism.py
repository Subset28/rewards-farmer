"""Typing realism: corrected slips, word-boundary pauses, a thinking pause.

A fake ActionChains records every key and pause, and rebuilds the text in the
box (Backspace removes a character, Enter submits), so what is checked is what
a search box would actually hold when Enter is pressed.
"""

import os
import random
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import behavior
import mimic_typing
import search_behavior
from selenium.webdriver.common.keys import Keys

QUERY = "best hiking trails near me"


class FakeActions:
	last = None

	def __init__(self, driver, duration=0):
		self.events = []
		FakeActions.last = self

	def send_keys(self, key):
		self.events.append(("key", key))
		return self

	def pause(self, seconds):
		self.events.append(("pause", seconds))
		return self

	def perform(self):
		pass

	def duration(self):
		return sum(e[1] for e in self.events if e[0] == "pause")

	def keys(self):
		return [e[1] for e in self.events if e[0] == "key"]

	def result(self):
		"""(text in the box at the moment of Enter, whether Enter came last)."""
		text = []
		keys = self.keys()

		for key in keys:
			if key == Keys.ENTER:
				return "".join(text), key is keys[-1]
			elif key == Keys.BACKSPACE:
				text.pop()
			else:
				text.append(key)

		return "".join(text), False


def type_query(query, typed=None, seed=0, profile=None):
	keyboard = mimic_typing.KeyboardUtils(mock.Mock(), profile)

	with mock.patch.object(mimic_typing, "ActionChains", FakeActions):
		keyboard.send_keys(f"{typed or query}{Keys.ENTER}", intended=query, rng=random.Random(seed))

	return FakeActions.last


class TestCorrection(unittest.TestCase):
	def slipped(self, seed):
		return search_behavior.with_typo(QUERY, rate=1, rng=random.Random(seed))

	def test_a_corrected_slip_leaves_the_intended_text(self):
		corrected = 0

		for seed in range(200):
			typed = self.slipped(seed)
			fake = type_query(QUERY, typed, seed)
			text, enter_last = fake.result()

			self.assertTrue(enter_last)

			if Keys.BACKSPACE in fake.keys():
				corrected += 1
				self.assertEqual(text, QUERY)
			else:
				self.assertEqual(text, typed)

		# About 70% of slips are fixed.
		self.assertTrue(110 < corrected < 170, corrected)

	def test_enter_is_never_pressed_mid_correction(self):
		for seed in range(200):
			keys = type_query(QUERY, self.slipped(seed), seed).keys()

			self.assertEqual(keys.count(Keys.ENTER), 1)
			self.assertEqual(keys[-1], Keys.ENTER)

	def test_backspace_count_matches_the_retyped_keys(self):
		for seed in range(200):
			keys = type_query(QUERY, self.slipped(seed), seed).keys()
			backs = keys.count(Keys.BACKSPACE)

			if backs:
				self.assertLessEqual(backs, 4)

	def test_no_slip_means_no_backspace(self):
		for seed in range(50):
			fake = type_query(QUERY, None, seed)

			self.assertNotIn(Keys.BACKSPACE, fake.keys())
			self.assertEqual(fake.result()[0], QUERY)

	def test_without_intended_text_nothing_is_corrected(self):
		keyboard = mimic_typing.KeyboardUtils(mock.Mock())

		with mock.patch.object(mimic_typing, "ActionChains", FakeActions):
			keyboard.send_keys("abcd", rng=random.Random(1))

		self.assertEqual(FakeActions.last.keys(), list("abcd"))


class TestTiming(unittest.TestCase):
	def test_seeded_runs_are_identical(self):
		a = type_query(QUERY, None, 7).events
		b = type_query(QUERY, None, 7).events

		self.assertEqual(a, b)

	def test_a_thinking_pause_comes_before_the_first_key(self):
		fake = type_query(QUERY, None, 3)

		self.assertEqual(fake.events[0][0], "pause")
		self.assertGreater(fake.events[0][1], 0)

	def test_word_boundaries_pause_longer_on_average(self):
		after_space, after_letter = [], []

		for seed in range(100):
			events = type_query(QUERY, None, seed).events

			for i, e in enumerate(events[:-1]):
				if e[0] == "key" and e[1] != Keys.ENTER:
					(after_space if e[1] == " " else after_letter).append(events[i + 1][1])

		self.assertGreater(sum(after_space) / len(after_space), 1.4 * sum(after_letter) / len(after_letter))

	def test_the_total_stays_near_the_old_budget(self):
		query = "how to grow tomatoes in small pots"[:30]
		old = []
		new = []

		for seed in range(300):
			rng = random.Random(seed)
			weights = [mimic_typing.FIRST_INTERVAL_PROBABILITY, mimic_typing.SECOND_INTERVAL_PROBABILITY,
				mimic_typing.THIRD_INTERVAL_PROBABILITY]
			buckets = [mimic_typing.FIRST_INTERVAL, mimic_typing.SECOND_INTERVAL, mimic_typing.THIRD_INTERVAL]
			old.append(sum(rng.uniform(*rng.choices(buckets, weights=weights)[0]) for _ in range(len(query) + 1)))

			typed = search_behavior.with_typo(query, rng=random.Random(seed))
			new.append(type_query(query, typed, seed).duration())

		self.assertLess(sum(new) / len(new), 1.35 * sum(old) / len(old))
		self.assertGreater(sum(new) / len(new), sum(old) / len(old))

	def test_pauses_scale_with_the_accounts_profile(self):
		fast = behavior.Behavior("x", "recorded", 0.9, 0.1, 0.4, 0.15)
		slow = behavior.Behavior("x", "recorded", 0.0, 0.2, 0.4, 0.15)

		fast_total = sum(type_query(QUERY, None, s, fast).duration() for s in range(50))
		slow_total = sum(type_query(QUERY, None, s, slow).duration() for s in range(50))

		self.assertGreater(slow_total, 2 * fast_total)


if __name__ == "__main__":
	unittest.main()
