"""Tests for how the required searches are sized, capped and browsed.

	python -m unittest discover -s tests
"""

import os
import sys
import unittest
from unittest import mock

SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src")
sys.path.insert(0, SRC)

from selenium.common.exceptions import NoSuchElementException, ElementClickInterceptedException
from selenium.webdriver.common.by import By

import element_selectors
import rewards_tasks
import search_only

Tasks = rewards_tasks.RewardsTaskUtils


class Quota:
	"""A stand-in for the RewardsTaskUtils methods complete_required_searches calls.

	Earns `rate` points per search, up to `cap`, and records every batch size.
	"""

	PROBE_SEARCHES = Tasks.PROBE_SEARCHES
	account_name = "tester"
	complete_required_searches = Tasks.complete_required_searches

	def __init__(self, earned=0, cap=50, rate=5, lag_reads=0):
		self.earned, self.cap, self.rate = earned, cap, rate
		self.batches = []
		# Points a batch earned that the page only shows after this many reads.
		self.lag_reads, self.pending, self.reads_left = lag_reads, 0, 0

	def read_search_points(self):
		if self.pending:
			if self.reads_left <= 0:
				self.earned = min(self.cap, self.earned + self.pending)
				self.pending = 0
			else:
				self.reads_left -= 1

		return self.earned, self.cap

	def run_search_batch(self, count):
		self.batches.append(count)
		gained = count * self.rate

		if self.lag_reads:
			self.pending, self.reads_left = gained, self.lag_reads
		else:
			self.earned = min(self.cap, self.earned + gained)


class TestSizing(unittest.TestCase):
	def test_fills_a_fifty_point_quota_in_ten_searches_not_fifteen(self):
		quota = Quota(earned=0, cap=50, rate=5)
		quota.complete_required_searches()

		self.assertEqual(quota.earned, 50)
		self.assertEqual(quota.batches, [3, 7])
		self.assertEqual(sum(quota.batches), 10)

	def test_a_three_point_market_is_measured_not_assumed(self):
		quota = Quota(earned=0, cap=30, rate=3)
		quota.complete_required_searches()

		self.assertEqual(quota.earned, 30)
		self.assertEqual(sum(quota.batches), 10)

	def test_nothing_to_do_when_the_quota_is_already_full(self):
		quota = Quota(earned=50, cap=50)
		quota.complete_required_searches()

		self.assertEqual(quota.batches, [])

	def test_only_the_remainder_is_searched(self):
		quota = Quota(earned=35, cap=50, rate=5)
		quota.complete_required_searches()

		self.assertEqual(sum(quota.batches), 3)


class TestRunLimit(unittest.TestCase):
	def test_a_run_stops_at_its_limit_and_leaves_the_rest(self):
		quota = Quota(earned=0, cap=50, rate=5)

		with self.assertLogs(rewards_tasks.logger, level="INFO") as logs:
			quota.complete_required_searches(max_searches=6)

		self.assertEqual(sum(quota.batches), 6)
		self.assertEqual(quota.earned, 30)
		self.assertTrue(any("stopped at its limit" in line for line in logs.output))

	def test_a_limit_above_what_is_needed_does_not_add_searches(self):
		quota = Quota(earned=0, cap=50, rate=5)
		quota.complete_required_searches(max_searches=40)

		self.assertEqual(sum(quota.batches), 10)

	def test_a_run_that_reaches_the_quota_within_its_limit_reports_it_complete(self):
		quota = Quota(earned=40, cap=50, rate=5)

		with self.assertLogs(rewards_tasks.logger, level="INFO") as logs:
			quota.complete_required_searches(max_searches=8)

		self.assertTrue(any("quota complete" in line for line in logs.output))


@mock.patch.object(rewards_tasks.time, "sleep", lambda *_: None)
class TestNoPoints(unittest.TestCase):
	def test_points_that_show_up_late_are_not_reported_as_a_restriction(self):
		# The page showed no gain straight after the searches, and the points
		# arrived on the next read. That is a slow update, not a restriction.
		quota = Quota(earned=0, cap=50, rate=5, lag_reads=1)

		with mock.patch.object(rewards_tasks.notify, "send") as alert:
			quota.complete_required_searches()

		alert.assert_not_called()
		self.assertEqual(quota.earned, 50)

	def test_points_that_never_arrive_are_still_reported(self):
		quota = Quota(earned=0, cap=50, rate=5, lag_reads=99)

		with mock.patch.object(rewards_tasks.notify, "send") as alert, 			self.assertLogs(rewards_tasks.logger, level="WARNING"):
			quota.complete_required_searches()

		alert.assert_called_once()

	def test_the_second_look_waits_before_reading_again(self):
		quota = Quota(earned=0, cap=50, rate=0)
		waits = []

		with mock.patch.object(rewards_tasks.time, "sleep", waits.append), 			mock.patch.object(rewards_tasks.notify, "send"), 			self.assertLogs(rewards_tasks.logger, level="WARNING"):
			quota.complete_required_searches()

		self.assertTrue(any(20 <= w <= 35 for w in waits))

	def test_searches_that_earn_nothing_stop_the_run_with_a_warning(self):
		quota = Quota(earned=0, cap=50, rate=0)

		with mock.patch.object(rewards_tasks.notify, "send") as alert, 			self.assertLogs(rewards_tasks.logger, level="WARNING") as logs:
			quota.complete_required_searches()

		self.assertEqual(quota.batches, [3])
		self.assertTrue(any("earned no points" in line for line in logs.output))
		alert.assert_called_once()
		self.assertIn("tester", alert.call_args.args[1])


