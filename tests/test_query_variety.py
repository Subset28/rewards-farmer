"""Tests for not repeating searches, and for how queries are drawn.

	python -m unittest discover -s tests
"""

import json
import os
import random
import sys
import tempfile
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import queries
import query_history
import query_sources
import rewards_tasks

TRENDS = [f"trend {i}" for i in range(30)]
WIKI = [f"wiki topic {i}" for i in range(30)]


def fake_suggestions(term):
	return [term, f"{term} near me", f"{term} today", f"{term} 2026", f"{term} news", f"{term} price"]


class SourcesTestCase(unittest.TestCase):
	def setUp(self):
		for patcher in (
			mock.patch.object(query_sources, "trending_queries", lambda geo="US": list(TRENDS)),
			mock.patch.object(query_sources, "wikipedia_topics", lambda days_ago=1: list(WIKI)),
			mock.patch.object(query_sources, "suggestions", fake_suggestions),
		):
			patcher.start()
			self.addCleanup(patcher.stop)


class TestRelatedQueries(SourcesTestCase):
	def test_batches_after_the_first_do_not_repeat_it(self):
		# The bug: every batch was the top of the feed, so batch 2 repeated batch 1.
		seen, rng = set(), random.Random(1)

		for size in (3, 5, 4, 6, 3):
			batch = query_sources.related_queries(size, exclude=seen, rng=rng)

			self.assertEqual(len(batch), size)
			self.assertFalse(seen & set(batch))
			seen |= set(batch)

		self.assertEqual(len(seen), 21)

	def test_it_does_not_take_the_top_of_the_feed_in_order(self):
		batch = query_sources.related_queries(5, rng=random.Random(2))

		self.assertNotEqual(batch, TRENDS[:5])

	def test_different_runs_get_different_queries(self):
		batches = {tuple(query_sources.related_queries(5, rng=random.Random(seed))) for seed in range(20)}

		self.assertGreater(len(batches), 15)

	def test_nothing_in_exclude_comes_back_whatever_its_case_or_spacing(self):
		exclude = {"TREND 0", "  trend   1 "}

		for seed in range(30):
			batch = query_sources.related_queries(10, exclude=exclude, rng=random.Random(seed))

			self.assertNotIn("trend 0", [q.lower() for q in batch])
			self.assertNotIn("trend 1", [q.lower() for q in batch])

	def test_a_batch_never_contains_the_same_query_twice(self):
		for seed in range(50):
			batch = query_sources.related_queries(12, rng=random.Random(seed))

			self.assertEqual(len(batch), len(set(q.lower() for q in batch)))

	def test_a_spent_feed_is_topped_up_from_bing_suggestions(self):
		with mock.patch.object(query_sources, "wikipedia_topics", lambda days_ago=1: []):
			batch = query_sources.related_queries(40, exclude=set(), rng=random.Random(3))

		self.assertGreater(len(batch), 30)
		self.assertTrue(any(q.endswith(("near me", "today", "2026", "news", "price")) for q in batch))

	def test_it_stops_short_rather_than_repeating(self):
		with mock.patch.object(query_sources, "trending_queries", lambda geo="US": ["only one"]), \
			mock.patch.object(query_sources, "wikipedia_topics", lambda days_ago=1: []), \
			mock.patch.object(query_sources, "suggestions", lambda term: []):
			batch = query_sources.related_queries(5, rng=random.Random(4))

		self.assertEqual(batch, ["only one"])

	def test_some_queries_follow_up_on_the_one_before(self):
		follow_ups = 0

		for seed in range(200):
			batch = query_sources.related_queries(4, rng=random.Random(seed))

			for before, after in zip(batch, batch[1:]):
				follow_ups += after.startswith(before + " ")

		self.assertGreater(follow_ups, 60)
		self.assertLess(follow_ups, 400)

	def test_a_follow_up_is_never_the_query_it_follows(self):
		for seed in range(100):
			batch = query_sources.related_queries(6, rng=random.Random(seed))

			for before, after in zip(batch, batch[1:]):
				self.assertNotEqual(before.lower(), after.lower())

	def test_an_unreachable_feed_gives_nothing_so_the_caller_can_fall_back(self):
		with mock.patch.object(query_sources, "trending_queries", lambda geo="US": []), \
			mock.patch.object(query_sources, "wikipedia_topics", lambda days_ago=1: []), \
			mock.patch.object(query_sources, "suggestions", lambda term: []):
			self.assertEqual(query_sources.related_queries(5), [])


