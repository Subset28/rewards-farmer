"""Searching in tangents: how long they run, what happens next, how long a person reads."""

import os
import random
import statistics
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import chains


class TestLengths(unittest.TestCase):
	def lengths(self, count=6000, seed=1):
		rng = random.Random(seed)

		return [chains.tangent_length(rng) for _ in range(count)]

	def test_most_tangents_are_short(self):
		lengths = self.lengths()

		self.assertGreater(sum(1 for n in lengths if n <= 3) / len(lengths), 0.55)

	def test_some_tangents_go_on(self):
		lengths = self.lengths()

		self.assertGreater(sum(1 for n in lengths if n >= 8) / len(lengths), 0.02)
		self.assertEqual(max(lengths), 12)

	def test_never_longer_than_what_is_left(self):
		rng = random.Random(2)

		self.assertTrue(all(chains.tangent_length(rng, longest=3) <= 3 for _ in range(500)))

	def test_a_sitting_is_split_into_tangents_that_add_up_exactly(self):
		rng = random.Random(3)

		for budget in (1, 2, 5, 8, 13, 31):
			plan = chains.plan_sitting(budget, rng)

			self.assertEqual(sum(plan), budget)
			self.assertTrue(all(n >= 1 for n in plan))

	def test_sittings_are_not_always_cut_the_same_way(self):
		plans = {tuple(chains.plan_sitting(10, random.Random(seed))) for seed in range(40)}

		self.assertGreater(len(plans), 10)


class TestReading(unittest.TestCase):
	def times(self, fn, count=4000):
		rng = random.Random(4)

		return [fn(rng) for _ in range(count)]

	def test_reading_clusters_near_the_middle_with_a_long_tail(self):
		times = self.times(chains.read_time)

		self.assertAlmostEqual(statistics.median(times), chains.READ_MEDIAN, delta=3)
		self.assertGreater(max(times), 2.5 * chains.READ_MEDIAN)
		self.assertGreaterEqual(min(times), chains.READ_LOW)
		self.assertLessEqual(max(times), chains.READ_HIGH)

	def test_a_slower_person_reads_longer(self):
		rng = random.Random(5)
		slow = statistics.median(chains.read_time(rng, 1.5) for _ in range(2000))

		self.assertGreater(slow, 1.3 * chains.READ_MEDIAN)

	def test_a_result_is_opened_now_and_then(self):
		rng = random.Random(6)
		opened = sum(chains.opens_a_result(rng) for _ in range(4000)) / 4000

		self.assertAlmostEqual(opened, chains.OPEN_RESULT_CHANCE, delta=0.03)

	def test_a_break_between_tangents_is_longer_than_a_look_at_a_page(self):
		self.assertGreater(statistics.median(self.times(chains.break_time)), statistics.median(self.times(chains.read_time)))


class TestWhatToDoNext(unittest.TestCase):
	OPTIONS = ["vitamin c foods", "vitamin c deficiency symptoms", "how much vitamin c per day", "scurvy"]

	def steps(self, options, asked=None, current="why do i need vitamin c", count=3000, seed=7):
		rng = random.Random(seed)

		return [chains.next_step(options, set(asked or ()), current, rng) for _ in range(count)]

	def test_it_clicks_one_of_the_pages_own_suggestions_often(self):
		steps = self.steps(self.OPTIONS)
		clicks = [s for s in steps if s[0] == "click"]

		self.assertAlmostEqual(len(clicks) / len(steps), chains.CLICK_RELATED_CHANCE, delta=0.04)
		self.assertTrue(all(text in self.OPTIONS for _, text in clicks))

	def test_it_types_a_follow_up_taken_from_them_about_as_often(self):
		steps = self.steps(self.OPTIONS)
		typed = [s for s in steps if s[0] == "type"]

		self.assertAlmostEqual(len(typed) / len(steps), chains.RETYPE_CHANCE, delta=0.04)
		self.assertTrue(all(any(text.lower() in opt or opt in text.lower() for opt in self.OPTIONS) for _, text in typed))

	def test_the_rest_of_the_time_the_thread_ends(self):
		steps = self.steps(self.OPTIONS)

		self.assertAlmostEqual(sum(1 for s in steps if s[0] == "stop") / len(steps), 1 - chains.CLICK_RELATED_CHANCE - chains.RETYPE_CHANCE, delta=0.04)

	def test_nothing_already_asked_is_followed(self):
		asked = {"vitamin c foods", "scurvy"}
		steps = self.steps(self.OPTIONS, asked=asked)
		followed = {text.lower() for kind, text in steps if text}

		self.assertFalse(followed & asked)

	def test_the_same_question_again_is_not_a_follow_up(self):
		steps = self.steps(["Why do I need vitamin C?", "why do i need vitamin c"], current="why do i need vitamin c")

		self.assertTrue(all(kind == "stop" or text is None for kind, text in steps))

	def test_with_no_suggestions_it_mostly_stops(self):
		steps = self.steps([])

		self.assertGreater(sum(1 for s in steps if s[0] == "stop") / len(steps), 0.6)
		self.assertFalse([s for s in steps if s[0] == "click"])

	def test_duplicates_and_nonsense_are_dropped(self):
		usable = chains.usable(["Scurvy", "scurvy", "", "  ", "x", "a very long suggestion that goes on and on far beyond what anyone types", "vitamin c foods"], set(), "vitamin c")

		self.assertEqual(usable, ["Scurvy", "vitamin c foods"])

	def test_rewording_never_changes_what_was_meant(self):
		rng = random.Random(8)

		for _ in range(200):
			reworded = chains.reword("how much vitamin c do adults need per day", "vitamin c", rng)

			self.assertTrue(reworded.startswith("how much vitamin c"))


class TestWhereAThreadStarts(unittest.TestCase):
	def test_the_accounts_own_interests_come_first_more_often(self):
		rng = random.Random(9)
		interests = [f"interest {n}" for n in range(6)]
		trending = [f"news {n}" for n in range(20)]
		firsts = [chains.starting_points(list(interests), list(trending), 1, rng)[0] for _ in range(2000)]

		share = sum(1 for f in firsts if f.startswith("interest")) / len(firsts)

		self.assertAlmostEqual(share, chains.INTEREST_SHARE, delta=0.05)

	def test_with_no_interests_it_is_the_news(self):
		starts = chains.starting_points([], ["a", "b", "c"], 2, random.Random(1))

		self.assertEqual(len(starts), 2)
		self.assertTrue(set(starts) <= {"a", "b", "c"})

	def test_with_nothing_at_all_there_is_nothing(self):
		self.assertEqual(chains.starting_points([], [], 3, random.Random(1)), [])

	def test_no_thread_starts_twice(self):
		starts = chains.starting_points(["x", "y"], ["x", "z"], 4, random.Random(2))

		self.assertEqual(len(starts), len(set(starts)))


if __name__ == "__main__":
	unittest.main()
