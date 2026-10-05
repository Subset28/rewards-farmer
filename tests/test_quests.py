"""Tests for the quest (punch card) task and the per-step points lines.

	python -m unittest discover -s tests
"""

import os
import sys
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from selenium.common.exceptions import NoSuchElementException, WebDriverException

import quests
import rewards_tasks
import safety


class TestProgress(unittest.TestCase):
	def test_reads_done_and_total(self):
		self.assertEqual(quests.progress("Expires in 4 weeks See your new dashboard +95 1/4 tasks"), (1, 4))
		self.assertEqual(quests.progress("0/5 tasks"), (0, 5))
		self.assertEqual(quests.progress("2 / 8 task"), (2, 8))

	def test_no_progress_is_none(self):
		self.assertIsNone(quests.progress("Spotify playlists on the house"))
		self.assertIsNone(quests.progress(""))
		self.assertIsNone(quests.progress(None))


class TestWantsQuest(unittest.TestCase):
	def test_an_ordinary_open_quest(self):
		self.assertTrue(quests.wants_quest("/earn/quest/ENWW_pcparent_FY27_BingMonthlyPC_Oct_punchcard", "+50 0/5 tasks"))

	def test_quests_that_need_something_we_do_not_have_are_left_alone(self):
		for href in (
			"/earn/quest/WW_evergreen_pcparent_Spotify_punchcard",
			"/earn/quest/WW_pcparent_RewardsApp_weekly_Exclusive_Septw4_2026_punchcard",
			"/earn/quest/ENstar_pcparent_FY26_WSB_Dec_punchcard",
		):
			with self.subTest(href=href):
				self.assertFalse(quests.wants_quest(href, "0/6 tasks"))

	def test_a_finished_quest_is_skipped(self):
		self.assertFalse(quests.wants_quest("/earn/quest/abc_punchcard", "4/4 tasks"))

	def test_unknown_progress_is_worth_a_look(self):
		self.assertTrue(quests.wants_quest("/earn/quest/abc_punchcard", "Some quest"))

	def test_a_link_that_is_not_a_quest_is_not_wanted(self):
		self.assertFalse(quests.wants_quest("/earn", ""))
		self.assertFalse(quests.wants_quest("", ""))


class TestIsTaskLink(unittest.TestCase):
	CASES = [
		("https://www.bing.com/search?q=Halloween+costumes+2026&form=ML2Y36", "Shop costume looks", True),
		("https://www.bing.com/?form=ML2PCR", "Search", True),
		("https://bing.com/search?q=toys", "Toys", True),
		# In-site links navigate the Rewards page itself; the first one tried
		# wedged the browser, so they are never tasks.
		("/dashboard/", "Explore now", False),
		("/earn", "Earn now", False),
		("https://rewards.bing.com/dashboard", "Go", False),
		("https://rewards.bing.com/about", "Go", False),
		("/about?section=benefits", "Learn more", False),
		("/redeem", "Redeem", False),
		("/refer", "Refer and Earn", False),
		("/", "Home", False),
		("/earn/quest/another", "Another", False),
		("https://www.bing.com/search?q=toys", "Learn more", False),
		("https://bingapp.microsoft.com/bing?adjust=1", "Get the app", False),
		("https://www.xbox.com/Rewards", "Xbox", False),
		("https://open.spotify.com/", "Spotify", False),
		("https://evil.example/https://www.bing.com/", "x", False),
		("https://www.bing.com.evil.example/", "x", False),
		("?modal=redeemcode", "Redeem your promo code", False),
		("#top", "Top", False),
		("javascript:void(0)", "x", False),
		("mailto:a@b.c", "x", False),
		("ftp://www.bing.com/x", "x", False),
		("", "x", False),
	]

	def test_the_table(self):
		for href, text, expected in self.CASES:
			with self.subTest(href=href, text=text):
				self.assertEqual(quests.is_task_link(href, text), expected)

	def test_the_rewards_about_page_is_never_a_task_because_it_reads_as_signed_out(self):
		self.assertFalse(quests.is_task_link("https://rewards.bing.com/about", "Go"))
		self.assertEqual(safety.classify("https://rewards.bing.com/about", "").kind, safety.SIGNED_OUT)


class TestPickTask(unittest.TestCase):
	def test_the_first_untried_task_link(self):
		toys, hats = "https://www.bing.com/search?q=toys", "https://www.bing.com/search?q=hats"
		links = [("/redeem", "Redeem"), (toys, "Toys"), ("/earn", "Earn now"), (hats, "Hats")]

		self.assertEqual(quests.pick_task(links, set()), (toys, "Toys"))
		self.assertEqual(quests.pick_task(links, {toys}), (hats, "Hats"))
		self.assertIsNone(quests.pick_task(links, {toys, hats}))

	def test_no_candidates(self):
		self.assertIsNone(quests.pick_task([], set()))


class Anchor:
	def __init__(self, href, text=""):
		self.href, self.text = href, text

	def get_dom_attribute(self, name):
		return self.href if name == "href" else None


