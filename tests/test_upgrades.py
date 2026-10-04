"""Tests for the brake, alerts, points record, account gap and Explore retry.

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

from selenium.common.exceptions import NoSuchElementException, WebDriverException

import main as main_module
import notify
import points_log
import query_sources
import rewards_tasks
import safety
import search_behavior
import search_only
from fakes import FakeDriver

# What an ordinary Rewards earn page reads like, including the words a loose
# pattern would trip on: a "puzzle" card, terms links, a download-the-app offer.
EARN_PAGE_TEXT = """
Microsoft Rewards
Earn Redeem Dashboard
Your daily set
Complete this puzzle
Arrange the tiles to reveal the image
Download the Bing app now to claim 500 points as a new user!
Search on Bing to find tickets for concerts near you
Terms of Use Privacy & Cookies Rewards Terms Feedback
Today's points 120
"""


class Page:
	def __init__(self, url, text):
		self.current_url = url
		self._text = text

	def execute_script(self, script):
		return self._text


class TestClassify(unittest.TestCase):
	def test_an_ordinary_earn_page_is_not_a_risk(self):
		self.assertIsNone(safety.classify("https://rewards.bing.com/", EARN_PAGE_TEXT))

	def test_bing_results_are_not_a_risk(self):
		self.assertIsNone(safety.classify("https://www.bing.com/search?q=hiking", "hiking trails near me results"))

	def test_a_sign_in_redirect_is_signed_out(self):
		for url in (
			"https://login.live.com/oauth20_authorize.srf?x=1",
			"https://login.microsoftonline.com/common/oauth2",
		):
			self.assertEqual(safety.classify(url, "").kind, safety.SIGNED_OUT)

	def test_the_rewards_signed_out_pages_are_signed_out(self):
		for url in (
			"https://rewards.bing.com/welcome",
			"https://rewards.bing.com/about",
			"https://rewards.bing.com/about?signin=1",
		):
			with self.subTest(url=url):
				self.assertEqual(safety.classify(url, "Join Microsoft Rewards").kind, safety.SIGNED_OUT)

	def test_the_signed_in_rewards_pages_are_not(self):
		for url in ("https://rewards.bing.com/", "https://rewards.bing.com/?ref=x", "https://rewards.bing.com/earn", "https://rewards.bing.com/redeem"):
			with self.subTest(url=url):
				self.assertIsNone(safety.classify(url, EARN_PAGE_TEXT))

	def test_the_account_locked_url_is_a_restriction(self):
		self.assertEqual(safety.classify("https://account.live.com/Abuse?mkt=en-US", "").kind, safety.RESTRICTED)

	def test_verification_urls_are_a_challenge(self):
		self.assertEqual(safety.classify("https://account.live.com/proofs/Verify", "").kind, safety.CHALLENGE)
		self.assertEqual(safety.classify("https://www.bing.com/turing/captcha/challenge", "").kind, safety.CHALLENGE)

	def test_notices_in_the_page_text(self):
		cases = {
			"We've detected unusual activity on your account": safety.RESTRICTED,
			"Your account has been suspended": safety.RESTRICTED,
			"Your account is temporarily locked": safety.RESTRICTED,
			"This account has been temporarily restricted": safety.RESTRICTED,
			"You are restricted from earning points": safety.RESTRICTED,
			"Please verify you are human": safety.CHALLENGE,
			"Are you a robot?": safety.CHALLENGE,
			"Press and hold the button": safety.CHALLENGE,
			"Solve the puzzle to continue": safety.CHALLENGE,
		}

		for text, kind in cases.items():
			with self.subTest(text=text):
				self.assertEqual(safety.classify("https://rewards.bing.com/", text).kind, kind)

	def test_the_notice_is_found_whatever_its_case(self):
		self.assertEqual(safety.classify("https://rewards.bing.com/", "UNUSUAL ACTIVITY detected").kind, safety.RESTRICTED)

	def test_a_notice_far_down_a_huge_page_is_ignored(self):
		text = ("filler " * 2000) + "unusual activity"

		self.assertIsNone(safety.classify("https://rewards.bing.com/", text))

	def test_a_missing_url_or_text_is_not_a_risk(self):
		self.assertIsNone(safety.classify("", ""))
		self.assertIsNone(safety.classify(None, None))


class TestBrake(unittest.TestCase):
	def setUp(self):
		self.directory = tempfile.TemporaryDirectory()
		self.addCleanup(self.directory.cleanup)
		patcher = mock.patch.object(safety, "PAUSE_FILE", os.path.join(self.directory.name, "PAUSED"))
		patcher.start()
		self.addCleanup(patcher.stop)
		alert = mock.patch.object(safety.notify, "send")
		self.alert = alert.start()
		self.addCleanup(alert.stop)

	def test_it_starts_off(self):
		self.assertIsNone(safety.paused())

	def test_tripping_writes_the_record_and_sends_one_alert(self):
		safety.trip(safety.Risk(safety.RESTRICTED, "the page says 'unusual activity'"), "personal")

		record = safety.paused()

		self.assertEqual(record["kind"], safety.RESTRICTED)
		self.assertEqual(record["account"], "personal")
		self.assertIn("unusual activity", record["reason"])
		self.alert.assert_called_once()
		self.assertEqual(self.alert.call_args.kwargs["priority"], "high")

	def test_tripping_again_keeps_the_first_record_and_does_not_alert_twice(self):
		safety.trip(safety.Risk(safety.CHALLENGE, "first"), "one")
		safety.trip(safety.Risk(safety.RESTRICTED, "second"), "two")

		self.assertEqual(safety.paused()["reason"], "first")
		self.assertEqual(self.alert.call_count, 1)

	def test_clearing_turns_it_off(self):
		safety.trip(safety.Risk(safety.CHALLENGE, "x"), "a")

		self.assertTrue(safety.clear())
		self.assertIsNone(safety.paused())
		self.assertFalse(safety.clear())

	def test_an_unreadable_pause_file_still_counts_as_paused(self):
		with open(safety.PAUSE_FILE, "w") as handle:
			handle.write("{not json")

		self.assertEqual(safety.paused()["kind"], "unknown")

	def test_guard_trips_and_raises_on_a_risky_page(self):
		page = Page("https://login.live.com/x", "")

		with self.assertRaises(safety.AccountAtRisk) as ctx:
			safety.guard(page, "personal")

		self.assertEqual(ctx.exception.risk.kind, safety.SIGNED_OUT)
		# A plain sign-out pauses that account alone.
		self.assertIsNone(safety.paused())
		self.assertIsNotNone(safety.paused_for("personal"))

	def test_guard_is_quiet_on_an_ordinary_page(self):
		safety.guard(Page("https://rewards.bing.com/", EARN_PAGE_TEXT), "personal")

		self.assertIsNone(safety.paused())
		self.alert.assert_not_called()

	def test_a_page_that_cannot_be_read_is_not_a_risk(self):
		class Broken:
			@property
			def current_url(self):
				raise WebDriverException("chrome not reachable")

		self.assertIsNone(safety.inspect(Broken()))

	def test_the_command_line(self):
		safety.trip(safety.Risk(safety.CHALLENGE, "x"), "a")

		with mock.patch("builtins.print") as shown:
			self.assertEqual(safety.main(["safety.py", "status"]), 0)
			self.assertIn("PAUSED", shown.call_args.args[0])
			self.assertEqual(safety.main(["safety.py", "clear"]), 0)
			self.assertEqual(safety.main(["safety.py", "status"]), 0)
			self.assertEqual(shown.call_args.args[0], "running normally")
			self.assertEqual(safety.main(["safety.py", "nonsense"]), 2)


class TestBrakeInTheTaskLoop(unittest.TestCase):
	STEPS = [
		"complete_bing_daily_set", "complete_explore_on_bing_tasks", "complete_visual_search",
		"complete_misc_cards", "complete_required_searches", "claim_bonus_points", "complete_quests",
	]

	def _tasks(self, page, failures):
		tasks = rewards_tasks.RewardsTaskUtils.__new__(rewards_tasks.RewardsTaskUtils)
		tasks.driver = page
		tasks.account_name = "personal"
		tasks.tab_utils = types.SimpleNamespace(close_all_other_tabs=lambda: None)
		tasks.ran = []

		for name in self.STEPS:
			def step(name=name):
				tasks.ran.append(name)

				if name in failures:
					raise failures[name]

			setattr(tasks, name, step)

		tasks.restore_main_tab = lambda: None
		tasks.return_to_rewards_home = lambda: None

		return tasks

	def setUp(self):
		directory = tempfile.TemporaryDirectory()
		self.addCleanup(directory.cleanup)

		for patcher in (
			mock.patch.object(safety, "PAUSE_FILE", os.path.join(directory.name, "PAUSED")),
			mock.patch.object(safety.notify, "send"),
		):
			patcher.start()
			self.addCleanup(patcher.stop)

	def test_a_run_into_a_sign_in_page_does_nothing_at_all(self):
		tasks = self._tasks(Page("https://login.live.com/x", ""), {})

		with self.assertRaises(safety.AccountAtRisk):
			tasks.complete_all_tasks()

		self.assertEqual(tasks.ran, [])

	def test_a_task_that_fails_on_a_restriction_notice_stops_the_run(self):
		page = Page("https://rewards.bing.com/", EARN_PAGE_TEXT)
		tasks = self._tasks(page, {"complete_explore_on_bing_tasks": NoSuchElementException("gone")})

		original = tasks.complete_explore_on_bing_tasks

		def fail_and_show_the_notice():
			page._text = "We've detected unusual activity on your account"
			original()

		tasks.complete_explore_on_bing_tasks = fail_and_show_the_notice

		with self.assertRaises(safety.AccountAtRisk):
			tasks.complete_all_tasks()

		self.assertEqual(tasks.ran, ["complete_bing_daily_set", "complete_explore_on_bing_tasks"])

	def test_an_ordinary_failure_still_lets_the_other_tasks_run(self):
		page = Page("https://rewards.bing.com/", EARN_PAGE_TEXT)
		tasks = self._tasks(page, {"complete_visual_search": NoSuchElementException("gone")})

		with self.assertLogs(rewards_tasks.logger, level="INFO"):
			tasks.complete_all_tasks()

		self.assertEqual(tasks.ran, self.STEPS)


class TestEntryPointsRespectTheBrake(unittest.TestCase):
	def test_main_does_not_run_while_paused(self):
		with mock.patch.object(main_module.safety, "blocked", return_value={"kind": "challenge", "reason": "x"}), \
			mock.patch.object(main_module, "run_account") as run, \
			mock.patch.object(main_module.accounts, "configured", return_value=[mock.Mock(name="a")]), \
			mock.patch.object(main_module.desktop_utils, "reset_virtual_desktop_state"), \
			mock.patch.object(main_module.log_utils, "setup_logging"):
			self.assertEqual(main_module.main(), 3)

		run.assert_not_called()

	def test_a_trip_in_one_account_stops_the_remaining_accounts(self):
		accounts = [types.SimpleNamespace(name=n) for n in ("one", "two", "three")]
		seen = []

		def run(account):
			seen.append(account.name)

			if account.name == "one":
				raise safety.AccountAtRisk(safety.Risk(safety.CHALLENGE, "x"))

			return True

		with mock.patch.object(main_module.safety, "blocked", return_value=None), \
			mock.patch.object(main_module.safety, "paused_for", return_value=None), \
			mock.patch.object(main_module, "run_account", side_effect=run), \
			mock.patch.object(main_module.accounts, "configured", return_value=accounts), \
			mock.patch.object(main_module.desktop_utils, "reset_virtual_desktop_state"), \
			mock.patch.object(main_module.desktop_utils, "cleanup_virtual_desktop"), \
			mock.patch.object(main_module, "HEADLESS", True), \
			mock.patch.object(main_module.time, "sleep"), \
			mock.patch.object(main_module.log_utils, "setup_logging"):
			main_module.main()

		self.assertEqual(seen, ["one"])

	def test_accounts_are_worked_one_after_another_with_a_gap_between_them(self):
		accounts = [types.SimpleNamespace(name=n) for n in ("one", "two", "three")]
		events = []

		with mock.patch.object(main_module.safety, "blocked", return_value=None), \
			mock.patch.object(main_module.safety, "paused_for", return_value=None), \
			mock.patch.object(main_module, "run_account", side_effect=lambda a: events.append(("run", a.name)) or True), \
			mock.patch.object(main_module.accounts, "configured", return_value=accounts), \
			mock.patch.object(main_module.desktop_utils, "reset_virtual_desktop_state"), \
			mock.patch.object(main_module.desktop_utils, "cleanup_virtual_desktop"), \
			mock.patch.object(main_module, "HEADLESS", True), \
			mock.patch.object(main_module.time, "sleep", side_effect=lambda s: events.append(("sleep", s))), \
			mock.patch.object(main_module.log_utils, "setup_logging"):
			main_module.main()

		self.assertEqual([e[0] for e in events], ["run", "sleep", "run", "sleep", "run"])
		self.assertTrue(all(20 * 60 <= e[1] <= 60 * 60 for e in events if e[0] == "sleep"))

	def test_search_only_does_not_run_while_paused(self):
		with mock.patch.object(search_only.safety, "blocked", return_value={"kind": "x", "reason": "y"}), \
			mock.patch.object(search_only, "run_account_searches") as run, \
			mock.patch.object(search_only.accounts, "configured", return_value=[mock.Mock()]), \
			mock.patch.object(search_only.desktop_utils, "reset_virtual_desktop_state"), \
			mock.patch.object(search_only.log_utils, "setup_logging"):
			self.assertEqual(search_only.main(), 3)

		run.assert_not_called()


class TestScopedPause(unittest.TestCase):
	def setUp(self):
		directory = tempfile.TemporaryDirectory()
		self.addCleanup(directory.cleanup)

		for patcher in (
			mock.patch.object(safety, "PAUSE_FILE", os.path.join(directory.name, "PAUSED")),
			mock.patch.object(safety.notify, "send"),
		):
			patcher.start()
			self.addCleanup(patcher.stop)

	def signed_out(self, account):
		safety.trip(safety.Risk(safety.SIGNED_OUT, "login lapsed"), account)

	def challenge(self, account):
		safety.trip(safety.Risk(safety.CHALLENGE, "human check"), account)

	def test_a_sign_out_pauses_only_that_account(self):
		self.signed_out("second")

		self.assertIsNone(safety.paused())
		self.assertIsNotNone(safety.paused_for("second"))
		self.assertIsNone(safety.paused_for("default"))

	def test_a_challenge_or_restriction_pauses_everyone(self):
		self.challenge("second")

		self.assertIsNotNone(safety.paused())
		self.assertIsNotNone(safety.paused_for("default"))
		self.assertIsNotNone(safety.paused_for("anyone"))

	def test_the_second_account_signing_out_never_blocks_the_first(self):
		self.signed_out("second")

		self.assertIsNone(safety.blocked(["default"]))
		self.assertIsNone(safety.blocked(["default", "second"]))

	def test_a_scheduler_for_only_the_paused_account_is_blocked(self):
		self.signed_out("second")

		self.assertIsNotNone(safety.blocked(["second"]))

	def test_the_whole_brake_blocks_every_scheduler(self):
		self.challenge("default")

		self.assertIsNotNone(safety.blocked(["second"]))
		self.assertIsNotNone(safety.blocked(None))

	def test_nothing_paused_blocks_nothing(self):
		self.assertIsNone(safety.blocked(["default"]))
		self.assertIsNone(safety.blocked(None))
		self.assertIsNone(safety.blocked([]))

	def test_a_sign_out_is_not_tripped_again_and_does_not_alert_twice(self):
		self.signed_out("second")
		self.signed_out("second")

		self.assertEqual(safety.notify.send.call_count, 1)

	def test_a_sign_out_while_everything_is_paused_adds_nothing(self):
		self.challenge("default")
		self.signed_out("second")

		self.assertIsNone(safety._read(safety._file_for("second")))

	def test_clearing_one_account_leaves_the_others(self):
		self.signed_out("second")
		self.signed_out("third")

		self.assertTrue(safety.clear("second"))
		self.assertIsNone(safety.paused_for("second"))
		self.assertIsNotNone(safety.paused_for("third"))

	def test_clearing_with_no_account_clears_everything(self):
		self.signed_out("second")
		self.challenge("default")

		self.assertTrue(safety.clear())
		self.assertIsNone(safety.paused())
		self.assertIsNone(safety.paused_for("second"))
		self.assertFalse(safety.clear())

	def test_the_command_line_shows_and_clears_a_single_account(self):
		self.signed_out("second")

		with mock.patch("builtins.print") as shown:
			safety.main(["safety.py", "status"])
			self.assertIn("second", shown.call_args.args[0])
			safety.main(["safety.py", "clear", "second"])
			safety.main(["safety.py", "status"])
			self.assertEqual(shown.call_args.args[0], "running normally")


class TestMainSkipsOnlyThePausedAccount(unittest.TestCase):
	def test_the_other_account_still_runs(self):
		accounts = [types.SimpleNamespace(name=n) for n in ("default", "second")]
		seen = []

		def paused_for(name):
			return {"kind": "signed-out", "reason": "x"} if name == "second" else None

		with mock.patch.object(main_module.safety, "blocked", return_value=None), 			mock.patch.object(main_module.safety, "paused_for", side_effect=paused_for), 			mock.patch.object(main_module, "run_account", side_effect=lambda a: seen.append(a.name) or True), 			mock.patch.object(main_module.accounts, "configured", return_value=accounts), 			mock.patch.object(main_module.desktop_utils, "reset_virtual_desktop_state"), 			mock.patch.object(main_module.desktop_utils, "cleanup_virtual_desktop"), 			mock.patch.object(main_module, "HEADLESS", True), 			mock.patch.object(main_module.time, "sleep"), 			mock.patch.object(main_module.log_utils, "setup_logging"):
			main_module.main()

		self.assertEqual(seen, ["default"])


class TestNotify(unittest.TestCase):
	def test_without_a_url_it_only_logs(self):
		with mock.patch.dict(os.environ, {"NOTIFY_URL": ""}), mock.patch.object(notify.urllib.request, "urlopen") as post:
			self.assertFalse(notify.send("t", "m"))

		post.assert_not_called()

	def test_a_url_that_is_not_http_is_refused(self):
		with mock.patch.dict(os.environ, {"NOTIFY_URL": "file:///etc/passwd"}), mock.patch.object(notify.urllib.request, "urlopen") as post:
			self.assertFalse(notify.send("t", "m"))

		post.assert_not_called()

	def test_it_posts_the_message_with_a_title_and_priority(self):
		with mock.patch.dict(os.environ, {"NOTIFY_URL": "https://ntfy.sh/mytopic"}), mock.patch.object(notify.urllib.request, "urlopen") as post:
			self.assertTrue(notify.send("Paused", "look at it", priority="high"))

		request = post.call_args.args[0]

		self.assertEqual(request.full_url, "https://ntfy.sh/mytopic")
		self.assertEqual(request.data, b"look at it")
		self.assertEqual(request.get_header("Title"), "Paused")
		self.assertEqual(request.get_header("Priority"), "high")

	def test_a_title_with_non_ascii_does_not_break_the_header(self):
		with mock.patch.dict(os.environ, {"NOTIFY_URL": "https://ntfy.sh/t"}), mock.patch.object(notify.urllib.request, "urlopen") as post:
			self.assertTrue(notify.send("Paüsed", "m"))

		self.assertEqual(post.call_args.args[0].get_header("Title"), "Pa?sed")

	def test_a_failing_server_never_raises(self):
		with mock.patch.dict(os.environ, {"NOTIFY_URL": "https://ntfy.sh/t"}), \
			mock.patch.object(notify.urllib.request, "urlopen", side_effect=notify.urllib.error.URLError("down")):
			self.assertFalse(notify.send("t", "m"))


class TestPointsLog(unittest.TestCase):
	PANEL = "Points breakdown\nToday's points\n120\nToday's activity\nPoints\nBing search\n50/50\nOffers\n70\nHistory\nPoints\nThis month\n710\nThis year\n710\nLifetime\n1,133\nHow it works"

	def setUp(self):
		directory = tempfile.TemporaryDirectory()
		self.addCleanup(directory.cleanup)
		patcher = mock.patch.object(points_log, "LOG_FILE", os.path.join(directory.name, "points.jsonl"))
		patcher.start()
		self.addCleanup(patcher.stop)

	def test_it_reads_the_three_numbers_off_the_panel(self):
		self.assertEqual(points_log.parse_breakdown(self.PANEL), {"today": 120, "month": 710, "lifetime": 1133})

	def test_a_panel_missing_a_field_gives_what_it_has(self):
		self.assertEqual(points_log.parse_breakdown("This month\n55"), {"month": 55})

	def test_nonsense_gives_nothing(self):
		self.assertEqual(points_log.parse_breakdown("hello"), {})

	def test_readings_are_appended_and_read_back_per_account(self):
		points_log.record("a", {"today": 10, "month": 100, "lifetime": 1000})
		points_log.record("b", {"today": 20, "month": 200, "lifetime": 2000})
		points_log.record("a", {"today": 30, "month": 130, "lifetime": 1030})

		self.assertEqual([r["today"] for r in points_log.history("a")], [10, 30])
		self.assertEqual(len(points_log.history()), 3)

	def test_no_file_is_an_empty_history(self):
		self.assertEqual(points_log.history(), [])
		self.assertEqual(points_log.summary([]), "no readings yet")

	def test_points_to_the_next_level(self):
		self.assertEqual(points_log.to_next_level(710, 750), 40)
		self.assertEqual(points_log.to_next_level(900, 750), 0)

	def test_the_summary_says_how_far_off_the_next_level_is(self):
		points_log.record("a", {"today": 120, "month": 710, "lifetime": 1133})

		self.assertIn("40 to the next level", points_log.summary(points_log.history(), 750))

	def test_no_target_means_no_next_level_line(self):
		points_log.record("a", {"today": 120, "month": 710, "lifetime": 1133})

		self.assertNotIn("next level", points_log.summary(points_log.history()))

	def test_targets_are_per_account_and_unlisted_ones_have_none(self):
		with mock.patch.dict(os.environ, {points_log.LEVEL_TARGETS_ENV: "default=750, second=500,junk,x=y"}):
			self.assertEqual(points_log.target_for("default"), 750)
			self.assertEqual(points_log.target_for("second"), 500)
			self.assertIsNone(points_log.target_for("third"))

	def test_default_targets_only_cover_the_default_account(self):
		with mock.patch.dict(os.environ, clear=False) as env:
			env.pop(points_log.LEVEL_TARGETS_ENV, None)

			self.assertEqual(points_log.target_for("default"), 750)
			self.assertIsNone(points_log.target_for("second"))

	def test_the_report_keeps_accounts_apart(self):
		points_log.record("default", {"today": 120, "month": 710, "lifetime": 1133})
		points_log.record("second", {"today": 5, "month": 20, "lifetime": 30})

		with mock.patch.dict(os.environ, {points_log.LEVEL_TARGETS_ENV: "default=750"}):
			text = points_log.report()

		self.assertIn("month 710", text)
		self.assertIn("month 20 ", text)
		self.assertEqual(text.count("to the next level"), 1)
		self.assertIn("default", text.split("\n\n")[0])
		self.assertEqual(points_log.report("second").count("month"), 1)

	def test_the_summary_reports_a_daily_rate_over_several_days(self):
		rows = [
			{"time": "2026-09-29 10:00:00", "account": "a", "lifetime": 878},
			{"time": "2026-09-30 10:00:00", "account": "a", "lifetime": 1133},
		]

		self.assertIn("255 points a day over 1 days", points_log.summary(rows))


class TestAccountGap(unittest.TestCase):
	def test_it_is_within_the_range_in_seconds(self):
		rng = random.Random(1)

		for _ in range(200):
			self.assertTrue(10 * 60 <= search_behavior.account_gap_seconds("10-30", rng) <= 30 * 60)

	def test_a_reversed_range_is_still_a_range(self):
		self.assertTrue(600 <= search_behavior.account_gap_seconds("30-10", random.Random(2)) <= 1800)

	def test_a_malformed_value_gives_the_default_not_no_gap(self):
		for raw in (None, "", "soon", "5", "-1-5", "a-b"):
			with self.subTest(raw=raw):
				self.assertTrue(20 * 60 <= search_behavior.account_gap_seconds(raw, random.Random(3)) <= 60 * 60)

	def test_the_gap_is_not_the_same_every_time(self):
		rng = random.Random(4)

		self.assertGreater(len({search_behavior.account_gap_seconds("20-60", rng) for _ in range(20)}), 10)


class TestQueryPick(unittest.TestCase):
	DESC = "Search on Bing to find tickets for concerts near you"

	def test_pick_chooses_a_different_suggestion_and_clamps(self):
		with mock.patch.object(query_sources, "suggestions", return_value=["tickets concerts near me", "concert tickets near me 2026"]):
			self.assertEqual(query_sources.query_from_task_description(self.DESC), "tickets concerts near me")
			self.assertEqual(query_sources.query_from_task_description(self.DESC, pick=1), "concert tickets near me 2026")
			self.assertEqual(query_sources.query_from_task_description(self.DESC, pick=9), "concert tickets near me 2026")

	def test_with_no_suggestions_it_is_the_trimmed_description_either_way(self):
		with mock.patch.object(query_sources, "suggestions", return_value=[]):
			self.assertEqual(
				query_sources.query_from_task_description(self.DESC, pick=1),
				query_sources.query_from_task_description(self.DESC),
			)


class TestConcreteQuery(unittest.TestCase):
	CASES = {
		"Search on Bing for the meaning of a word you don't understand.": "define ",
		"Search on Bing to see what time it is in a different time zone.": "current time in ",
		"Search on Bing for the latest price of a specific stock.": " stock price",
		"Search on Bing for the lyrics of your favorite song": " lyrics",
		"Search on Bing for the lyrics of your favourite song": " lyrics",
		"Search on Bing to find items on your shopping list": "buy ",
	}

	def test_each_placeholder_becomes_a_real_search(self):
		for description, marker in self.CASES.items():
			with self.subTest(description=description):
				query = query_sources.concrete_query(description)

				self.assertIsNotNone(query)
				self.assertIn(marker, query)
				# Nothing of the placeholder wording is left in it.
				for leftover in ("specific", "different", "favorite", "understand", "shopping list"):
					self.assertNotIn(leftover, query.lower())

	def test_ordinary_descriptions_are_left_alone(self):
		for description in (
			"Search on Bing to find tickets for concerts near you",
			"Search on Bing to compare checking and savings account options",
			"Search on Bing to book rental cars for your next adventure",
			"",
			None,
		):
			with self.subTest(description=description):
				self.assertIsNone(query_sources.concrete_query(description))

	def test_a_retry_picks_a_different_one(self):
		description = "Search on Bing for the latest price of a specific stock."

		for seed in range(20):
			first = query_sources.concrete_query(description, 0, random.Random(seed))
			second = query_sources.concrete_query(description, 1, random.Random(seed))

			self.assertNotEqual(first, second)

	def test_it_takes_priority_over_autosuggest_and_never_needs_the_network(self):
		with mock.patch.object(query_sources, "suggestions", side_effect=AssertionError("network used")):
			query = query_sources.query_from_task_description("Search on Bing for the latest price of a specific stock.")

		self.assertTrue(query.endswith(" stock price"))

	def test_other_descriptions_still_use_autosuggest(self):
		with mock.patch.object(query_sources, "suggestions", return_value=["tickets concerts near me"]):
			self.assertEqual(
				query_sources.query_from_task_description("Search on Bing to find tickets for concerts near you"),
				"tickets concerts near me",
			)


class TestExploreRetry(unittest.TestCase):
	"""complete_explore_on_bing_tasks against a page whose cards credit or do not."""

	def _tasks(self, credits_on_first, credits_on_retry, already_done=()):
		tasks = rewards_tasks.RewardsTaskUtils.__new__(rewards_tasks.RewardsTaskUtils)
		tasks.searched = []
		state = {name: name in already_done for name in credits_on_first}

		def card(name):
			return types.SimpleNamespace(name=name)

		tasks.elements = types.SimpleNamespace(
			get_explore_on_bing_elements=lambda: [card(n) for n in state],
			extract_card_descriptions=lambda c: c.name,
			card_is_complete=lambda c: state[c.name],
		)

		tasks.switch_to_earn_page = lambda: None

		def search(c, pick=0):
			tasks.searched.append((c.name, pick))
			table = credits_on_first if pick == 0 else credits_on_retry

			if table[c.name]:
				state[c.name] = True

		tasks.search_explore_card = search
		tasks.incomplete_explore_descriptions = lambda: [n for n, done in state.items() if not done]

		return tasks

	def _run(self, tasks):
		with mock.patch.object(rewards_tasks.time, "sleep"), self.assertLogs(rewards_tasks.logger, level="INFO") as logs:
			tasks.complete_explore_on_bing_tasks()

		return "\n".join(logs.output)

	def test_cards_that_credit_are_searched_once_and_not_retried(self):
		tasks = self._tasks({"a": True, "b": True}, {"a": True, "b": True})
		tasks.search_explore_card = tasks.search_explore_card
		with self.assertRaises(AssertionError):
			self._run(tasks)  # no log lines at all: nothing was left to warn about or retry

		self.assertEqual(tasks.searched, [("a", 0), ("b", 0)])

	def test_a_card_that_does_not_credit_is_retried_once_with_a_different_query(self):
		tasks = self._tasks({"a": True, "b": False}, {"a": True, "b": True})
		output = self._run(tasks)

		self.assertEqual(tasks.searched, [("a", 0), ("b", 0), ("b", 1)])
		self.assertNotIn("is not complete", output)

	def test_a_card_that_never_credits_is_retried_only_once_then_reported(self):
		tasks = self._tasks({"a": False}, {"a": False})
		output = self._run(tasks)

		self.assertEqual(tasks.searched, [("a", 0), ("a", 1)])
		self.assertIn("Explore on Bing Card [desc='a'] is not complete", output)

	def test_a_card_that_is_already_done_is_not_searched_at_all(self):
		tasks = self._tasks({"a": True, "b": True, "c": True}, {}, already_done=("a", "c"))

		with mock.patch.object(rewards_tasks.time, "sleep"):
			tasks.complete_explore_on_bing_tasks()

		self.assertEqual(tasks.searched, [("b", 0)])

	def test_no_cards_is_still_a_skip(self):
		tasks = self._tasks({}, {})

		with self.assertRaises(NoSuchElementException):
			tasks.complete_explore_on_bing_tasks()


if __name__ == "__main__":
	unittest.main()
