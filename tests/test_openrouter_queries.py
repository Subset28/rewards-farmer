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


REAL_ENSURE_PERSONA = o.ensure_persona


class OpenRouterTestCase(unittest.TestCase):
	# The persona request is a request of its own; tests about batches and the
	# budget leave it out so their counts stay about what they test.
	INVENT_PERSONA = False

	def setUp(self):
		directory = tempfile.TemporaryDirectory()
		self.addCleanup(directory.cleanup)
		self.directory = directory.name

		patchers = [
			mock.patch.object(o, "USAGE_FILE", os.path.join(self.directory, "usage.json")),
			mock.patch.object(o, "POOL_FILE", os.path.join(self.directory, "pool.json")),
			mock.patch.object(o, "INTERESTS_DIR", os.path.join(self.directory, "interests")),
			mock.patch.object(o, "PERSONA_DIR", os.path.join(self.directory, "persona")),
			mock.patch.object(o.time, "sleep", lambda *_: None),
			mock.patch.dict(os.environ, {"OPENROUTER_API_KEY": KEY}),
		]

		if not self.INVENT_PERSONA:
			patchers.append(mock.patch.object(o, "ensure_persona", return_value=[]))

		for patcher in patchers:
			patcher.start()
			self.addCleanup(patcher.stop)

		o._last_call = 0.0
		os.environ.pop("OPENROUTER_DAILY_LIMIT", None)
		os.environ["OPENROUTER_MODEL"] = "test/single:free"

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

	def test_the_default_model_is_an_explicit_plain_free_model_not_the_random_router(self):
		os.environ.pop("OPENROUTER_MODEL")
		_, net = self.ask(Reply(REPLY))

		self.assertEqual(json.loads(net.call_args.args[0].data)["model"], "google/gemma-4-26b-a4b-it:free")
		self.assertTrue(all(m.endswith(":free") for m in o.DEFAULT_MODELS))
		self.assertEqual(o.DEFAULT_MODEL, o.DEFAULT_MODELS[0])

	def test_the_timeout_allows_for_the_free_endpoints_slow_tail(self):
		self.assertGreaterEqual(o.REQUEST_TIMEOUT, 90)

		_, net = self.ask(Reply(REPLY))

		self.assertEqual(net.call_args.kwargs["timeout"], o.REQUEST_TIMEOUT)


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

	def test_the_request_asks_for_a_batch_of_sessions(self):
		_, net = self.ask(Reply(REPLY))
		prompt = json.loads(net.call_args.args[0].data)["messages"][1]["content"]

		self.assertIn(f"Write {o.SESSIONS_PER_BATCH} different search sessions", prompt)

	def test_the_prompt_defines_a_session_and_asks_for_blank_lines_between_them(self):
		system = o.build_messages(8, None, [])[0]["content"]

		self.assertIn("session", system)
		self.assertIn("blank line between sessions", system)

	def test_a_typed_interests_file_beats_a_kept_persona(self):
		os.makedirs(o.PERSONA_DIR, exist_ok=True)

		with open(os.path.join(o.PERSONA_DIR, "second.json"), "w", encoding="utf-8") as handle:
			json.dump({"interests": ["from the persona"]}, handle)

		self.assertEqual(o.interests("second"), ["from the persona"])

		self.write_interests("second", "typed by the owner\n")

		self.assertEqual(o.interests("second"), ["typed by the owner"])


PERSONA_REPLY = "\n".join(["a local football team", "baking sourdough", "planning a trip to portugal", "budget laptops", "indoor herb gardening", "watching crime documentaries"])


