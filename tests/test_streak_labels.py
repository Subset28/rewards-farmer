import os
import sys
import unittest
from unittest import mock

from selenium.common.exceptions import NoSuchElementException

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import element_selectors


def button(text):
	return mock.Mock(text=text)


class TestStreakLabels(unittest.TestCase):
	def test_it_lists_the_visible_text_of_each_button_on_one_line(self):
		section = mock.Mock()
		section.find_elements.return_value = [button("Daily set streak\n 7 days"), button(""), button("Bing   streak")]
		driver = mock.Mock()
		driver.find_element.return_value = section

		self.assertEqual(element_selectors.ElementSelectionUtils(driver).streak_labels(), ["Daily set streak 7 days", "Bing streak"])

	def test_a_page_without_the_section_gives_an_empty_list_rather_than_an_error(self):
		driver = mock.Mock()
		driver.find_element.side_effect = NoSuchElementException("no streaks")

		self.assertEqual(element_selectors.ElementSelectionUtils(driver).streak_labels(), [])


class TestVisualSearchEntry(unittest.TestCase):
	def selectors(self, texts):
		section = mock.Mock()
		section.find_elements.return_value = [button(t) for t in texts]
		driver = mock.Mock()
		driver.find_elements.return_value = []
		driver.find_element.return_value = section

		return element_selectors.ElementSelectionUtils(driver), section

	def test_a_plainly_worded_entry_in_the_streaks_section_is_found(self):
		selectors, section = self.selectors(["Daily set streak", "Visual search"])

		self.assertEqual(selectors.get_open_visual_search_sidebar().text, "Visual search")

	def test_the_position_in_streaks_is_still_the_last_resort(self):
		selectors, section = self.selectors(["Daily set streak"])
		section.find_element.return_value = "fifth"

		self.assertEqual(selectors.get_open_visual_search_sidebar(), "fifth")

	def test_a_button_outside_the_streaks_container_is_found_by_its_text(self):
		# 10-09 on the real page: "Visual Search  How to activate" was on the page but not inside #streaks.
		selectors, section = self.selectors(["Daily streak 14 days"])
		selectors.driver.find_elements.return_value = [button("Daily streak 14 days"), button("Visual Search\n How to activate")]
		section.find_element.return_value = "fifth"

		self.assertEqual(selectors.get_open_visual_search_sidebar().text, "Visual Search\n How to activate")


class TestVisualSearchTask(unittest.TestCase):
	"""The task as a whole, on a fake page: a locked tile is a quiet skip, a pop-up in the way is closed first."""

	def tasks(self, entry_text):
		import types
		import rewards_tasks

		page = types.SimpleNamespace()
		page.clicked = []
		page.elements = mock.Mock()
		page.elements.get_open_visual_search_sidebar.return_value = mock.Mock(text=entry_text)
		page.open_streaks_section = lambda: None
		page.switch_to_earn_page = lambda: None
		page.wait_for_then_click = lambda getter, timeout=10: page.clicked.append(getter)
		page.tab_utils = mock.Mock()
		page.complete_visual_search = rewards_tasks.RewardsTaskUtils.complete_visual_search.__get__(page)

		return page

	def run_task(self, page):
		import rewards_tasks

		with mock.patch.object(rewards_tasks.os.path, "exists", return_value=True):
			page.complete_visual_search()

	def test_a_locked_tile_is_skipped_without_clicking_it(self):
		page = self.tasks("Visual Search Streak\nHow to activate")

		with self.assertRaises(NoSuchElementException) as caught:
			self.run_task(page)

		self.assertIn("locked", str(caught.exception))
		self.assertEqual(page.clicked, [])

	def test_an_unlocked_tile_is_clicked(self):
		page = self.tasks("Visual Search Streak\n3 / 7")

		with self.assertRaises(Exception):
			self.run_task(page)  # runs on into the later steps of a fake page; only the first click matters here

		self.assertTrue(page.clicked)


class TestCelebrationPopup(unittest.TestCase):
	def tasks(self, dialogs):
		import types
		import rewards_tasks

		page = types.SimpleNamespace()
		page.driver = mock.Mock()
		page.driver.find_elements.return_value = dialogs
		page.clicks = []
		page.move_to_and_click = lambda el: page.clicks.append(el)
		page.close_celebration = rewards_tasks.RewardsTaskUtils.close_celebration.__get__(page)

		return page

	def test_the_quest_completed_popup_is_closed_by_its_close_button(self):
		close = mock.Mock()
		dialog = mock.Mock(is_displayed=mock.Mock(return_value=True))
		dialog.find_element.return_value = close
		page = self.tasks([dialog])

		with mock.patch("rewards_tasks._wait"):
			page.close_celebration()

		self.assertEqual(page.clicks, [close])

	def test_a_hidden_popup_or_none_is_left_alone(self):
		hidden = mock.Mock(is_displayed=mock.Mock(return_value=False))

		for dialogs in ([], [hidden]):
			page = self.tasks(dialogs)
			page.close_celebration()

			self.assertEqual(page.clicks, [])

	def test_it_only_looks_for_the_celebration_wording(self):
		page = self.tasks([])
		page.close_celebration()
		xpath = page.driver.find_elements.call_args.args[1]

		self.assertIn("Nice work", xpath)
		self.assertNotIn("sign", xpath.lower())
