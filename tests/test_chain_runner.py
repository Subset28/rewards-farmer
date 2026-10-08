"""Searching in tangents: the runner follows the page, reads between searches, and backs off when memory runs short."""

import os
import random
import sys
import tempfile
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import memory_guard
import query_history
import reading
import rewards_tasks
from selenium.common.exceptions import WebDriverException


class Link:
	def __init__(self, text):
		self.text = text


class Driver:
	def __init__(self):
		self.handles = ["main"]
		self.current_window_handle = "main"
		self.current_url = "https://www.bing.com/search?q=x"
		self.visited = []
		self.backs = 0
		self.timeouts = []
		self.switch_to = types.SimpleNamespace(window=lambda handle: setattr(self, "current_window_handle", handle))

	@property
	def window_handles(self):
		return list(self.handles)

	def get(self, url):
		self.visited.append(url)
		self.current_url = url

	def back(self):
		self.backs += 1
		self.current_url = "https://www.bing.com/search?q=x"

	def set_page_load_timeout(self, seconds):
		self.timeouts.append(seconds)


class Page:
	"""Just enough of RewardsTaskUtils to run a tangent, borrowing the real methods."""

	account_name = "second"

	for _name in ("run_chain_batch", "_search_from_home", "_type_query", "related_texts", "_follow_up", "read_results", "_open_a_result", "relieve_memory"):
		locals()[_name] = getattr(rewards_tasks.RewardsTaskUtils, _name)

	def __init__(self, related_per_page=("vitamin c foods", "vitamin c deficiency symptoms", "scurvy", "how much vitamin c per day"), organic=2):
		self.driver = Driver()
		self.typed = []
		self.clicks = []
		self.related_per_page = list(related_per_page)
		self.organic = organic
		self.tab_utils = types.SimpleNamespace(
			ensure_focus=lambda: None,
			switch_to_other_tab=lambda: setattr(self.driver, "current_window_handle", "other"),
			close_all_other_tabs=lambda exceptions=None: self.driver.handles.__setitem__(slice(None), ["main"]),
		)
		self.elements = types.SimpleNamespace(
			get_bing_search_bar=lambda: object(),
			get_related_searches=lambda: [Link(t) for t in self.related_per_page],
			get_organic_results=lambda: [Link(f"result {n}") for n in range(self.organic)],
			get_serp_search_box=lambda: Link("box"),
		)
		self.keyboard = types.SimpleNamespace(
			send_keys=lambda text, **kw: self.typed.append(kw.get("intended", text)),
			slip_settings=lambda text=None: (0.0, 0.5),
			slip_weights=lambda text: None,
		)
		self.mouse = types.SimpleNamespace(read_page=lambda steps: self.read.append(steps))
		self.pace = types.SimpleNamespace(active=lambda: False, factor=lambda: 1.0)
		self.read = []

	def wait_for_element(self, getter, *a, **k):
		return getter()

	def move_to_and_click(self, target, *a, **k):
		self.clicks.append(target.text)

		if target.text.startswith("result"):
			self.driver.handles = ["main", "other"]
			self.driver.current_url = "https://example.org/page"
		elif target.text != "box":
			# Clicking a related search makes the clicked text the new query.
			self.typed.append(target.text)


class Case(unittest.TestCase):
	def setUp(self):
		directory = tempfile.TemporaryDirectory()
		self.addCleanup(directory.cleanup)

		for patcher in (
			mock.patch.object(query_history, "HISTORY_FILE", os.path.join(directory.name, "history.jsonl")),
			mock.patch.object(rewards_tasks.queries, "tangent_starts", side_effect=lambda count, account=None, rng=random: ["why do i need vitamin c", "best pizza near me", "nba scores"][:count]),
			mock.patch.object(rewards_tasks.queries, "related_queries", return_value=["fallback one", "fallback two", "fallback three"]),
			mock.patch.object(rewards_tasks.time, "sleep"),
			mock.patch.object(rewards_tasks, "_wait"),
			mock.patch.object(rewards_tasks.memory_guard, "fraction", return_value=0.3),
			mock.patch.object(rewards_tasks, "ActionChains", mock.MagicMock()),
		):
			patcher.start()
			self.addCleanup(patcher.stop)

		random.seed(5)