class Browsing:
	RESULTS_TAB_RATE = Tasks.RESULTS_TAB_RATE
	RESULTS_SCROLL_RATE = Tasks.RESULTS_SCROLL_RATE
	browse_results = Tasks.browse_results

	def __init__(self, tab_error=None, click_error=None):
		self.clicked, self.scrolled, self.tabs_asked = 0, 0, []
		self.tab_error, self.click_error = tab_error, click_error
		self.elements = mock.Mock()
		self.elements.get_search_results_tab.side_effect = self._tab
		self.mouse = mock.Mock()
		self.mouse.wheel_scroll_read.side_effect = self._scroll
		self.driver = mock.Mock(current_window_handle="main", window_handles=["main", "extra"])
		self.tab_utils = mock.Mock()

	def _tab(self, name):
		self.tabs_asked.append(name)

		if self.tab_error:
			raise self.tab_error

		return object()

	def _scroll(self):
		self.scrolled += 1

	def move_to_and_click(self, element):
		if self.click_error:
			raise self.click_error

		self.clicked += 1


@mock.patch.object(rewards_tasks.time, "sleep", lambda *_: None)
class TestBrowseResults(unittest.TestCase):
	def test_a_low_roll_opens_a_results_tab(self):
		page = Browsing()

		with mock.patch.object(rewards_tasks.random, "random", return_value=0.05):
			page.browse_results()

		self.assertEqual(page.clicked, 1)
		self.assertEqual(page.scrolled, 0)
		self.assertIn(page.tabs_asked[0], ("images", "videos", "news"))

	def test_a_middle_roll_scrolls_the_results(self):
		page = Browsing()

		with mock.patch.object(rewards_tasks.random, "random", side_effect=[0.5, 0.5]):
			page.browse_results()

		self.assertEqual((page.clicked, page.scrolled), (0, 1))

	def test_a_high_roll_leaves_the_page_alone(self):
		page = Browsing()

		with mock.patch.object(rewards_tasks.random, "random", side_effect=[0.5, 0.95]):
			page.browse_results()

		self.assertEqual((page.clicked, page.scrolled), (0, 0))

	def test_a_results_tab_is_followed_then_closed_and_the_main_tab_restored(self):
		page = Browsing()

		with mock.patch.object(rewards_tasks.random, "random", return_value=0.05):
			page.browse_results()

		page.tab_utils.switch_to_other_tab.assert_called_once()
		page.tab_utils.close_all_other_tabs.assert_called_once_with(exceptions=["main"])
		page.driver.switch_to.window.assert_called_with("main")

	def test_a_failed_click_still_leaves_one_tab_and_the_main_one_in_front(self):
		page = Browsing(click_error=ElementClickInterceptedException("covered"))

		with mock.patch.object(rewards_tasks.random, "random", return_value=0.05):
			page.browse_results()

		page.tab_utils.close_all_other_tabs.assert_called_once_with(exceptions=["main"])
		page.driver.switch_to.window.assert_called_with("main")

	def test_a_missing_tab_is_skipped_not_an_error(self):
		page = Browsing(tab_error=NoSuchElementException("no tab"))

		with mock.patch.object(rewards_tasks.random, "random", return_value=0.05):
			page.browse_results()

		self.assertEqual(page.clicked, 0)

	def test_an_intercepted_click_is_skipped_not_an_error(self):
		page = Browsing(click_error=ElementClickInterceptedException("covered"))

		with mock.patch.object(rewards_tasks.random, "random", return_value=0.05):
			page.browse_results()


class TestSearchesThisRun(unittest.TestCase):
	def _with(self, value):
		return mock.patch.dict(os.environ, {"REWARDS_SEARCHES_PER_RUN": value})

	def test_a_fixed_range(self):
		with self._with("3-3"):
			self.assertEqual(search_only.searches_this_run(), 3)

	def test_a_reversed_range_is_still_a_range(self):
		with self._with("8-5"):
			for _ in range(50):
				self.assertIn(search_only.searches_this_run(), range(5, 9))

	def test_a_malformed_value_falls_back_to_the_default(self):
		with self._with("lots"):
			for _ in range(50):
				self.assertIn(search_only.searches_this_run(), range(5, 9))

	def test_the_default_is_five_to_eight(self):
		with mock.patch.dict(os.environ):
			os.environ.pop("REWARDS_SEARCHES_PER_RUN", None)

			for _ in range(50):
				self.assertIn(search_only.searches_this_run(), range(5, 9))


class TestResultsTabSelector(unittest.TestCase):
	def test_each_tab_uses_the_id_bing_gives_it(self):
		driver = mock.Mock()
		selectors = element_selectors.ElementSelectionUtils(driver)

		for name, expected in (
			("images", "#b-scopeListItem-images a"),
			("videos", "#b-scopeListItem-video a"),
			("news", "#b-scopeListItem-news a"),
		):
			selectors.get_search_results_tab(name)
			driver.find_element.assert_called_with(By.CSS_SELECTOR, expected)


if __name__ == "__main__":
	unittest.main()
