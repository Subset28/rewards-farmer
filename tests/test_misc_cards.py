"""Tests for how the misc cards on the earn page are opened.

	python -m unittest discover -s tests
"""

import os
import sys
import unittest
from unittest import mock

SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src")
sys.path.insert(0, SRC)

import rewards_tasks

Tasks = rewards_tasks.RewardsTaskUtils


class Page:
	"""Just enough of RewardsTaskUtils for complete_misc_cards, recording what it does."""

	complete_misc_cards = Tasks.complete_misc_cards

	def __init__(self, cards):
		self.events = []
		self.driver = mock.Mock(current_window_handle="main")
		self.elements = mock.Mock()
		self.elements.get_all_misc_cards.side_effect = lambda: [c for c in cards]
		self.elements.card_is_complete.side_effect = lambda c: c.get("complete", False)
		self.elements.get_card_point_value.side_effect = lambda c: c.get("points", 0)
		self.elements.extract_card_descriptions.side_effect = lambda c: c.get("name", "card")
		self.mouse = mock.Mock()
		self.mouse.wheel_scroll_element_into_view.side_effect = lambda c: self.events.append(("scroll", c["name"]))
		self.mouse.wheel_scroll_to_top.side_effect = lambda: None
		self.tab_utils = mock.Mock()
		self.tab_utils.switch_to_other_tab.side_effect = lambda: self.events.append(("switch",))
		self.tab_utils.close_all_other_tabs.side_effect = lambda exceptions=None: self.events.append(("close",))

	def switch_to_earn_page(self):
		pass

	def wait_for_element(self, getter, *args, **kwargs):
		return getter()

	def move_to_and_click(self, card):
		self.events.append(("click", card["name"]))


class TestMiscCards(unittest.TestCase):
	def _run(self, cards):
		page = Page(cards)
		sleeps = []

		def record_sleep(seconds):
			sleeps.append(seconds)
			page.events.append(("sleep", seconds))

		with mock.patch.object(rewards_tasks.time, "sleep", record_sleep):
			page.complete_misc_cards()

		return page, sleeps

	def test_a_card_is_opened_focused_and_left_for_a_few_seconds_before_closing(self):
		page, _ = self._run([{"name": "bird", "points": 15}])
		kinds = [e[0] for e in page.events]

		self.assertEqual(kinds[kinds.index("click"):kinds.index("close") + 1], ["click", "sleep", "switch", "sleep", "close"])

		after_switch = page.events[kinds.index("switch") + 1]
		self.assertEqual(after_switch[0], "sleep")
		self.assertGreaterEqual(after_switch[1], 3)
		self.assertLessEqual(after_switch[1], 6)

	def test_a_finished_card_is_left_alone(self):
		page, _ = self._run([{"name": "done", "points": 15, "complete": True}])

		self.assertNotIn("click", [e[0] for e in page.events])
		self.assertNotIn("switch", [e[0] for e in page.events])

	def test_a_card_worth_nothing_is_left_alone(self):
		page, _ = self._run([{"name": "promo", "points": 0}])

		self.assertNotIn("click", [e[0] for e in page.events])

	def test_every_open_card_gets_the_same_treatment(self):
		page, _ = self._run([{"name": "a", "points": 15}, {"name": "b", "points": 5}])
		kinds = [e[0] for e in page.events]

		self.assertEqual(kinds.count("click"), 2)
		self.assertEqual(kinds.count("switch"), 2)


if __name__ == "__main__":
	unittest.main()
