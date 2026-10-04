"""Tests for search queries from OpenRouter's free models.

	python -m unittest discover -s tests
"""

import email.message
import io
import json
import logging
import os
import sys
import tempfile
import time
import unittest
import urllib.error
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import openrouter_queries as o
import queries
import query_sources

KEY = "sk-or-v1-SECRETSECRETSECRET"

REPLY = "\n".join(f"query number {i} about hiking" for i in range(30))


class Reply:
	def __init__(self, text):
		self.body = json.dumps({"choices": [{"message": {"content": text}}]}).encode("utf-8")

	def __enter__(self):
		return self

	def __exit__(self, *exc):
		return False

	def read(self):
		return self.body


def http_error(code, retry_after=None):
	headers = email.message.Message()

	if retry_after is not None:
		headers["Retry-After"] = str(retry_after)

	return urllib.error.HTTPError("https://openrouter.ai/api/v1/chat/completions", code, "err", headers, io.BytesIO(b""))


class OpenRouterTestCase(unittest.TestCase):
	def setUp(self):
		directory = tempfile.TemporaryDirectory()
		self.addCleanup(directory.cleanup)
		self.directory = directory.name

		for patcher in (
			mock.patch.object(o, "USAGE_FILE", os.path.join(self.directory, "usage.json")),
			mock.patch.object(o, "POOL_FILE", os.path.join(self.directory, "pool.json")),
			mock.patch.object(o, "INTERESTS_DIR", os.path.join(self.directory, "interests")),
			mock.patch.object(o.time, "sleep", lambda *_: None),
			mock.patch.dict(os.environ, {"OPENROUTER_API_KEY": KEY}),
		):
			patcher.start()
			self.addCleanup(patcher.stop)

		o._last_call = 0.0
		os.environ.pop("OPENROUTER_DAILY_LIMIT", None)
		os.environ.pop("OPENROUTER_MODEL", None)

	def ask(self, urlopen_result, **kwargs):
		"""related_queries with the network replaced; returns (queries, calls)."""
		with mock.patch.object(o.urllib.request, "urlopen", side_effect=urlopen_result if isinstance(urlopen_result, list) else [urlopen_result] * 50) as net:
			return o.related_queries(kwargs.pop("count", 5), **kwargs), net


class TestParse(unittest.TestCase):
	def test_a_messy_reply_becomes_clean_queries(self):
		reply = "\n".join([
			"Here are 12 search queries:", "1. best hiking trails near me", "- how to season a cast iron pan",
			"* \"used honda civic 2019 price\"", "sure, let's go", "cheap flights to lisbon in march!",
			"why is my sourdough not rising?", "https://example.com/page", "how to season a cast iron pan", "a b",
			"this one is far too long to be a thing a person would ever type into a box at all today ok",
			"iphone 16 vs pixel 9", "queries:",
		])

		self.assertEqual(o.parse_queries(reply), [
			"best hiking trails near me", "how to season a cast iron pan", "used honda civic 2019 price",
			"cheap flights to lisbon in march", "why is my sourdough not rising", "iphone 16 vs pixel 9",
		])

	def test_anything_to_avoid_is_dropped(self):
		self.assertEqual(o.parse_queries("one two three\nfour five six", avoid={"one two three"}), ["four five six"])

	def test_nothing_in_gives_nothing_out(self):
		self.assertEqual(o.parse_queries(""), [])
		self.assertEqual(o.parse_queries(None), [])

	def test_punctuation_and_case_are_normalized(self):
		self.assertEqual(o.parse_queries("What's the Weather Like (Today)?"), ["what s the weather like today"])


