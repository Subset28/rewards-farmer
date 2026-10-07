"""The per-task record that lets health.py notice a task that quietly broke."""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import task_log


class TestTaskLog(unittest.TestCase):
	def setUp(self):
		directory = tempfile.TemporaryDirectory()
		self.addCleanup(directory.cleanup)
		patcher = mock.patch.object(task_log, "LOG_FILE", str(Path(directory.name) / "task_log.jsonl"))
		patcher.start()
		self.addCleanup(patcher.stop)

	def test_what_is_recorded_is_read_back_per_account(self):
		task_log.record("default", "Explore on Bing", True, "OK", 60)
		task_log.record("second", "Explore on Bing", False, "SKIP", None)

		self.assertEqual([r["task"] for r in task_log.history("default")], ["Explore on Bing"])
		self.assertEqual(task_log.history("default")[0]["gained"], 60)
		self.assertFalse(task_log.history("second")[0]["completed"])
		self.assertEqual(len(task_log.history()), 2)

	def test_a_damaged_line_is_skipped(self):
		task_log.record("default", "Bonus points", True, "OK", 5)

		with open(task_log.LOG_FILE, "a", encoding="utf-8") as handle:
			handle.write("{broken\n")

		task_log.record("default", "Quests", True, "OK", 0)

		self.assertEqual([r["task"] for r in task_log.history()], ["Bonus points", "Quests"])

	def test_the_oldest_lines_go_when_the_file_grows(self):
		with mock.patch.object(task_log, "MAX_LINES", 10):
			for n in range(14):
				task_log.record("default", f"task{n}", True, "OK")

		tasks = [r["task"] for r in task_log.history()]

		self.assertLessEqual(len(tasks), 10)
		self.assertEqual(tasks[-1], "task13")

	def test_no_file_means_no_history_not_an_error(self):
		self.assertEqual(task_log.history(), [])

	def test_a_write_that_fails_does_not_raise(self):
		with mock.patch("builtins.open", side_effect=OSError("read-only")):
			task_log.record("default", "Quests", True, "OK")

	def test_lines_are_plain_json(self):
		task_log.record("default", "Quests", True, "OK", 0)

		self.assertEqual(json.loads(Path(task_log.LOG_FILE).read_text().splitlines()[0])["tag"], "OK")


if __name__ == "__main__":
	unittest.main()