class QuestPage:
	"""A RewardsTaskUtils with just the pieces complete_quests and work_quest use."""

	complete_quests = rewards_tasks.RewardsTaskUtils.complete_quests
	work_quest = rewards_tasks.RewardsTaskUtils.work_quest
	MAX_QUESTS = rewards_tasks.RewardsTaskUtils.MAX_QUESTS
	MAX_QUEST_TASKS = rewards_tasks.RewardsTaskUtils.MAX_QUEST_TASKS

	def __init__(self, cards, page_links=(), opens_new_tab=True, fail_quest=None):
		self.events = []
		self.cards = cards
		self.page_links = list(page_links)
		self.opens_new_tab = opens_new_tab
		self.fail_quest = fail_quest
		self.handles = ["main"]
		page = self

		class Driver:
			current_window_handle = "main"
			current_url = "https://rewards.bing.com/earn/quest/q1"
			switch_to = types.SimpleNamespace(window=lambda h: page.events.append(("window", h)))

			@property
			def window_handles(self):
				return list(page.handles)

			def get(self, url):
				page.events.append(("get", url))

		self.driver = Driver()
		self.elements = types.SimpleNamespace(
			get_quest_links=lambda: self.cards,
			get_quest_page_links=lambda: self.page_links,
		)
		self.mouse = mock.Mock()
		self.tab_utils = types.SimpleNamespace(
			switch_to_other_tab=lambda: self.events.append(("switch",)),
			close_all_other_tabs=lambda exceptions=None: (self.events.append(("close",)), self.handles.__setitem__(slice(None), ["main"])),
			ensure_focus=lambda: self.events.append(("focus",)),
		)

	def wait_for_element(self, getter, *args, **kwargs):
		return getter()

	def switch_to_earn_page(self):
		self.events.append(("earn",))

	def restore_main_tab(self):
		self.events.append(("restore",))

	def move_to_and_click(self, target):
		self.events.append(("click", target.href))

		if self.fail_quest and target.href == self.fail_quest:
			raise WebDriverException("click failed")

		if self.opens_new_tab and "/earn/quest/" not in target.href:
			self.handles.append("task")


@mock.patch.object(rewards_tasks.time, "sleep", lambda *_: None)
class TestCompleteQuests(unittest.TestCase):
	def _run(self, page):
		page.complete_quests()

		return [e for e in page.events if e[0] == "click"]

	def test_it_opens_only_the_quests_worth_doing(self):
		page = QuestPage(
			[
				Anchor("/earn/quest/ENWW_Oct_punchcard", "+50 0/5 tasks"),
				Anchor("/earn/quest/WW_Spotify_punchcard", "0/6 tasks"),
				Anchor("/earn/quest/done_punchcard", "4/4 tasks"),
				Anchor("/earn/quest/WW_onboarding_punchcard", "1/4 tasks"),
			],
		)
		clicks = [href for _, href in self._run(page)]

		self.assertEqual(clicks, ["/earn/quest/ENWW_Oct_punchcard", "/earn/quest/WW_onboarding_punchcard"])

	def test_no_quest_worth_doing_is_a_skip(self):
		page = QuestPage([Anchor("/earn/quest/WW_Spotify_punchcard", "0/6 tasks")])

		with self.assertRaises(NoSuchElementException):
			page.complete_quests()

	def test_only_max_quests_are_opened(self):
		page = QuestPage([Anchor(f"/earn/quest/q{i}_punchcard", "0/5 tasks") for i in range(9)])

		self.assertEqual(len(self._run(page)), QuestPage.MAX_QUESTS)

	def test_a_quest_that_fails_does_not_stop_the_next_one(self):
		page = QuestPage(
			[Anchor("/earn/quest/a_punchcard", "0/2 tasks"), Anchor("/earn/quest/b_punchcard", "0/2 tasks")],
			fail_quest="/earn/quest/a_punchcard",
		)
		clicks = [href for _, href in self._run(page)]

		self.assertEqual(clicks, ["/earn/quest/a_punchcard", "/earn/quest/b_punchcard"])

	def test_a_brake_trip_inside_a_quest_stops_everything(self):
		page = QuestPage([Anchor("/earn/quest/a_punchcard", "0/2 tasks"), Anchor("/earn/quest/b_punchcard", "0/2 tasks")])

		def trip(href, main_tab):
			raise safety.AccountAtRisk(safety.Risk(safety.CHALLENGE, "x"))

		page.work_quest = trip

		with self.assertRaises(safety.AccountAtRisk):
			page.complete_quests()

	def test_the_tab_state_is_restored_after_every_quest(self):
		page = QuestPage([Anchor("/earn/quest/a_punchcard", "0/2 tasks"), Anchor("/earn/quest/b_punchcard", "0/2 tasks")])
		self._run(page)

		self.assertEqual([e[0] for e in page.events].count("restore"), 2)


