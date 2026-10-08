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