class TestInventedPersona(OpenRouterTestCase):
	INVENT_PERSONA = True

	def ask_persona(self, account="second", reply=PERSONA_REPLY):
		with mock.patch.object(o.urllib.request, "urlopen", return_value=Reply(reply)) as net:
			return o.ensure_persona(account), net

	def test_one_is_invented_and_kept(self):
		made, net = self.ask_persona()

		self.assertEqual(len(made), 6)
		self.assertEqual(net.call_count, 1)
		self.assertEqual(o.interests("second"), made)
		self.assertTrue(os.path.exists(os.path.join(o.PERSONA_DIR, "second.json")))

	def test_it_is_not_invented_twice_so_the_account_keeps_the_same_interests(self):
		first, _ = self.ask_persona()
		second, net = self.ask_persona(reply="completely different\nother things\nsomething else")

		net.assert_not_called()
		self.assertEqual(first, second)

	def test_it_is_not_invented_when_the_owner_wrote_one(self):
		os.makedirs(o.INTERESTS_DIR, exist_ok=True)

		with open(os.path.join(o.INTERESTS_DIR, "second.txt"), "w", encoding="utf-8") as handle:
			handle.write("my own interest\n")

		made, net = self.ask_persona()

		net.assert_not_called()
		self.assertEqual(made, ["my own interest"])

	def test_each_account_gets_its_own(self):
		a, _ = self.ask_persona("one", "alpha thing one\nalpha thing two\nalpha thing three")
		b, _ = self.ask_persona("two", "beta thing one\nbeta thing two\nbeta thing three")

		self.assertNotEqual(a, b)
		self.assertEqual(o.interests("one"), a)

	def test_it_costs_one_request_from_the_budget(self):
		self.ask_persona()

		self.assertEqual(o.requests_today(), 1)

	def test_with_no_budget_it_is_not_asked_for(self):
		with mock.patch.dict(os.environ, {"OPENROUTER_DAILY_LIMIT": "0"}):
			made, net = self.ask_persona()

		self.assertEqual(made, [])
		net.assert_not_called()

	def test_a_reply_that_is_not_a_list_of_interests_makes_no_persona(self):
		with self.assertLogs(o.logger, level="WARNING"):
			made, _ = self.ask_persona(reply="I am sorry, I cannot help with that.")

		self.assertEqual(made, [])
		self.assertFalse(os.path.exists(os.path.join(o.PERSONA_DIR, "second.json")))

	def test_a_failed_request_makes_no_persona_and_does_not_raise(self):
		with mock.patch.object(o.urllib.request, "urlopen", side_effect=urllib.error.URLError("down")), \
			self.assertLogs(o.logger, level="WARNING"):
			self.assertEqual(o.ensure_persona("second"), [])

	def test_the_interests_are_cleaned_like_things_a_person_would_say(self):
		made, _ = self.ask_persona(reply="1. The Dodgers!\n- baking (sourdough)\n* \"road trips\"\nhere are some interests:\nx")

		self.assertEqual(made, ["the dodgers", "baking sourdough", "road trips"])

	def test_at_most_eight_are_kept(self):
		made, _ = self.ask_persona(reply="\n".join(f"interest number {i}" for i in range(20)))

		self.assertEqual(len(made), o.MAX_INTERESTS)

	def test_related_queries_makes_the_persona_first_and_uses_it_in_the_batch(self):
		replies = [Reply(PERSONA_REPLY), Reply(REPLY)]

		with mock.patch.object(o.urllib.request, "urlopen", side_effect=replies) as net:
			got = o.related_queries(5, account="second")

		self.assertEqual(net.call_count, 2)
		self.assertEqual(len(got), 5)

		batch_prompt = json.loads(net.call_args_list[1].args[0].data)["messages"][1]["content"]

		self.assertIn("a local football team", batch_prompt)
		self.assertEqual(o.requests_today(), 2)

	def test_a_second_call_the_same_day_needs_neither(self):
		replies = [Reply(PERSONA_REPLY), Reply(REPLY)]

		with mock.patch.object(o.urllib.request, "urlopen", side_effect=replies):
			o.related_queries(5, account="second")

		with mock.patch.object(o.urllib.request, "urlopen") as net:
			o.related_queries(5, account="second")

		net.assert_not_called()

	def test_a_failed_persona_does_not_stop_the_queries(self):
		replies = [urllib.error.URLError("down"), Reply(REPLY)]

		with mock.patch.object(o.urllib.request, "urlopen", side_effect=replies), self.assertLogs(o.logger, level="WARNING"):
			got = o.related_queries(5, account="second")

		self.assertEqual(len(got), 5)


