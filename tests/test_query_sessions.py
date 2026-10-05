"""Tests for grouping queries into topical sessions.

	python -m pytest tests/test_query_sessions.py
"""

import os
import random
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import query_history
import query_sources

TRENDS = [f"gadget{i} widget{i}" for i in range(30)]
WIKI = [f"wiki{i} topic{i}" for i in range(30)]


def fake_suggestions(term):
	return [term, f"{term} near me", f"{term} today", f"{term} 2026", f"{term} news", f"{term} price"]


def no_suggestions(term):
	return []


def is_refined(query):
	"""A follow-up, in these tests, is anything that is not a bare feed item."""
	return query not in TRENDS and query not in WIKI


class SessionsTestCase(unittest.TestCase):
	suggest = staticmethod(fake_suggestions)

	def setUp(self):
		for patcher in (
			mock.patch.object(query_sources, "trending_queries", lambda geo="US": list(TRENDS)),
			mock.patch.object(query_sources, "wikipedia_topics", lambda days_ago=1: list(WIKI)),
			mock.patch.object(query_sources, "suggestions", self.suggest),
		):
			patcher.start()
			self.addCleanup(patcher.stop)

	def share(self, draws=200, count=10):
		refined = total = 0

		for i in range(draws):
			batch = query_sources.related_queries(count, rng=random.Random(i))
			refined += sum(is_refined(q) for q in batch)
			total += len(batch)

		return refined / total


class TestRefinementShare(SessionsTestCase):
	def test_about_half_the_queries_are_refinements(self):
		self.assertTrue(0.40 <= self.share() <= 0.55, self.share())


class TestOfflineRefinementShare(SessionsTestCase):
	suggest = staticmethod(no_suggestions)

	def test_templated_follow_ups_give_the_same_share(self):
		self.assertTrue(0.40 <= self.share() <= 0.55, self.share())

	def test_the_offline_path_still_returns_follow_ups(self):
		batch = query_sources.related_queries(10, rng=random.Random(3))

		self.assertEqual(len(batch), 10)
		self.assertTrue(any(is_refined(q) for q in batch))


class TestSessionStructure(SessionsTestCase):
	def test_follow_ups_contain_the_words_of_their_seed(self):
		for i in range(50):
			batch = query_sources.related_queries(10, rng=random.Random(i))
			topic = None

			for query in batch:
				if not is_refined(query):
					topic = query
				else:
					self.assertIsNotNone(topic)
					self.assertTrue(set(topic.split()) <= set(query.split()), (topic, query))

	def test_sessions_are_one_to_four_queries(self):
		for i in range(50):
			batch = query_sources.related_queries(12, rng=random.Random(i))
			run = 0

			for query in batch:
				run = run + 1 if is_refined(query) else 1

				self.assertLessEqual(run, 4)

	def test_a_batch_is_a_few_sessions_not_unrelated_topics(self):
		batch = query_sources.related_queries(10, rng=random.Random(5))
		seeds = [q for q in batch if not is_refined(q)]

		self.assertLess(len(seeds), 10)

	def test_the_same_rng_seed_gives_the_same_batch(self):
		first = query_sources.related_queries(8, rng=random.Random(9))
		second = query_sources.related_queries(8, rng=random.Random(9))

		self.assertEqual(first, second)


class TestContracts(SessionsTestCase):
	def test_count_and_exclude_hold(self):
		exclude = {"gadget0 widget0 news", TRENDS[1], TRENDS[2]}

		for count in (1, 2, 5, 9):
			batch = query_sources.related_queries(count, exclude=exclude, rng=random.Random(count))

			self.assertEqual(len(batch), count)
			self.assertFalse({query_history.normalize(q) for q in batch} & {query_history.normalize(q) for q in exclude})

	def test_exclude_is_compared_normalised(self):
		batch = query_sources.related_queries(30, exclude={"  GADGET3   Widget3 "}, rng=random.Random(1))

		self.assertNotIn("gadget3 widget3", batch)

	def test_no_duplicates_across_batches(self):
		seen, rng = set(), random.Random(4)

		for size in (3, 5, 4, 6):
			batch = query_sources.related_queries(size, exclude=seen, rng=rng)

			self.assertEqual(len(batch), len(set(batch)))
			self.assertFalse(seen & set(batch))
			seen |= set(batch)

	def test_lengths_stay_short_and_nothing_reads_as_an_instruction(self):
		for i in range(30):
			for query in query_sources.related_queries(10, rng=random.Random(i)):
				self.assertLessEqual(len(query.split()), 6, query)
				self.assertGreater(len(query), 2)
				self.assertNotIn(query.split()[0], {"search", "bing", "find", "use", "learn", "discover", "explore"})

	def test_a_seed_argument_is_still_used(self):
		batch = query_sources.related_queries(5, seed="tea", rng=random.Random(2))

		self.assertEqual(len(batch), 5)


class TestTemplates(unittest.TestCase):
	def test_a_person_sized_seed_gets_no_price_or_near_me(self):
		for template in query_sources.follow_up_templates("taylor swift"):
			self.assertNotIn("price", template)
			self.assertNotIn("near me", template)

	def test_an_event_gets_event_follow_ups(self):
		self.assertIn("{x} score", query_sources.follow_up_templates("lakers vs celtics"))

	def test_a_single_word_gets_the_general_list(self):
		self.assertIn("{x} near me", query_sources.follow_up_templates("pizza"))

	def test_the_choice_is_deterministic_under_a_seeded_rng(self):
		a = query_sources.templated_follow_ups("pizza", random.Random(7))
		b = query_sources.templated_follow_ups("pizza", random.Random(7))

		self.assertEqual(a, b)
		self.assertTrue(all("pizza" in q for q in a))


if __name__ == "__main__":
	unittest.main()
