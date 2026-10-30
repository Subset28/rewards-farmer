"""Tests for Microsoft Rewards perks automation, including 2x points multipliers, partner promotions, and bonus offers."""

import os
import sys
import types
import unittest
from unittest import mock

SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src")
if SRC not in sys.path:
	sys.path.insert(0, SRC)

import element_selectors
import rewards_tasks

Tasks = rewards_tasks.RewardsTaskUtils


class TestPerksSelectors(unittest.TestCase):
	def test_get_perk_cards_matches_2x_and_promotions(self):
		mock_driver = mock.Mock()

		# Card 1: 2x multiplier perk
		card1 = mock.Mock()
		card1.tag_name = "span"
		card1.text = "Perk unlocked\nFor a limited time, search 2x more per day to earn more points.\nClaim offer"
		card1.get_attribute.side_effect = lambda attr: "card1" if attr == "id" else ("link" if attr == "role" else None)

		# Card 2: already completed perk
		card2 = mock.Mock()
		card2.tag_name = "span"
		card2.text = "Perk unlocked\nCompleted"
		card2.get_attribute.side_effect = lambda attr: "card2" if attr == "id" else None

		# Card 3: partner perk (Super Duolingo)
		card3 = mock.Mock()
		card3.tag_name = "div"
		card3.text = "Get Super, on us\nEnjoy 2 free months of Super Duolingo, on us\nStart now"
		card3.get_attribute.side_effect = lambda attr: "card3" if attr == "id" else None

		heading = mock.Mock()
		heading.text = "Your perks"
		container = mock.Mock()
		container.find_elements.return_value = [card1, card2, card3]
		heading.find_element.return_value = container

		mock_driver.find_elements.side_effect = lambda by, query: [heading] if "your perks" in query.lower() else []

		utils = element_selectors.ElementSelectionUtils(mock_driver)
		cards = utils.get_perk_cards()

		self.assertIn(card1, cards)
		self.assertNotIn(card2, cards)
		self.assertIn(card3, cards)

	def test_get_dialog_activate_action_finds_activate_link(self):
		mock_dialog = mock.Mock()
		act_link = mock.Mock()
		act_link.tag_name = "a"
		act_link.text = "Activate"
		close_btn = mock.Mock()
		close_btn.tag_name = "button"
		close_btn.text = "Close"
		close_btn.get_attribute.return_value = "Close"

		mock_dialog.find_elements.side_effect = lambda by, tag: [act_link] if tag == "a" else [close_btn]

		utils = element_selectors.ElementSelectionUtils(mock.Mock())
		action = utils.get_dialog_activate_action(mock_dialog)
		self.assertEqual(action, act_link)

		close = utils.get_dialog_close_button(mock_dialog)
		self.assertEqual(close, close_btn)


class TestCompletePerks(unittest.TestCase):
	def test_complete_perks_activates_dialog_perk(self):
		clicks = []
		mock_card = mock.Mock(text="Perk unlocked\nSearch 2x more per day\nClaim offer")
		mock_dialog = mock.Mock(text="Perk unlocked\nLimited time search offer\nActivate")
		mock_activate = mock.Mock(tag_name="a", text="Activate")
		mock_close = mock.Mock(tag_name="button", text="Close")

		mock_elements = types.SimpleNamespace(
			get_perk_cards=mock.Mock(return_value=[mock_card]),
			get_visible_dialog=mock.Mock(return_value=mock_dialog),
			get_dialog_activate_action=mock.Mock(return_value=mock_activate),
			get_dialog_close_button=mock.Mock(return_value=mock_close),
		)

		mock_instance = types.SimpleNamespace(
			account_name="test_user",
			elements=mock_elements,
			switch_to_dashboard=mock.Mock(),
			mouse=types.SimpleNamespace(
				wheel_scroll_element_into_view=mock.Mock(),
				wheel_scroll_to_top=mock.Mock(),
			),
			move_to_and_click=lambda elem: clicks.append(elem),
			driver=mock.Mock(window_handles=["main"]),
			main_window="main",
			restore_main_tab=mock.Mock(),
		)

		with mock.patch("rewards_tasks._wait"):
			Tasks.complete_perks(mock_instance)

		mock_instance.switch_to_dashboard.assert_called_once()
		self.assertIn(mock_card, clicks)
		self.assertIn(mock_activate, clicks)
		self.assertIn(mock_close, clicks)

	def test_complete_perks_handles_new_tab(self):
		mock_card = mock.Mock(text="Get Super, on us\nStart now")
		mock_elements = types.SimpleNamespace(
			get_perk_cards=mock.Mock(return_value=[mock_card]),
			get_visible_dialog=mock.Mock(return_value=None),
		)

		switched = []
		closed = []
		tab_utils = types.SimpleNamespace(
			switch_to_other_tab=lambda: switched.append(True),
			close_all_other_tabs=lambda exceptions=None: closed.append(exceptions),
		)

		mock_instance = types.SimpleNamespace(
			account_name="test_user",
			elements=mock_elements,
			switch_to_dashboard=mock.Mock(),
			mouse=types.SimpleNamespace(
				wheel_scroll_element_into_view=mock.Mock(),
				wheel_scroll_to_top=mock.Mock(),
			),
			move_to_and_click=mock.Mock(),
			driver=mock.Mock(window_handles=["main", "tab2"]),
			main_window="main",
			tab_utils=tab_utils,
			restore_main_tab=mock.Mock(),
		)

		with mock.patch("rewards_tasks._wait"):
			Tasks.complete_perks(mock_instance)

		self.assertEqual(len(switched), 1)
		self.assertEqual(closed, [["main"]])

	def test_complete_perks_no_cards_exits_silently(self):
		mock_elements = types.SimpleNamespace(
			get_perk_cards=mock.Mock(return_value=[]),
		)
		mock_instance = types.SimpleNamespace(
			account_name="test_user",
			elements=mock_elements,
			switch_to_dashboard=mock.Mock(),
		)
		with mock.patch("rewards_tasks._wait"):
			Tasks.complete_perks(mock_instance)

		mock_instance.switch_to_dashboard.assert_called_once()

	def test_complete_required_searches_triggers_complete_perks(self):
		perks_called = []
		mock_instance = types.SimpleNamespace(
			account_name="test_user",
			complete_perks=lambda: perks_called.append(True),
			read_search_points=mock.Mock(return_value=(50, 50)),
		)
		with mock.patch.object(rewards_tasks.pacing, "search_target", return_value=50):
			Tasks.complete_required_searches(mock_instance)

		self.assertEqual(len(perks_called), 1)


if __name__ == "__main__":
	unittest.main()