class TestSessions(OpenRouterTestCase):
	REPLY = "best hiking boots\nhiking boots for wide feet\nhiking boots sale\n\nhow to bake sourdough\nsourdough starter not rising\n\ncheap flights to lisbon\nlisbon weather in march\nthings to do in lisbon\nlisbon airport transport"

	def test_blank_lines_separate_sessions(self):
		sessions = o.parse_sessions(self.REPLY)

		self.assertEqual(len(sessions), 3)
		self.assertEqual(sessions[1], ["how to bake sourdough", "sourdough starter not rising"])

	def test_a_session_is_cut_to_the_maximum_length(self):
		sessions = o.parse_sessions("\n".join(f"query about topic {i}" for i in range(4)) + "\n\n" + "\n".join(f"other topic number {i}" for i in range(9)))

		self.assertTrue(all(len(s) <= o.MAX_SESSION_LENGTH for s in sessions))

	def test_a_reply_with_no_blank_lines_is_cut_into_sessions_not_thrown_away(self):
		sessions = o.parse_sessions("\n".join(f"query number {i} here" for i in range(9)))

		self.assertEqual([len(s) for s in sessions], [3, 3, 3])

	def test_no_query_is_used_in_two_sessions(self):
		sessions = o.parse_sessions("same query here\nanother query here\n\nsame query here\nthird query here")

		flat = [q for s in sessions for q in s]

		self.assertEqual(len(flat), len(set(flat)))

	def test_what_to_avoid_is_dropped_and_an_emptied_session_disappears(self):
		sessions = o.parse_sessions("old one here\nold two here\n\nnew one here", avoid={"old one here", "old two here"})

		self.assertEqual(sessions, [["new one here"]])

	def test_a_preamble_block_is_ignored(self):
		sessions = o.parse_sessions("Here are your sessions:\n\nbest hiking boots\nhiking boots sale")

		self.assertEqual(sessions, [["best hiking boots", "hiking boots sale"]])

	def test_nothing_in_gives_no_sessions(self):
		self.assertEqual(o.parse_sessions(""), [])
		self.assertEqual(o.parse_sessions(None), [])

	def test_queries_come_back_in_session_order_so_topics_stay_together(self):
		got, _ = self.ask(Reply(self.REPLY), count=5)

		self.assertEqual(got, ["best hiking boots", "hiking boots for wide feet", "hiking boots sale", "how to bake sourdough", "sourdough starter not rising"])

	def test_the_rest_of_a_cut_session_comes_first_next_time(self):
		first, _ = self.ask(Reply(self.REPLY), count=4)
		second, net = self.ask(Reply(self.REPLY), count=3)

		self.assertEqual(first[-1], "how to bake sourdough")
		self.assertEqual(second[0], "sourdough starter not rising")
		net.assert_not_called()

	def test_an_old_flat_pool_is_still_readable(self):
		o._write_json(o.POOL_FILE, {"default": {"date": o._today(), "queries": ["old flat one", "old flat two"]}})

		got, net = self.ask(Reply(self.REPLY), count=2)

		self.assertEqual(got, ["old flat one", "old flat two"])
		net.assert_not_called()


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