class TestBudget(OpenRouterTestCase):
	def test_a_request_is_counted(self):
		self.ask(Reply(REPLY))

		self.assertEqual(o.requests_today(), 1)

	def test_the_daily_limit_stops_requests(self):
		with mock.patch.dict(os.environ, {"OPENROUTER_DAILY_LIMIT": "2"}):
			for i in range(5):
				self.ask(Reply(REPLY), account=f"a{i}")

		self.assertEqual(o.requests_today(), 2)

	def test_with_no_budget_left_nothing_is_asked_and_nothing_comes_back(self):
		with mock.patch.dict(os.environ, {"OPENROUTER_DAILY_LIMIT": "0"}):
			queries_, net = self.ask(Reply(REPLY))

		self.assertEqual(queries_, [])
		net.assert_not_called()

	def test_a_new_day_starts_the_count_again(self):
		self.ask(Reply(REPLY))

		with mock.patch.object(o, "_today", return_value="2999-01-01"):
			self.assertEqual(o.requests_today(), 0)

	def test_a_bad_limit_setting_falls_back_to_the_default(self):
		with mock.patch.dict(os.environ, {"OPENROUTER_DAILY_LIMIT": "lots"}):
			self.assertEqual(o.daily_limit(), o.DEFAULT_DAILY_LIMIT)

	def test_the_default_limit_stays_under_the_free_allowance(self):
		self.assertLess(o.DEFAULT_DAILY_LIMIT, 50)

	def test_failed_attempts_count_because_the_provider_counts_them(self):
		for failure in (http_error(500), urllib.error.URLError("down"), TimeoutError()):
			before = o.requests_today()
			self.ask(failure, account="x")
			self.assertEqual(o.requests_today(), before + 1)


class TestBatching(OpenRouterTestCase):
	def test_one_request_serves_many_calls(self):
		first, net = self.ask(Reply(REPLY), count=5)
		second, net2 = self.ask(Reply(REPLY), count=5)
		third, net3 = self.ask(Reply(REPLY), count=5)

		self.assertEqual(len(first), 5)
		self.assertEqual(net.call_count, 1)
		net2.assert_not_called()
		net3.assert_not_called()
		self.assertEqual(len({*first, *second, *third}), 15)
		self.assertEqual(o.requests_today(), 1)

	def test_a_pool_that_runs_short_triggers_one_more_request(self):
		# 30 queries per reply: five calls of 6 empty it, the sixth asks again.
		for _ in range(5):
			self.ask(Reply(REPLY), count=6)

		self.assertEqual(o.requests_today(), 1)

		self.ask(Reply(REPLY), count=6)

		self.assertEqual(o.requests_today(), 2)

	def test_each_account_has_its_own_pool(self):
		a, _ = self.ask(Reply(REPLY), count=3, account="one")
		b, net = self.ask(Reply(REPLY), count=3, account="two")

		self.assertEqual(net.call_count, 1)
		self.assertEqual(a, b)

	def test_the_pool_does_not_carry_over_to_the_next_day(self):
		self.ask(Reply(REPLY), count=3)

		with mock.patch.object(o, "_today", return_value="2999-01-01"):
			_, net = self.ask(Reply(REPLY), count=3)

		self.assertEqual(net.call_count, 1)

	def test_what_the_account_already_searched_is_never_returned(self):
		avoid = {f"query number {i} about hiking" for i in range(10)}
		got, _ = self.ask(Reply(REPLY), count=10, exclude=avoid)

		self.assertEqual(len(got), 10)
		self.assertFalse(avoid & set(got))

	def test_a_reply_with_nothing_usable_returns_nothing(self):
		got, _ = self.ask(Reply("sure!\nhere you go:\n"), count=5)

		self.assertEqual(got, [])

	def test_an_unexpected_reply_shape_returns_nothing(self):
		class Odd(Reply):
			def __init__(self):
				self.body = b'{"choices": []}'

		got, _ = self.ask(Odd(), count=5)

		self.assertEqual(got, [])


