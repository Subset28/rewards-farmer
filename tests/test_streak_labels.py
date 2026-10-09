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