class TestFreeModelGuard(OpenRouterTestCase):
	def sent_model(self, **env):
		with mock.patch.dict(os.environ, env):
			_, net = self.ask(Reply(REPLY))

		return json.loads(net.call_args.args[0].data)["model"]

	def test_a_free_model_is_used_as_given(self):
		self.assertEqual(self.sent_model(OPENROUTER_MODEL="google/gemma-4-31b-it:free"), "google/gemma-4-31b-it:free")

	def test_the_free_router_is_allowed(self):
		self.assertEqual(self.sent_model(OPENROUTER_MODEL="openrouter/free"), "openrouter/free")

	def test_a_model_without_the_free_suffix_is_replaced_not_billed(self):
		# The paid model is listed under the same name without the suffix.
		with self.assertLogs(o.logger, level="WARNING") as logs:
			sent = self.sent_model(OPENROUTER_MODEL="google/gemma-4-26b-a4b-it")

		self.assertEqual(sent, o.DEFAULT_MODEL)
		self.assertIn("would be billed", logs.output[0])

	def test_paid_models_need_an_explicit_yes(self):
		self.assertEqual(
			self.sent_model(OPENROUTER_MODEL="google/gemma-4-26b-a4b-it", OPENROUTER_ALLOW_PAID="1"),
			"google/gemma-4-26b-a4b-it",
		)

	def test_anything_but_one_does_not_allow_paid_models(self):
		for value in ("0", "true", "yes", ""):
			o._write_json(o.POOL_FILE, {})

			with self.subTest(value=value), self.assertLogs(o.logger, level="WARNING"):
				self.assertEqual(self.sent_model(OPENROUTER_MODEL="some/paid-model", OPENROUTER_ALLOW_PAID=value), o.DEFAULT_MODEL)

	def test_an_unset_or_blank_model_is_the_free_default(self):
		self.assertEqual(self.sent_model(OPENROUTER_MODEL=""), o.DEFAULT_MODEL)

	def test_the_default_is_itself_free(self):
		self.assertTrue(o.DEFAULT_MODEL.endswith(":free"))


class TestDocumentedErrors(OpenRouterTestCase):
	def fail_with(self, code):
		with self.assertLogs(o.logger, level="WARNING") as logs:
			got, _ = self.ask(http_error(code))

		return got, logs.output[0]

	def test_malformed_credits_and_unknown_model_go_quiet_for_hours_and_say_what_to_check(self):
		for code in (400, 402, 404):
			with self.subTest(code=code):
				o._write_json(o.USAGE_FILE, {})
				got, message = self.fail_with(code)

				self.assertEqual(got, [])
				self.assertGreater(o.quiet_until(), time.time() + 5 * 3600)
				self.assertIn("test/single:free", message)

	def test_a_402_says_the_model_is_not_free(self):
		_, message = self.fail_with(402)

		self.assertIn("not a free model", message)

	def test_a_502_is_brief_so_it_does_not_silence_later_requests(self):
		self.fail_with(502)

		self.assertTrue(o.may_ask())
		self.assertEqual(o.requests_today(), 1)

	def test_nothing_is_asked_during_the_quiet_spell_after_a_config_error(self):
		self.fail_with(404)
		_, net = self.ask(Reply(REPLY))

		net.assert_not_called()


class TestBilledResponses(OpenRouterTestCase):
	def reply_with_cost(self, cost):
		reply = Reply(REPLY)
		payload = json.loads(reply.body)
		payload["usage"] = {"prompt_tokens": 10, "completion_tokens": 20, "cost": cost}
		reply.body = json.dumps(payload).encode("utf-8")

		return reply

	def test_a_reported_cost_stops_requests_for_a_day_but_keeps_the_reply(self):
		with self.assertLogs(o.logger, level="WARNING") as logs:
			got, _ = self.ask(self.reply_with_cost(0.0013))

		self.assertEqual(len(got), 5)
		self.assertGreater(o.quiet_until(), time.time() + 23 * 3600)
		self.assertIn("was billed", logs.output[0])
		self.assertFalse(o.may_ask())

	def test_a_zero_cost_is_fine(self):
		got, _ = self.ask(self.reply_with_cost(0))

		self.assertEqual(len(got), 5)
		self.assertTrue(o.may_ask())

	def test_a_missing_or_odd_cost_is_fine(self):
		for cost in (None, "free", True, [], {}):
			with self.subTest(cost=cost):
				o._write_json(o.USAGE_FILE, {})
				o._write_json(o.POOL_FILE, {})
				self.ask(self.reply_with_cost(cost), account=repr(cost))

				self.assertTrue(o.may_ask())

	def test_a_reply_with_no_usage_at_all_is_fine(self):
		got, _ = self.ask(Reply(REPLY))

		self.assertEqual(len(got), 5)
		self.assertTrue(o.may_ask())

	def test_a_billed_request_still_counts_once_against_the_day(self):
		with self.assertLogs(o.logger, level="WARNING"):
			self.ask(self.reply_with_cost(0.5))

		self.assertEqual(o.requests_today(), 1)


