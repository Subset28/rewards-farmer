"""Tests for 2026 Rewards Meta: Cooldown Burst Pacing, Puzzle Piece Harvesting, and Contemplation Timing."""

import os
import sys
import tempfile
import types
import unittest
from unittest import mock

SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src")
if SRC not in sys.path:
	sys.path.insert(0, SRC)

import element_selectors  # noqa: E402
import rewards_tasks  # noqa: E402

Tasks = rewards_tasks.RewardsTaskUtils


class TestCooldownAndBurstPacing(unittest.TestCase):
	def setUp(self):
		self.temp_dir = tempfile.TemporaryDirectory()
		self.addCleanup(self.temp_dir.cleanup)
		import journal
		patch = mock.patch.object(journal, "JOURNAL_FILE", os.path.join(self.temp_dir.name, "journal.jsonl"))
		patch.start()
		self.addCleanup(patch.stop)

	def test_burst_search_limit_restricts_searches_per_round(self):
		batches = []
		mock_instance = types.SimpleNamespace(
			account_name="test_acc",
			PROBE_SEARCHES=3,
			DRY_SEARCHES_TO_ALERT=3,
			BURST_SEARCH_LIMIT=4,
			read_search_points=mock.Mock(side_effect=[(0, 100), (9, 100), (21, 100), (33, 100)]),
			run_search_batch=lambda count: batches.append(count),
		)
		with mock.patch.object(rewards_tasks.pacing, "search_target", return_value=30):
			Tasks.complete_required_searches(mock_instance)

		self.assertEqual(batches[0], 3)
		self.assertLessEqual(batches[1], 4)

	def test_cooldown_recognized_when_subsequent_round_earns_zero(self):
		# Round 1 gains 15 pts. Round 2 does 3 searches and gains 0.
		# Should break cleanly and NOT call notify.send
		mock_instance = types.SimpleNamespace(
			account_name="test_cooldown",
			PROBE_SEARCHES=3,
			DRY_SEARCHES_TO_ALERT=3,
			BURST_SEARCH_LIMIT=0,
			read_search_points=mock.Mock(side_effect=[(0, 100), (15, 100), (15, 100), (15, 100)]),
			run_search_batch=mock.Mock(),
		)
		with mock.patch.object(rewards_tasks.pacing, "search_target", return_value=50), \
			mock.patch.object(rewards_tasks.notify, "send") as mock_notify, \
			mock.patch("rewards_tasks._wait"):
			Tasks.complete_required_searches(mock_instance)

		mock_notify.assert_not_called()

	def test_uncredited_account_from_start_still_triggers_alert(self):
		# Account with 0 pts whose searches don't credit at all MUST still alert
		mock_instance = types.SimpleNamespace(
			account_name="test_blocked",
			PROBE_SEARCHES=3,
			DRY_SEARCHES_TO_ALERT=3,
			BURST_SEARCH_LIMIT=0,
			read_search_points=mock.Mock(side_effect=[(0, 100), (0, 100), (0, 100)]),
			run_search_batch=mock.Mock(),
		)
		with mock.patch.object(rewards_tasks.pacing, "search_target", return_value=50), \
			mock.patch.object(rewards_tasks.notify, "send") as mock_notify, \
			mock.patch("rewards_tasks._wait"):
			Tasks.complete_required_searches(mock_instance)

		mock_notify.assert_called_once()
		self.assertIn("Searches earned no points", mock_notify.call_args.args[0])


class TestPuzzlePieceHarvesting(unittest.TestCase):
	def test_claim_puzzle_pieces_clicks_when_button_found(self):
		mock_btn = mock.Mock()
		mock_elements = types.SimpleNamespace(
			get_puzzle_claim_button=mock.Mock(return_value=mock_btn)
		)
		mock_instance = types.SimpleNamespace(
			account_name="test_acc",
			elements=mock_elements,
			move_to_and_click=mock.Mock(),
		)
		with mock.patch("rewards_tasks._wait"):
			Tasks.claim_puzzle_pieces(mock_instance)

		mock_instance.move_to_and_click.assert_called_once_with(mock_btn)

	def test_claim_puzzle_pieces_handles_no_button_gracefully(self):
		mock_elements = types.SimpleNamespace(
			get_puzzle_claim_button=mock.Mock(return_value=None)
		)
		mock_instance = types.SimpleNamespace(
			account_name="test_acc",
			elements=mock_elements,
			move_to_and_click=mock.Mock(),
		)
		Tasks.claim_puzzle_pieces(mock_instance)
		mock_instance.move_to_and_click.assert_not_called()

	def test_get_puzzle_claim_button_matches_puzzle_text(self):
		mock_driver = mock.Mock()
		btn1 = mock.Mock()
		btn1.text = "Other Action"
		btn2 = mock.Mock()
		btn2.text = "Claim Puzzle Piece"
		mock_driver.find_elements.side_effect = lambda by, val: [btn1, btn2] if val == "button" else []

		utils = element_selectors.ElementSelectionUtils(mock_driver)
		found = utils.get_puzzle_claim_button()
		self.assertEqual(found, btn2)


if __name__ == "__main__":
	unittest.main()