class TestTheRunner(Case):
	def test_exactly_the_number_of_searches_asked_for_is_made(self):
		for count in (1, 3, 6, 9):
			page = Page()
			page.run_chain_batch(count)

			self.assertEqual(len(page.typed), count, count)

	def test_a_thread_starts_from_the_homepage_and_continues_from_the_results_page(self):
		page = Page()
		page.run_chain_batch(8)
		homepage_visits = [u for u in page.driver.visited if u == "https://www.bing.com/"]

		# Fewer homepage loads than searches: most searches continue a thread on the results page.
		self.assertLess(len(homepage_visits), 8)
		self.assertGreaterEqual(len(homepage_visits), 1)

	def test_no_search_is_repeated(self):
		page = Page()
		page.run_chain_batch(9)
		typed = [t.lower() for t in page.typed]

		self.assertEqual(len(typed), len(set(typed)))

	def test_follow_ups_come_from_what_the_page_offered(self):
		page = Page()
		page.run_chain_batch(8)
		starts = {"why do i need vitamin c", "best pizza near me", "nba scores", "fallback one", "fallback two", "fallback three"}
		follow_ups = [t for t in page.typed if t.lower() not in starts]

		self.assertTrue(follow_ups)
		self.assertTrue(all(any(o in t.lower() or t.lower() in o for o in page.related_per_page) for t in follow_ups))

	def test_the_results_are_read_between_searches(self):
		page = Page()
		page.run_chain_batch(6)

		self.assertGreaterEqual(len(page.read), 2)
		self.assertTrue(all(steps and steps[0][0] in ("scroll", "wait", "drift") for steps in page.read))

	def test_every_search_is_remembered_so_it_is_not_repeated_for_a_month(self):
		page = Page()
		page.run_chain_batch(6)

		self.assertGreaterEqual(len(query_history.recent("second")), 4)

	def test_a_page_with_no_related_searches_ends_the_thread_instead_of_inventing_one(self):
		page = Page(related_per_page=())
		page.run_chain_batch(5)

		self.assertEqual(len(page.typed), 5)
		self.assertGreaterEqual(len([u for u in page.driver.visited if u == "https://www.bing.com/"]), 3)

	def test_the_run_ends_on_the_rewards_home_page(self):
		page = Page()
		page.run_chain_batch(3)

		self.assertEqual(page.driver.visited[-1], rewards_tasks.REWARDS_HOME_URL)

	def test_with_the_switch_off_the_original_loop_runs_and_not_this_one(self):
		page = Page()
		page.run_search_batch = types.MethodType(rewards_tasks.RewardsTaskUtils.run_search_batch, page)
		page.run_chain_batch = mock.Mock()
		page.browse_results = lambda: None

		with mock.patch.dict(os.environ, {"REWARDS_FEATURES": "typing"}), mock.patch.object(rewards_tasks.queries, "related_queries", return_value=["a b"]):
			page.run_search_batch(1)

		page.run_chain_batch.assert_not_called()

	def test_with_the_switch_on_this_loop_runs(self):
		page = Page()
		page.run_search_batch = types.MethodType(rewards_tasks.RewardsTaskUtils.run_search_batch, page)
		page.run_chain_batch = mock.Mock()

		with mock.patch.dict(os.environ, {"REWARDS_FEATURES": "chains"}):
			page.run_search_batch(4)

		page.run_chain_batch.assert_called_once_with(4)


class TestMemory(Case):
	def test_a_nearly_full_container_ends_the_run_early_and_cleanly(self):
		page = Page()

		with mock.patch.object(rewards_tasks.memory_guard, "fraction", return_value=0.93):
			page.run_chain_batch(8)

		self.assertEqual(page.typed, [])
		self.assertEqual(page.driver.visited[-1], rewards_tasks.REWARDS_HOME_URL)

	def test_no_result_is_opened_when_memory_is_already_high(self):
		page = Page()

		with mock.patch.object(rewards_tasks.chains, "opens_a_result", return_value=True), \
			mock.patch.object(rewards_tasks.memory_guard, "fraction", return_value=0.70):
			page.run_chain_batch(6)

		self.assertFalse([c for c in page.clicks if c.startswith("result")])

	def test_a_result_is_opened_when_there_is_room_and_it_is_the_persons_way(self):
		page = Page()

		with mock.patch.object(rewards_tasks.chains, "opens_a_result", return_value=True):
			page.run_chain_batch(4)

		self.assertTrue([c for c in page.clicks if c.startswith("result")])

	def test_the_page_is_blanked_between_tangents_when_memory_has_grown(self):
		page = Page(related_per_page=())

		with mock.patch.object(rewards_tasks.memory_guard, "fraction", return_value=0.78):
			page.run_chain_batch(4)

		self.assertIn("about:blank", page.driver.visited)

	def test_no_blanking_when_memory_is_fine(self):
		page = Page(related_per_page=())
		page.run_chain_batch(4)

		self.assertNotIn("about:blank", page.driver.visited)