# What OpenRouter actually returned when the shared free pool at Google was full.
SHARED_POOL_BODY = {
	"error": {
		"message": "Provider returned error",
		"code": 429,
		"metadata": {
			"raw": "google/gemma-4-26b-a4b-it:free is temporarily rate-limited upstream. Please retry shortly, or add your own key to accumulate your rate limits: https://openrouter.ai/settings/integrations",
			"provider_name": "Google AI Studio",
			"is_byok": False,
			"provider_error_code": "429",
			"limit_source": "upstream_provider_shared_pool",
		},
	},
	"user_id": "user_PRIVATE_ID_SHOULD_NOT_BE_LOGGED",
}

ACCOUNT_LIMIT_BODY = {"error": {"message": "Rate limit exceeded: free-models-per-day", "code": 429, "metadata": {"limit_source": "account"}}}


def http_error_body(code, body, retry_after=None):
	headers = email.message.Message()

	if retry_after is not None:
		headers["Retry-After"] = str(retry_after)

	return urllib.error.HTTPError("https://openrouter.ai/api/v1/chat/completions", code, "err", headers, io.BytesIO(json.dumps(body).encode("utf-8")))


class TestModelList(OpenRouterTestCase):
	def models(self, **env):
		with mock.patch.dict(os.environ, env):
			return o.models_to_use()

	def test_the_defaults_are_two_free_gemma_models(self):
		os.environ.pop("OPENROUTER_MODEL")

		self.assertEqual(o.models_to_use(), list(o.DEFAULT_MODELS))
		self.assertEqual(len(o.DEFAULT_MODELS), 3)

	def test_a_comma_separated_list_is_used_in_order_ignoring_spaces(self):
		self.assertEqual(self.models(OPENROUTER_MODEL=" a/one:free , b/two:free,, c/three:free "), ["a/one:free", "b/two:free", "c/three:free"])

	def test_an_entry_that_is_not_free_is_left_out_with_a_warning(self):
		with self.assertLogs(o.logger, level="WARNING") as logs:
			self.assertEqual(self.models(OPENROUTER_MODEL="a/one:free,google/gemma-4-26b-a4b-it"), ["a/one:free"])

		self.assertIn("would be billed", logs.output[0])

	def test_if_nothing_is_free_the_defaults_are_used(self):
		with self.assertLogs(o.logger, level="WARNING"):
			self.assertEqual(self.models(OPENROUTER_MODEL="paid/model"), list(o.DEFAULT_MODELS))

	def test_paid_models_need_an_explicit_yes(self):
		self.assertEqual(self.models(OPENROUTER_MODEL="paid/model", OPENROUTER_ALLOW_PAID="1"), ["paid/model"])

	def test_a_blank_setting_is_the_defaults(self):
		self.assertEqual(self.models(OPENROUTER_MODEL="  "), list(o.DEFAULT_MODELS))