class TestWikipediaTopics(unittest.TestCase):
	def topics(self, titles):
		body = json.dumps({"mostread": {"articles": [{"titles": {"normalized": t}} for t in titles]}})

		with mock.patch.object(query_sources, "_fetch", return_value=body):
			return query_sources.wikipedia_topics()

	def test_disambiguation_tags_are_dropped(self):
		self.assertEqual(
			self.topics(["Michael McDonald (musician)", "East of Eden (novel)", "Verity (novel)", "Bella Ramsey"]),
			["michael mcdonald", "east of eden", "verity", "bella ramsey"],
		)

	def test_a_tag_in_the_middle_is_left_alone(self):
		self.assertEqual(self.topics(["Spider-Man (2002 film) soundtrack"]), ["spider-man (2002 film) soundtrack"])

	def test_list_and_chrome_pages_are_skipped(self):
		self.assertEqual(
			self.topics(["Main Page", "Deaths in 2026", "List of countries", "Lists of films", "Index of articles", "Outline of physics", "Timeline of Rome", "Wikipedia:About", "Portal:Current events", "Wolves"]),
			["wolves"],
		)

	def test_a_title_that_is_only_a_tag_is_skipped(self):
		self.assertEqual(self.topics(["(disambiguation)", "Real Topic"]), ["real topic"])

	def test_an_unreachable_feed_is_empty(self):
		with mock.patch.object(query_sources, "_fetch", return_value=None):
			self.assertEqual(query_sources.wikipedia_topics(), [])


class HistoryTestCase(unittest.TestCase):
	def setUp(self):
		directory = tempfile.TemporaryDirectory()
		self.addCleanup(directory.cleanup)
		patcher = mock.patch.object(query_history, "HISTORY_FILE", os.path.join(directory.name, "query_history.jsonl"))
		patcher.start()
		self.addCleanup(patcher.stop)


class TestQueryHistory(HistoryTestCase):
	def test_a_recorded_query_is_remembered(self):
		query_history.record("default", "Medicare Advantage")

		self.assertEqual(query_history.recent("default"), {"medicare advantage"})

	def test_each_account_has_its_own_history(self):
		query_history.record("default", "one")
		query_history.record("second", "two")

		self.assertEqual(query_history.recent("default"), {"one"})
		self.assertEqual(query_history.recent("second"), {"two"})

	def test_an_old_search_is_forgotten(self):
		now = 100 * 86400
		query_history.record("default", "old", now=now - 40 * 86400)
		query_history.record("default", "new", now=now - 2 * 86400)

		self.assertEqual(query_history.recent("default", now=now), {"new"})

	def test_no_account_means_default(self):
		query_history.record(None, "x")

		self.assertEqual(query_history.recent("default"), {"x"})

	def test_a_missing_file_is_an_empty_history(self):
		self.assertEqual(query_history.recent("default"), set())

	def test_damaged_lines_are_skipped_not_fatal(self):
		query_history.record("default", "good")

		with open(query_history.HISTORY_FILE, "a", encoding="utf-8") as handle:
			handle.write("{not json\n[1,2]\n{\"q\": \"no time\"}\n\n")

		self.assertEqual(query_history.recent("default"), {"good"})

	def test_an_unwritable_location_does_not_raise(self):
		with mock.patch.object(query_history, "HISTORY_FILE", os.path.join(os.devnull, "nope", "h.jsonl")):
			query_history.record("default", "x")
			self.assertEqual(query_history.recent("default"), set())

	def test_the_file_is_trimmed_so_it_cannot_grow_forever(self):
		with mock.patch.object(query_history, "MAX_LINES", 20):
			for i in range(60):
				query_history.record("default", f"q{i}")

			with open(query_history.HISTORY_FILE, encoding="utf-8") as handle:
				self.assertLessEqual(len(handle.readlines()), 20)

		self.assertIn("q59", query_history.recent("default"))