class TestAnOpenedResult(Case):
	def test_the_browser_comes_back_to_the_results_and_the_timeout_is_restored(self):
		page = Page()
		page._open_a_result(1.0)

		self.assertEqual(page.driver.current_window_handle, "main")
		self.assertEqual(page.driver.handles, ["main"])
		self.assertEqual(page.driver.timeouts[0], 30)
		self.assertEqual(page.driver.timeouts[-1], 300)

	def test_a_page_that_misbehaves_does_not_leave_the_browser_stranded(self):
		page = Page()
		page.mouse.read_page = mock.Mock(side_effect=WebDriverException("renderer crashed"))

		with self.assertRaises(WebDriverException):
			page._open_a_result(1.0)

		self.assertEqual(page.driver.current_window_handle, "main")
		self.assertEqual(page.driver.handles, ["main"])
		self.assertEqual(page.driver.timeouts[-1], 300)

	def test_reading_the_results_survives_that_failure(self):
		page = Page()
		page.mouse.read_page = mock.Mock(side_effect=WebDriverException("renderer crashed"))

		with mock.patch.object(rewards_tasks.chains, "opens_a_result", return_value=False):
			page.read_results(1.0)

	def test_a_results_page_with_no_results_opens_nothing(self):
		page = Page(organic=0)
		page._open_a_result(1.0)

		self.assertEqual(page.clicks, [])


class TestTheMemoryReading(unittest.TestCase):
	def paths(self, files):
		directory = tempfile.TemporaryDirectory()
		self.addCleanup(directory.cleanup)

		for name, text in files.items():
			full = os.path.join(directory.name, name)
			os.makedirs(os.path.dirname(full), exist_ok=True)

			with open(full, "w", encoding="ascii") as handle:
				handle.write(text)

		return {"v1": os.path.join(directory.name, "v1"), "v2": os.path.join(directory.name, "v2")}

	def test_cgroup_v1_counts_resident_and_shared_memory_not_cache(self):
		paths = self.paths({
			"v1/memory.limit_in_bytes": str(1536 * 2**20),
			"v1/memory.stat": f"total_cache {900 * 2**20}\ntotal_rss {700 * 2**20}\ntotal_shmem {100 * 2**20}\n",
		})

		self.assertAlmostEqual(memory_guard.fraction(**paths), 800 / 1536, places=3)

	def test_cgroup_v2(self):
		paths = self.paths({
			"v2/memory.max": str(2000 * 2**20),
			"v2/memory.stat": f"anon {1000 * 2**20}\nshmem {200 * 2**20}\nfile {500 * 2**20}\n",
		})

		self.assertAlmostEqual(memory_guard.fraction(**paths), 0.6, places=3)

	def test_no_limit_or_no_files_means_no_pressure(self):
		unlimited = self.paths({"v2/memory.max": "max\n", "v2/memory.stat": "anon 5\n"})

		self.assertIsNone(memory_guard.fraction(**unlimited))
		self.assertTrue(memory_guard.can_open_a_result(**unlimited))
		self.assertFalse(memory_guard.needs_relief(**unlimited))
		self.assertFalse(memory_guard.must_stop(**unlimited))
		self.assertIsNone(memory_guard.fraction(v1="/nonexistent", v2="/nonexistent"))

	def test_the_three_thresholds_are_in_order(self):
		self.assertLess(memory_guard.OPEN_RESULT_BELOW, memory_guard.RELIEF_ABOVE)
		self.assertLess(memory_guard.RELIEF_ABOVE, memory_guard.STOP_ABOVE)

	def test_each_decision_flips_at_its_threshold(self):
		limit = 1000

		def paths(held):
			return self.paths({"v2/memory.max": str(limit), "v2/memory.stat": f"anon {held}\nshmem 0\n"})

		self.assertTrue(memory_guard.can_open_a_result(**paths(600)))
		self.assertFalse(memory_guard.can_open_a_result(**paths(640)))
		self.assertFalse(memory_guard.needs_relief(**paths(700)))
		self.assertTrue(memory_guard.needs_relief(**paths(760)))
		self.assertFalse(memory_guard.must_stop(**paths(860)))
		self.assertTrue(memory_guard.must_stop(**paths(900)))


class TestTheReadingPlan(unittest.TestCase):
	def test_it_fills_about_the_time_asked(self):
		for budget in (10, 25, 60):
			steps = reading.plan(budget, random.Random(budget))

			self.assertTrue(budget * 0.8 <= reading.duration(steps) <= budget * 1.6 + 6, (budget, reading.duration(steps)))

	def test_scrolling_is_in_whole_wheel_ticks_in_short_bursts(self):
		steps = reading.plan(60, random.Random(1))
		scrolls = [s[1] for s in steps if s[0] == "scroll"]

		self.assertTrue(all(abs(dy) % reading.WHEEL_TICK == 0 for dy in scrolls))
		self.assertGreater(sum(1 for dy in scrolls if dy > 0), sum(1 for dy in scrolls if dy < 0))

	def test_it_sometimes_goes_back_up_and_sometimes_the_hand_drifts(self):
		kinds = {s[0] for _ in range(20) for s in reading.plan(60, random.Random(_))}

		self.assertEqual(kinds, {"scroll", "wait", "drift"})
		self.assertTrue(any(s[0] == "scroll" and s[1] < 0 for _ in range(20) for s in reading.plan(60, random.Random(_))))


if __name__ == "__main__":
	unittest.main()