class TestDescribe(unittest.TestCase):
	def test_it_reads_the_reason_and_recognizes_the_shared_pool(self):
		message, shared = o._describe(http_error_body(429, SHARED_POOL_BODY))

		self.assertIn("temporarily rate-limited upstream", message)
		self.assertTrue(shared)
		self.assertNotIn("PRIVATE_ID", message)

	def test_an_account_limit_is_not_the_shared_pool(self):
		message, shared = o._describe(http_error_body(429, ACCOUNT_LIMIT_BODY))

		self.assertIn("free-models-per-day", message)
		self.assertFalse(shared)

	def test_the_reason_is_kept_short(self):
		body = {"error": {"message": "x" * 5000, "code": 400}}

		self.assertLessEqual(len(o._describe(http_error_body(400, body))[0]), 200)

	def test_a_body_that_is_not_json_gives_nothing(self):
		exc = urllib.error.HTTPError("u", 500, "err", email.message.Message(), io.BytesIO(b"<html>bad gateway</html>"))

		self.assertEqual(o._describe(exc), ("", False))

	def test_an_odd_shape_gives_nothing(self):
		for body in ([], {"error": "plain string"}, {"error": {"metadata": "nope"}}, {}):
			self.assertEqual(o._describe(http_error_body(500, body))[1], False)