@mock.patch.object(rewards_tasks.time, "sleep", lambda *_: None)
class TestWorkQuest(unittest.TestCase):
	def _quest(self, page_links, **kwargs):
		page = QuestPage([Anchor("/earn/quest/q1", "0/5 tasks")], page_links=page_links, **kwargs)

		page.work_quest("/earn/quest/q1", "main")

		return page

	def test_a_task_that_opens_a_new_tab_is_followed_and_closed(self):
		page = self._quest([Anchor("https://www.bing.com/search?q=toys", "Shop toys")])
		kinds = [e[0] for e in page.events]

		self.assertEqual(kinds[kinds.index("click", 1):], ["click", "switch", "close", "window"])
		self.assertNotIn("get", kinds)

	def test_a_task_that_navigates_in_place_returns_to_the_quest_page(self):
		page = self._quest([Anchor("https://www.bing.com/search?q=toys", "Toys")], opens_new_tab=False)

		self.assertIn(("get", "https://rewards.bing.com/earn/quest/q1"), page.events)
		self.assertNotIn(("switch",), page.events)

	def test_in_site_links_are_never_clicked(self):
		page = self._quest([Anchor("/dashboard/", "Explore now"), Anchor("/earn", "Earn now")])

		self.assertEqual([e[1] for e in page.events if e[0] == "click"], ["/earn/quest/q1"])

	def test_each_task_link_is_done_once(self):
		links = [Anchor("https://www.bing.com/search?q=hats", "Hats"), Anchor("https://www.bing.com/search?q=toys", "Toys")]
		page = self._quest(links)
		clicked = [e[1] for e in page.events if e[0] == "click"]

		self.assertEqual(clicked, ["/earn/quest/q1", "https://www.bing.com/search?q=hats", "https://www.bing.com/search?q=toys"])

	def test_navigation_and_app_links_are_never_clicked(self):
		page = self._quest([
			Anchor("/redeem", "Redeem"), Anchor("/about?section=benefits", "Learn more"),
			Anchor("https://bingapp.microsoft.com/bing", "Get the app"), Anchor("/faq", "FAQ"),
		])

		self.assertEqual([e[1] for e in page.events if e[0] == "click"], ["/earn/quest/q1"])

	def test_it_stops_after_max_tasks(self):
		links = [Anchor(f"https://www.bing.com/search?q=t{i}", "x") for i in range(20)]
		page = self._quest(links)

		self.assertEqual(len([e for e in page.events if e[0] == "click"]) - 1, QuestPage.MAX_QUEST_TASKS)

	def test_a_quest_missing_from_the_page_is_skipped(self):
		page = QuestPage([Anchor("/earn/quest/other", "0/5 tasks")], page_links=[Anchor("/dashboard/", "Go")])
		page.work_quest("/earn/quest/q1", "main")

		self.assertEqual(page.events, [])


class PointsPage:
	complete_all_tasks = rewards_tasks.RewardsTaskUtils.complete_all_tasks
	today_points = rewards_tasks.RewardsTaskUtils.today_points
	account_name = "tester"
	NAMES = (
		"complete_bing_daily_set", "complete_explore_on_bing_tasks", "complete_visual_search",
		"complete_misc_cards", "complete_required_searches", "claim_bonus_points", "complete_quests",
	)

	def __init__(self, readings):
		self.readings = list(readings)
		self.driver = types.SimpleNamespace(current_url="https://rewards.bing.com/", execute_script=lambda s: "")
		self.tab_utils = types.SimpleNamespace(close_all_other_tabs=lambda: None)

		for name in self.NAMES:
			setattr(self, name, lambda: None)

	def read_points_summary(self):
		value = self.readings.pop(0)

		if isinstance(value, Exception):
			raise value

		return {"today": value}

	def restore_main_tab(self):
		pass

	def return_to_rewards_home(self):
		pass

	def brake_if_risky(self):
		pass


class TestPerStepPoints(unittest.TestCase):
	def _run(self, readings, env=None):
		page = PointsPage(readings)

		with mock.patch.dict(os.environ, env or {}), self.assertLogs(rewards_tasks.logger, level="INFO") as logs:
			page.complete_all_tasks()

		return page, "\n".join(logs.output)

	def test_each_task_reports_what_it_paid(self):
		page, output = self._run([100, 100, 140, 140, 150, 450, 450, 465])

		self.assertIn("[POINTS] Bing daily set: +0 (today 100)", output)
		self.assertIn("[POINTS] Explore on Bing: +40 (today 140)", output)
		self.assertIn("[POINTS] Required searches: +300 (today 450)", output)
		self.assertIn("[POINTS] Quests: +15 (today 465)", output)

	def test_a_reading_that_fails_does_not_fail_the_task_or_stop_the_run(self):
		page, output = self._run([100, WebDriverException("panel"), 120, 120, 120, 120, 120, 120])

		self.assertNotIn("[FAIL]", output)
		self.assertEqual(page.readings, [])

	def test_it_can_be_turned_off(self):
		page, output = self._run([], env={"REWARDS_TASK_POINTS": "0"})

		self.assertNotIn("[POINTS]", output)

	def test_a_page_that_needs_a_human_is_not_swallowed_as_an_unreadable_panel(self):
		page = PointsPage([safety.AccountAtRisk(safety.Risk(safety.CHALLENGE, "x"))])

		with self.assertRaises(safety.AccountAtRisk):
			page.today_points()


if __name__ == "__main__":
	unittest.main()