class TestQueriesUsesHistory(SourcesTestCase, HistoryTestCase):
	def setUp(self):
		SourcesTestCase.setUp(self)
		HistoryTestCase.setUp(self)
		patcher = mock.patch.object(queries, "selected_source", return_value=queries.TRENDS)
		patcher.start()
		self.addCleanup(patcher.stop)

	def test_what_the_account_searched_is_not_offered_again(self):
		for q in TRENDS[:25] + WIKI[:25]:
			query_history.record("default", q)

		for _ in range(20):
			for q in queries.related_queries(6, account="default"):
				self.assertNotIn(q.lower(), query_history.recent("default"))

	def test_another_account_is_not_held_to_this_ones_history(self):
		for q in TRENDS + WIKI:
			query_history.record("default", q)

		self.assertEqual(len(queries.related_queries(5, account="second")), 5)

	def test_with_nothing_fresh_it_falls_back_to_the_wordlist(self):
		with mock.patch.object(query_sources, "trending_queries", lambda geo="US": []), \
			mock.patch.object(query_sources, "wikipedia_topics", lambda days_ago=1: []), \
			mock.patch.object(query_sources, "suggestions", lambda term: []), \
			mock.patch.object(query_sources, "wordlist_queries", lambda n: ["apple", "pear", "plum", "fig"]), \
			self.assertLogs(queries.logger, level="WARNING"):
			self.assertEqual(queries.related_queries(2, account="default"), ["apple", "pear"])

	def test_the_wordlist_fallback_skips_what_was_searched(self):
		query_history.record("default", "apple")

		with mock.patch.object(query_sources, "trending_queries", lambda geo="US": []), \
			mock.patch.object(query_sources, "wikipedia_topics", lambda days_ago=1: []), \
			mock.patch.object(query_sources, "suggestions", lambda term: []), \
			mock.patch.object(query_sources, "wordlist_queries", lambda n: ["apple", "pear", "plum", "fig"]), \
			self.assertLogs(queries.logger, level="WARNING"):
			self.assertEqual(queries.related_queries(2, account="default"), ["pear", "plum"])


class TestConcreteCardQueriesAvoidRepeats(unittest.TestCase):
	STOCK = "Search on Bing for the latest price of a specific stock."

	def test_a_recent_pick_is_passed_over(self):
		avoid = {f"{t} stock price".lower() for t in ("MSFT", "AAPL", "GOOGL", "AMZN", "NVDA", "TSLA", "META")}

		for seed in range(50):
			self.assertEqual(query_sources.concrete_query(self.STOCK, 0, random.Random(seed), avoid=avoid), "NFLX stock price")

	def test_when_everything_was_used_it_still_gives_an_answer(self):
		avoid = {f"{t} stock price".lower() for t in ("MSFT", "AAPL", "GOOGL", "AMZN", "NVDA", "TSLA", "META", "NFLX")}

		self.assertTrue(query_sources.concrete_query(self.STOCK, 0, random.Random(1), avoid=avoid).endswith(" stock price"))

	def test_a_retry_still_differs_from_the_first_pick(self):
		for seed in range(20):
			first = query_sources.concrete_query(self.STOCK, 0, random.Random(seed), avoid={"msft stock price"})
			second = query_sources.concrete_query(self.STOCK, 1, random.Random(seed), avoid={"msft stock price"})

			self.assertNotEqual(first, second)


class Batch:
	"""Just enough of RewardsTaskUtils for run_search_batch's query handling."""

	run_search_batch = rewards_tasks.RewardsTaskUtils.run_search_batch
	RESULTS_TAB_RATE = 0
	RESULTS_SCROLL_RATE = 0
	account_name = "second"

	def __init__(self):
		self.typed = []
		self.driver = types.SimpleNamespace(get=lambda url: None)
		self.tab_utils = types.SimpleNamespace(ensure_focus=lambda: None)
		self.elements = types.SimpleNamespace(get_bing_search_bar=lambda: object())
		self.keyboard = types.SimpleNamespace(send_keys=lambda text, **kw: self.typed.append(text))

	def wait_for_element(self, getter, *a, **k):
		return getter()

	def browse_results(self):
		pass


@mock.patch.object(rewards_tasks.time, "sleep", lambda *_: None)
class TestSearchBatchRemembersAndAsksForThisAccount(HistoryTestCase):
	def test_every_search_is_recorded_for_the_account_and_logged(self):
		page = Batch()

		with mock.patch.object(rewards_tasks.queries, "related_queries", return_value=["alpha beta", "gamma delta"]) as ask, \
			mock.patch.object(rewards_tasks.search_behavior, "with_typo", side_effect=lambda q: q), \
			self.assertLogs(rewards_tasks.logger, level="INFO") as logs:
			page.run_search_batch(2)

		ask.assert_called_once_with(2, account="second")
		self.assertEqual(query_history.recent("second"), {"alpha beta", "gamma delta"})
		self.assertEqual(query_history.recent("default"), set())
		self.assertTrue(any("Search 1/2: 'alpha beta'" in line for line in logs.output))

	def test_the_clean_query_is_remembered_not_the_one_with_a_typo(self):
		page = Batch()

		with mock.patch.object(rewards_tasks.queries, "related_queries", return_value=["haircare products"]), \
			mock.patch.object(rewards_tasks.search_behavior, "with_typo", side_effect=lambda q: "hairacre products"):
			page.run_search_batch(1)

		self.assertEqual(query_history.recent("second"), {"haircare products"})
		self.assertIn("hairacre products", page.typed[0])


if __name__ == "__main__":
	unittest.main()