class TestFallsBackBetweenModels(OpenRouterTestCase):
	TWO = "m/first:free,m/second:free"

	def run_with(self, results, **env):
		"""related_queries with two models, each attempt answering from `results` in order."""
		with mock.patch.dict(os.environ, {"OPENROUTER_MODEL": self.TWO, **env}), \
			mock.patch.object(o.urllib.request, "urlopen", side_effect=results) as net:
			return o.related_queries(5), net

	def models_asked(self, net):
		return [json.loads(call.args[0].data)["model"] for call in net.call_args_list]

	def test_a_full_shared_pool_moves_on_to_the_next_model(self):
		with self.assertLogs(o.logger, level="WARNING") as logs:
			got, net = self.run_with([http_error_body(429, SHARED_POOL_BODY), Reply(REPLY)])

		self.assertEqual(len(got), 5)
		self.assertEqual(self.models_asked(net), ["m/first:free", "m/second:free"])
		self.assertEqual(o.requests_today(), 2)
		self.assertIn("shared free pool is full", logs.output[0])
		self.assertIn("temporarily rate-limited upstream", logs.output[0])

	def test_the_reason_is_logged_without_the_account_id(self):
		with self.assertLogs(o.logger, level="WARNING") as logs:
			self.run_with([http_error_body(429, SHARED_POOL_BODY), Reply(REPLY)])

		self.assertNotIn("PRIVATE_ID", "\n".join(logs.output))
		self.assertNotIn(KEY, "\n".join(logs.output))

	def test_every_model_rate_limited_goes_quiet_for_a_few_minutes_only(self):
		with self.assertLogs(o.logger, level="WARNING") as logs:
			got, net = self.run_with([http_error_body(429, SHARED_POOL_BODY) for _ in range(2)])

		self.assertEqual(got, [])
		self.assertEqual(net.call_count, 2)
		self.assertTrue(time.time() + 8 * 60 < o.quiet_until() < time.time() + 12 * 60)
		self.assertIn("Every free model is rate limited upstream", "\n".join(logs.output))

	def test_an_account_level_429_stops_without_trying_the_next_model(self):
		with self.assertLogs(o.logger, level="WARNING"):
			got, net = self.run_with([http_error_body(429, ACCOUNT_LIMIT_BODY, retry_after=900), Reply(REPLY)])

		self.assertEqual(got, [])
		self.assertEqual(net.call_count, 1)
		self.assertGreater(o.quiet_until(), time.time() + 800)

	def test_a_rejected_key_stops_without_trying_the_next_model(self):
		with self.assertLogs(o.logger, level="WARNING"):
			got, net = self.run_with([http_error_body(401, {"error": {"message": "No auth credentials found", "code": 401}}), Reply(REPLY)])

		self.assertEqual(got, [])
		self.assertEqual(net.call_count, 1)
		self.assertGreater(o.quiet_until(), time.time() + 5 * 3600)

	def test_a_model_that_does_not_exist_moves_on_to_the_next(self):
		with self.assertLogs(o.logger, level="WARNING"):
			got, net = self.run_with([http_error_body(404, {"error": {"message": "No endpoints found", "code": 404}}), Reply(REPLY)])

		self.assertEqual(len(got), 5)
		self.assertEqual(net.call_count, 2)

	def test_every_model_rejected_goes_quiet_for_hours(self):
		with self.assertLogs(o.logger, level="WARNING") as logs:
			got, _ = self.run_with([http_error_body(404, {"error": {"message": "nope", "code": 404}}) for _ in range(2)])

		self.assertEqual(got, [])
		self.assertGreater(o.quiet_until(), time.time() + 5 * 3600)
		self.assertIn("Check OPENROUTER_MODEL", "\n".join(logs.output))

	def test_a_timeout_moves_on_to_the_next_model(self):
		with self.assertLogs(o.logger, level="WARNING"):
			got, net = self.run_with([TimeoutError(), Reply(REPLY)])

		self.assertEqual(len(got), 5)
		self.assertEqual(net.call_count, 2)

	def test_an_empty_reply_moves_on_to_the_next_model(self):
		with self.assertLogs(o.logger, level="WARNING"):
			got, net = self.run_with([Reply("   "), Reply(REPLY)])

		self.assertEqual(len(got), 5)
		self.assertEqual(net.call_count, 2)

	def test_a_mix_of_failures_does_not_go_quiet(self):
		with self.assertLogs(o.logger, level="WARNING"):
			got, _ = self.run_with([http_error_body(429, SHARED_POOL_BODY), TimeoutError()])

		self.assertEqual(got, [])
		self.assertTrue(o.may_ask())

	def test_the_first_model_that_works_is_the_only_one_asked(self):
		got, net = self.run_with([Reply(REPLY)])

		self.assertEqual(len(got), 5)
		self.assertEqual(net.call_count, 1)

	def test_a_billed_reply_from_a_fallback_model_still_trips_the_breaker(self):
		billed = Reply(REPLY)
		payload = json.loads(billed.body)
		payload["usage"] = {"cost": 0.01}
		billed.body = json.dumps(payload).encode("utf-8")

		with self.assertLogs(o.logger, level="WARNING"):
			got, _ = self.run_with([http_error_body(429, SHARED_POOL_BODY), billed])

		self.assertEqual(len(got), 5)
		self.assertGreater(o.quiet_until(), time.time() + 23 * 3600)

	def test_the_attempts_are_spaced_so_a_fallback_cannot_break_the_per_minute_limit(self):
		slept = []

		with mock.patch.object(o.time, "sleep", slept.append):
			o._last_call = time.monotonic()
			self.run_with([http_error_body(429, SHARED_POOL_BODY), Reply(REPLY)])

		self.assertGreaterEqual(len([s for s in slept if 0 < s <= o.MIN_SPACING_SECONDS]), 1)


class TestStripThinking(unittest.TestCase):
	def test_a_whole_think_block_is_removed(self):
		self.assertEqual(o.strip_thinking("<think>let me plan the queries</think>best hiking boots\nsourdough starter").strip(), "best hiking boots\nsourdough starter")

	def test_a_multiline_block_and_any_case_are_removed(self):
		self.assertEqual(o.strip_thinking("<THINK>line one\nline two\n</Think>\nreal query here").strip(), "real query here")

	def test_several_blocks_are_all_removed(self):
		self.assertEqual(o.strip_thinking("<think>a</think>one query here<think>b</think> two query here").strip(), "one query here two query here")

	def test_only_the_text_after_a_lone_closing_tag_is_kept(self):
		self.assertEqual(o.strip_thinking("thoughts that ran on\nand on</think>\nbest hiking boots").strip(), "best hiking boots")

	def test_a_stray_opening_tag_is_dropped(self):
		self.assertEqual(o.strip_thinking("<think>best hiking boots").strip(), "best hiking boots")

	def test_text_without_any_is_unchanged(self):
		self.assertEqual(o.strip_thinking("best hiking boots\nsourdough starter"), "best hiking boots\nsourdough starter")

	def test_only_thinking_leaves_nothing(self):
		self.assertEqual(o.strip_thinking("<think>I should write queries now</think>").strip(), "")


