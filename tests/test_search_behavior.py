"""Tests for the pacing and typo behavior of the required searches.

	python -m unittest discover -s tests
"""

import os
import random
import sys
import unittest

SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src")
sys.path.insert(0, SRC)

import search_behavior as sb


class TestWithTypo(unittest.TestCase):
	QUERY = "best beauty haircare and fragrance products"

	def test_a_rate_of_zero_never_changes_anything(self):
		rng = random.Random(1)

		for _ in range(200):
			self.assertEqual(sb.with_typo(self.QUERY, rate=0, rng=rng), self.QUERY)

	def test_a_slip_keeps_the_length_and_touches_one_place(self):
		rng = random.Random(2)
		changed = 0

		for _ in range(500):
			out = sb.with_typo(self.QUERY, rate=1, rng=rng)
			self.assertEqual(len(out), len(self.QUERY))

			differing = [i for i, (a, b) in enumerate(zip(out, self.QUERY)) if a != b]
			# A substitution changes one character, a swap two neighbors, and a
			# swap of two equal letters none.
			self.assertLessEqual(len(differing), 2)

			if differing:
				changed += 1
				if len(differing) == 2:
					self.assertEqual(differing[1], differing[0] + 1)

		self.assertGreater(changed, 400)

	def test_the_first_letter_of_a_word_is_never_the_slip(self):
		rng = random.Random(3)
		starts, i = set(), 0

		for word in self.QUERY.split(" "):
			starts.add(i)
			i += len(word) + 1

		for _ in range(500):
			out = sb.with_typo(self.QUERY, rate=1, rng=rng)

			for j in starts:
				self.assertEqual(out[j], self.QUERY[j])

	def test_short_words_are_left_alone(self):
		rng = random.Random(4)

		for _ in range(200):
			self.assertEqual(sb.with_typo("how to do it", rate=1, rng=rng), "how to do it")

	def test_the_default_rate_is_about_eight_percent(self):
		rng = random.Random(5)
		trials = 20000
		changed = sum(1 for _ in range(trials) if sb.with_typo(self.QUERY, rng=rng) != self.QUERY)

		# Some slips are a swap of equal letters and change nothing, so the
		# observed rate sits a little under the configured one.
		self.assertGreater(changed / trials, 0.05)
		self.assertLess(changed / trials, 0.09)


class TestCoffeeBreaks(unittest.TestCase):
	def test_no_break_before_the_first_search(self):
		for seed in range(50):
			self.assertEqual(sb.CoffeeBreaks(random.Random(seed)).before_search(), 0.0)

	def test_breaks_come_after_four_to_fifteen_searches_and_stay_in_range(self):
		for seed in range(100):
			breaks = sb.CoffeeBreaks(random.Random(seed))
			gaps, since = [], 0

			for _ in range(120):
				pause = breaks.before_search()
				since += 1

				if pause:
					gaps.append(since - 1)
					since = 0
					self.assertTrue(15 <= pause <= 30 or 45 <= pause <= 90, pause)

			self.assertTrue(gaps)
			for gap in gaps:
				self.assertTrue(4 <= gap <= 15, gap)

	def test_most_breaks_are_the_short_kind(self):
		breaks = sb.CoffeeBreaks(random.Random(7))
		pauses = [p for p in (breaks.before_search() for _ in range(5000)) if p]
		short = sum(1 for p in pauses if p <= 30)

		self.assertGreater(short / len(pauses), 0.6)


class TestSearchesNeeded(unittest.TestCase):
	def test_rounds_up(self):
		self.assertEqual(sb.searches_needed(45, 5), 9)
		self.assertEqual(sb.searches_needed(46, 5), 10)

	def test_always_at_least_one(self):
		self.assertEqual(sb.searches_needed(0, 5), 1)
		self.assertEqual(sb.searches_needed(10, 0), 1)


if __name__ == "__main__":
	unittest.main()