class TestFailures(OpenRouterTestCase):
	def test_no_key_means_no_request_and_no_spend(self):
		with mock.patch.dict(os.environ, {"OPENROUTER_API_KEY": ""}), self.assertLogs(o.logger, level="WARNING"):
			got, net = self.ask(Reply(REPLY))

		self.assertEqual(got, [])
		net.assert_not_called()
		self.assertEqual(o.requests_today(), 0)

	def test_a_429_backs_off_for_as_long_as_it_says(self):
		with self.assertLogs(o.logger, level="WARNING"):
			got, _ = self.ask(http_error(429, retry_after=600))

		self.assertEqual(got, [])
		self.assertGreater(o.quiet_until(), time.time() + 500)
		self.assertFalse(o.may_ask())

	def test_a_429_with_no_time_still_backs_off(self):
		with self.assertLogs(o.logger, level="WARNING"):
			self.ask(http_error(429))

		self.assertGreater(o.quiet_until(), time.time() + 60)

	def test_nothing_is_asked_during_the_quiet_spell(self):
		with self.assertLogs(o.logger, level="WARNING"):
			self.ask(http_error(429, retry_after=600))

		_, net = self.ask(Reply(REPLY))

		net.assert_not_called()

	def test_a_rejected_key_goes_quiet_for_hours(self):
		for code in (401, 403):
			with self.subTest(code=code):
				_write = o._write_json
				_write(o.USAGE_FILE, {})

				with self.assertLogs(o.logger, level="WARNING"):
					self.ask(http_error(code), account=str(code))

				self.assertGreater(o.quiet_until(), time.time() + 5 * 3600)

	def test_a_server_error_or_a_network_failure_returns_nothing(self):
		for failure in (http_error(500), http_error(503), urllib.error.URLError("down"), TimeoutError(), ValueError("bad")):
			with self.assertLogs(o.logger, level="WARNING"):
				got, _ = self.ask(failure, account=repr(failure))

			self.assertEqual(got, [])

	def test_a_server_error_does_not_silence_later_requests(self):
		with self.assertLogs(o.logger, level="WARNING"):
			self.ask(http_error(500))

		self.assertTrue(o.may_ask())


class TestSecrets(OpenRouterTestCase):
	def test_the_key_goes_in_a_header_and_nowhere_else(self):
		_, net = self.ask(Reply(REPLY))
		request = net.call_args.args[0]

		self.assertEqual(request.get_header("Authorization"), f"Bearer {KEY}")
		self.assertNotIn(KEY, request.full_url)
		self.assertNotIn(KEY, request.data.decode())

	def test_the_key_never_reaches_the_logs_or_the_files(self):
		with self.assertLogs(o.logger, level="DEBUG") as logs:
			o.logger.debug("start")
			self.ask(http_error(401))
			self.ask(http_error(429, retry_after=10), account="b")
			self.ask(urllib.error.URLError(f"cannot reach host with {KEY[:0]}"), account="c")

		self.assertNotIn(KEY, "\n".join(logs.output))

		for name in os.listdir(self.directory):
			with open(os.path.join(self.directory, name), encoding="utf-8") as handle:
				self.assertNotIn(KEY, handle.read())

	def test_the_model_and_endpoint_come_from_the_environment(self):
		with mock.patch.dict(os.environ, {"OPENROUTER_MODEL": "some/model:free", "OPENROUTER_BASE_URL": "https://example.test/v1/"}):
			_, net = self.ask(Reply(REPLY))

		request = net.call_args.args[0]

		self.assertEqual(request.full_url, "https://example.test/v1/chat/completions")
		self.assertEqual(json.loads(request.data)["model"], "some/model:free")

	def test_the_default_model_is_the_free_router(self):
		_, net = self.ask(Reply(REPLY))

		self.assertEqual(json.loads(net.call_args.args[0].data)["model"], "openrouter/free")


class TestPersona(OpenRouterTestCase):
	def write_interests(self, account, text):
		os.makedirs(o.INTERESTS_DIR, exist_ok=True)

		with open(os.path.join(o.INTERESTS_DIR, f"{account}.txt"), "w", encoding="utf-8") as handle:
			handle.write(text)

	def test_interests_are_read_per_account_and_comments_are_skipped(self):
		self.write_interests("second", "# what I like\nbasketball\n\nsourdough baking\n")

		self.assertEqual(o.interests("second"), ["basketball", "sourdough baking"])
		self.assertEqual(o.interests("default"), [])

	def test_interests_shape_the_request(self):
		self.write_interests("second", "basketball\nsourdough baking\n")
		prompt = o.build_messages(10, "second", [])[1]["content"]

		self.assertIn("basketball", prompt)
		self.assertIn("sourdough baking", prompt)

	def test_without_interests_it_asks_for_a_mix(self):
		prompt = o.build_messages(10, "second", [])[1]["content"]

		self.assertIn("Mix everyday topics", prompt)

	def test_recent_searches_are_passed_so_they_are_not_repeated(self):
		prompt = o.build_messages(10, "second", ["alpha beta", "gamma delta"])[1]["content"]

		self.assertIn("alpha beta", prompt)
		self.assertIn("Do not repeat", prompt)

	def test_the_prompt_asks_for_queries_as_people_type_them(self):
		system = o.build_messages(10, None, [])[0]["content"]

		self.assertIn("lower case", system)
		self.assertIn("one per line", system)

	def test_the_request_asks_for_a_batch(self):
		_, net = self.ask(Reply(REPLY))
		prompt = json.loads(net.call_args.args[0].data)["messages"][1]["content"]

		self.assertIn(f"Write {o.BATCH_SIZE} different", prompt)