class TestThirdModel(OpenRouterTestCase):
	ALL = "m/first:free,m/second:free,n/third:free"

	def run_with(self, results):
		with mock.patch.dict(os.environ, {"OPENROUTER_MODEL": self.ALL}), \
			mock.patch.object(o.urllib.request, "urlopen", side_effect=results) as net:
			return o.related_queries(5), net

	def models_asked(self, net):
		return [json.loads(call.args[0].data)["model"] for call in net.call_args_list]

	def test_the_defaults_end_with_a_model_from_another_provider(self):
		os.environ.pop("OPENROUTER_MODEL")

		models = o.models_to_use()

		self.assertEqual(models[:2], ["google/gemma-4-26b-a4b-it:free", "google/gemma-4-31b-it:free"])
		self.assertEqual(models[2], "nvidia/nemotron-3-super-120b-a12b:free")
		self.assertTrue(all(m.endswith(":free") for m in models))

	def test_the_third_is_used_when_the_first_two_are_rate_limited(self):
		with self.assertLogs(o.logger, level="WARNING"):
			got, net = self.run_with([http_error_body(429, SHARED_POOL_BODY), http_error_body(429, SHARED_POOL_BODY), Reply(REPLY)])

		self.assertEqual(len(got), 5)
		self.assertEqual(self.models_asked(net), ["m/first:free", "m/second:free", "n/third:free"])
		self.assertEqual(o.requests_today(), 3)

	def test_all_three_rate_limited_waits_a_few_minutes(self):
		with self.assertLogs(o.logger, level="WARNING"):
			got, net = self.run_with([http_error_body(429, SHARED_POOL_BODY) for _ in range(3)])

		self.assertEqual(got, [])
		self.assertEqual(net.call_count, 3)
		self.assertTrue(time.time() + 8 * 60 < o.quiet_until() < time.time() + 12 * 60)

	def test_a_thinking_model_that_returns_only_thinking_moves_on(self):
		with self.assertLogs(o.logger, level="WARNING"):
			got, net = self.run_with([Reply("<think>planning</think>"), Reply(REPLY)])

		self.assertEqual(len(got), 5)
		self.assertEqual(net.call_count, 2)

	def test_thinking_that_leaks_into_the_reply_is_not_searched_for(self):
		leaked = "<think>I need eight words at most per query so I will keep them short</think>\n" + REPLY
		got, _ = self.run_with([Reply(leaked)])

		self.assertEqual(len(got), 5)
		self.assertTrue(all("hiking" in q for q in got))
		self.assertFalse(any("think" in q or "eight words" in q for q in got))

	def test_the_output_cap_leaves_room_to_think_and_still_answer(self):
		_, net = self.run_with([Reply(REPLY)])

		self.assertEqual(json.loads(net.call_args.args[0].data)["max_tokens"], o.MAX_TOKENS)
		self.assertGreaterEqual(o.MAX_TOKENS, 2000)

	def test_no_reasoning_parameter_is_sent_because_its_shape_is_not_documented(self):
		_, net = self.run_with([Reply(REPLY)])
		sent = json.loads(net.call_args.args[0].data)

		self.assertNotIn("reasoning", sent)
		self.assertNotIn("reasoning_effort", sent)


if __name__ == "__main__":
	unittest.main()