class TestWiredIntoQueries(OpenRouterTestCase):
	def setUp(self):
		super().setUp()
		directory = tempfile.TemporaryDirectory()
		self.addCleanup(directory.cleanup)

		for patcher in (
			mock.patch.dict(os.environ, {"QUERY_SOURCE": "openrouter"}),
			mock.patch.object(queries.query_history, "HISTORY_FILE", os.path.join(directory.name, "h.jsonl")),
		):
			patcher.start()
			self.addCleanup(patcher.stop)

	def test_the_source_is_recognised(self):
		self.assertEqual(queries.selected_source(), queries.OPENROUTER)

	def test_openrouters_queries_are_used_first(self):
		with mock.patch.object(o.urllib.request, "urlopen", return_value=Reply(REPLY)), \
			mock.patch.object(query_sources, "related_queries") as feeds:
			got = queries.related_queries(4, account="default")

		self.assertEqual(len(got), 4)
		self.assertTrue(all("hiking" in q for q in got))
		feeds.assert_not_called()

	def test_a_shortfall_is_made_up_from_the_public_feeds(self):
		with mock.patch.object(o, "related_queries", return_value=["one from openrouter"]), \
			mock.patch.object(query_sources, "related_queries", return_value=["feed a", "feed b"]) as feeds:
			got = queries.related_queries(3, account="default")

		self.assertEqual(got, ["one from openrouter", "feed a", "feed b"])
		self.assertEqual(feeds.call_args.args[0], 2)

	def test_when_openrouter_fails_everything_comes_from_the_feeds(self):
		with mock.patch.object(o.urllib.request, "urlopen", side_effect=urllib.error.URLError("down")), \
			mock.patch.object(query_sources, "related_queries", return_value=["feed a", "feed b", "feed c"]), \
			self.assertLogs(o.logger, level="WARNING"):
			got = queries.related_queries(3, account="default")

		self.assertEqual(got, ["feed a", "feed b", "feed c"])

	def test_what_the_account_already_searched_is_passed_on_to_both(self):
		queries.query_history.record("default", "already searched thing")

		with mock.patch.object(o, "related_queries", return_value=[]) as router, \
			mock.patch.object(query_sources, "related_queries", return_value=["x y z"]) as feeds:
			queries.related_queries(2, account="default")

		self.assertIn("already searched thing", router.call_args.kwargs["exclude"])
		self.assertIn("already searched thing", feeds.call_args.kwargs["exclude"])

	def test_cards_use_the_public_feeds_so_the_allowance_goes_to_the_batch(self):
		with mock.patch.object(o.urllib.request, "urlopen") as net, \
			mock.patch.object(query_sources, "suggestions", return_value=["tickets concerts near me"]):
			got = queries.search_query_for_task("Search on Bing to find tickets for concerts near you", account="default")

		net.assert_not_called()
		self.assertEqual(got, "tickets concerts near me")

	def test_the_trends_and_llm_sources_are_unchanged(self):
		with mock.patch.dict(os.environ, {"QUERY_SOURCE": "trends"}):
			self.assertEqual(queries.selected_source(), queries.TRENDS)

		with mock.patch.dict(os.environ, {"QUERY_SOURCE": "llm"}):
			self.assertEqual(queries.selected_source(), queries.LLM)

		with mock.patch.dict(os.environ, {"QUERY_SOURCE": "nonsense"}):
			self.assertEqual(queries.selected_source(), queries.DEFAULT_SOURCE)


class TestSpacing(OpenRouterTestCase):
	def test_calls_are_spaced_under_the_per_minute_limit(self):
		slept = []

		with mock.patch.object(o.time, "sleep", slept.append), mock.patch.object(o.urllib.request, "urlopen", return_value=Reply(REPLY)):
			o._last_call = time.monotonic()
			o.related_queries(30, account="spacing")

		self.assertTrue(any(0 < s <= o.MIN_SPACING_SECONDS for s in slept), slept)
		self.assertLess(60 / o.MIN_SPACING_SECONDS, 20 + 1)


if __name__ == "__main__":
	unittest.main()
