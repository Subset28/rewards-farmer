"""Tests for the journal, and for a scheduler that reads it to know how far it got.

	python -m unittest discover -s tests
"""

import json
import logging
import os
import random
import sys
import tempfile
import types
import unittest
from datetime import datetime, timedelta
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import journal
import log_utils
import schedule_plan
import search_scheduler as ss

DAY = datetime(2026, 10, 4)


class JournalTestCase(unittest.TestCase):
	def setUp(self):
		directory = tempfile.TemporaryDirectory()
		self.addCleanup(directory.cleanup)
		self.directory = directory.name

		for patcher in (
			mock.patch.object(journal, "JOURNAL_FILE", os.path.join(self.directory, "journal.jsonl")),
			mock.patch.object(schedule_plan, "PLAN_FILE", os.path.join(self.directory, "schedule_plan.json")),
		):
			patcher.start()
			self.addCleanup(patcher.stop)

	def write_at(self, when, owner, kind, event, **details):
		with mock.patch.object(journal, "_now", return_value=when):
			journal.record(owner, kind, event, **details)


class TestJournal(JournalTestCase):
	def test_an_event_is_recorded_and_read_back_with_its_details(self):
		self.write_at(DAY.replace(hour=14, minute=4, second=30), "default", "search", "start", planned="x", mode="scheduled")

		rows = journal.read_all()

		self.assertEqual(rows, [{"t": "2026-10-04T14:04:30", "owner": "default", "kind": "search", "event": "start", "planned": "x", "mode": "scheduled"}])

	def test_events_come_back_oldest_first(self):
		for hour in (9, 12, 15):
			self.write_at(DAY.replace(hour=hour), "default", "search", "start", planned=str(hour))

		self.assertEqual([r["planned"] for r in journal.read_all()], ["9", "12", "15"])

	def test_damaged_and_odd_lines_are_skipped(self):
		self.write_at(DAY.replace(hour=9), "default", "search", "start", planned="good")

		with open(journal.JOURNAL_FILE, "a", encoding="utf-8") as handle:
			handle.write("{not json\n[1, 2]\n" + json.dumps({"owner": "x"}) + "\n" + json.dumps({"t": 5, "event": "e"}) + "\n\n")

		self.assertEqual([r["planned"] for r in journal.read_all()], ["good"])

	def test_no_file_is_an_empty_journal(self):
		self.assertEqual(journal.read_all(), [])
		self.assertEqual(journal.events(), [])

	def test_an_unwritable_location_does_not_raise(self):
		with mock.patch.object(journal, "JOURNAL_FILE", os.path.join(os.devnull, "nope", "j.jsonl")):
			journal.record("default", "search", "start")

			self.assertEqual(journal.read_all(), [])

	def test_details_that_cannot_be_written_do_not_raise(self):
		journal.record("default", "search", "start", bad=object())

		self.assertEqual(journal.read_all(), [])

	def test_the_oldest_lines_go_when_the_file_gets_long(self):
		with mock.patch.object(journal, "MAX_LINES", 20):
			for index in range(60):
				self.write_at(DAY.replace(hour=9) + timedelta(seconds=index), "default", "search", "start", n=index)

			rows = journal.read_all()

		self.assertLessEqual(len(rows), 20)
		self.assertEqual(rows[-1]["n"], 59)

	def test_events_are_filtered_by_owner_kind_and_day(self):
		self.write_at(DAY - timedelta(days=1), "default", "search", "start", planned="yesterday")
		self.write_at(DAY.replace(hour=9), "default", "search", "start", planned="a")
		self.write_at(DAY.replace(hour=10), "second", "search", "start", planned="b")
		self.write_at(DAY.replace(hour=11), "default", "daily", "start", planned="c")
		now = DAY.replace(hour=15)

		self.assertEqual([r["planned"] for r in journal.events(now=now)], ["a", "b", "c"])
		self.assertEqual([r["planned"] for r in journal.events(owner="default", now=now)], ["a", "c"])
		self.assertEqual([r["planned"] for r in journal.events(owner="default", kind="search", now=now)], ["a"])
		self.assertEqual([r["planned"] for r in journal.events(days=2, owner="default", kind="search", now=now)], ["yesterday", "a"])

	def test_a_new_day_starts_a_clean_slate(self):
		self.write_at(DAY.replace(hour=22), "default", "search", "start", planned="late")

		self.assertEqual(journal.events(now=DAY + timedelta(days=1, hours=1)), [])


class TestQuotaComplete(JournalTestCase):
	NOW = DAY.replace(hour=15)

	def quota(self, hour, account, complete, **extra):
		self.write_at(DAY.replace(hour=hour), account, "search", "quota", account=account, points=100 if complete else 40, cap=100, complete=complete, **extra)

	def test_nothing_reported_is_not_complete(self):
		self.assertFalse(journal.quota_complete(["default"], now=self.NOW))

	def test_a_full_quota_reported_today_is_complete(self):
		self.quota(10, "default", True)

		self.assertTrue(journal.quota_complete(["default"], now=self.NOW))

	def test_a_quota_not_yet_full_is_not_complete(self):
		self.quota(10, "default", False)

		self.assertFalse(journal.quota_complete(["default"], now=self.NOW))

	def test_the_latest_report_counts(self):
		self.quota(10, "default", True)
		self.quota(12, "default", False)

		self.assertFalse(journal.quota_complete(["default"], now=self.NOW))

	def test_every_account_must_be_full(self):
		self.quota(10, "default", True)

		self.assertFalse(journal.quota_complete(["default", "second"], now=self.NOW))

		self.quota(11, "second", True)

		self.assertTrue(journal.quota_complete(["default", "second"], now=self.NOW))

	def test_yesterdays_full_quota_does_not_count_today(self):
		self.write_at(DAY - timedelta(days=1), "default", "search", "quota", account="default", complete=True)

		self.assertFalse(journal.quota_complete(["default"], now=self.NOW))

	def test_no_accounts_is_never_complete(self):
		self.quota(10, "default", True)

		self.assertFalse(journal.quota_complete([], now=self.NOW))
		self.assertFalse(journal.quota_complete(None, now=self.NOW))


class TestDescribe(JournalTestCase):
	def test_an_empty_journal_says_so(self):
		self.assertEqual(journal.describe(now=DAY), "nothing recorded yet")

	def test_it_reads_as_a_day_by_day_list(self):
		self.write_at(DAY.replace(hour=14, minute=4, second=30), "default", "search", "start", planned="p", mode="scheduled")
		self.write_at(DAY.replace(hour=14, minute=12), "default", "search", "end", outcome="ok", seconds=450)
		text = journal.describe(now=DAY.replace(hour=15))

		self.assertIn("2026-10-04", text)
		self.assertIn("14:04:30", text)
		self.assertIn("start", text)
		self.assertIn("outcome=ok", text)
		self.assertIn("seconds=450", text)


class TestWhichRunsAreStillDue(JournalTestCase):
	"""The scheduler working out how far the previous process got."""

	TIMES = [DAY.replace(hour=h) for h in (9, 12, 15, 20)]

	def setUp(self):
		super().setUp()
		schedule_plan.write("search", "default", {"day": "2026-10-04", "times": [t.isoformat() for t in self.TIMES]})

	def mark(self, planned, *events):
		for event in events:
			self.write_at(planned + timedelta(minutes=1 if event == "start" else 9), "default", "search", event, planned=planned.isoformat())

	def due(self, hour, minute=0, rng=None):
		return ss.outstanding(DAY.replace(hour=hour, minute=minute), "default", rng or random.Random(1))

	def planned(self, due):
		return [d.planned.hour for d in due]

	def test_before_anything_has_run_everything_is_scheduled(self):
		due = self.due(8, 30)

		self.assertEqual(self.planned(due), [9, 12, 15, 20])
		self.assertTrue(all(d.mode == "scheduled" for d in due))

	def test_a_run_that_started_and_ended_is_done(self):
		self.mark(self.TIMES[0], "start", "end")

		self.assertEqual(self.planned(self.due(10)), [12, 15, 20])

	def test_a_run_that_was_cut_off_is_redone_as_a_catch_up(self):
		self.mark(self.TIMES[0], "start")
		due = self.due(10)

		self.assertEqual(self.planned(due), [9, 12, 15, 20])
		self.assertEqual(due[0].mode, "catch_up")
		self.assertEqual(due[0].planned, self.TIMES[0])

	def test_a_run_that_never_started_is_redone_while_not_too_late(self):
		due = self.due(11)

		self.assertEqual(due[0].mode, "catch_up")
		self.assertEqual(due[0].planned, self.TIMES[0])

	def test_a_run_missed_by_more_than_three_hours_is_dropped(self):
		due = self.due(13)

		self.assertEqual(self.planned(due), [12, 15, 20])
		self.assertEqual(due[0].mode, "catch_up")

		due = self.due(16)

		self.assertEqual(self.planned(due), [15, 20])

	def test_a_run_that_ended_in_failure_or_was_skipped_is_not_redone(self):
		self.write_at(self.TIMES[0] + timedelta(minutes=2), "default", "search", "end", planned=self.TIMES[0].isoformat(), outcome="failed", exit_code=1)
		self.write_at(self.TIMES[1] + timedelta(minutes=2), "default", "search", "end", planned=self.TIMES[1].isoformat(), outcome="skipped", reason="paused")

		self.assertEqual(self.planned(self.due(12, 30)), [15, 20])

	def test_after_the_window_closes_nothing_is_due(self):
		self.assertEqual(self.due(23, 30), [])

	def test_a_catch_up_waits_a_short_different_time_not_zero(self):
		waits = set()

		for seed in range(20):
			due = self.due(10, rng=random.Random(seed))
			waits.add(round((due[0].when - DAY.replace(hour=10)).total_seconds()))

		self.assertTrue(all(30 <= w <= 180 for w in waits))
		self.assertGreater(len(waits), 10)

	def test_runs_come_back_in_the_order_they_will_start(self):
		self.mark(self.TIMES[0], "start")
		due = self.due(10)

		self.assertEqual([d.when for d in due], sorted(d.when for d in due))

	def test_another_schedulers_events_do_not_count(self):
		self.write_at(self.TIMES[0] + timedelta(minutes=9), "second", "search", "end", planned=self.TIMES[0].isoformat(), outcome="ok")

		self.assertEqual(self.planned(self.due(10)), [9, 12, 15, 20])

	def test_a_restart_in_the_middle_of_a_run_is_the_cut_off_case(self):
		# The run began at 12:01 and the container was replaced during it.
		self.mark(self.TIMES[0], "start", "end")
		self.mark(self.TIMES[1], "start")

		due = self.due(12, 10)

		self.assertEqual(self.planned(due), [12, 15, 20])
		self.assertEqual(due[0].mode, "catch_up")


class FakeRun:
	def __init__(self, code=0, error=None):
		self.code, self.error, self.calls = code, error, 0

	def __call__(self, command, check=False):
		self.calls += 1

		if self.error:
			raise self.error

		return types.SimpleNamespace(returncode=self.code)


class TestLaunch(JournalTestCase):
	PLANNED = DAY.replace(hour=14, minute=4)

	def setUp(self):
		super().setUp()

		for patcher in (
			mock.patch.object(ss, "account_names", return_value=["default"]),
			mock.patch.object(ss.safety, "blocked", return_value=None),
			mock.patch.object(journal, "_now", return_value=DAY.replace(hour=14, minute=5)),
		):
			patcher.start()
			self.addCleanup(patcher.stop)

	def due(self, mode="scheduled"):
		return ss.Due(self.PLANNED, self.PLANNED, mode)

	def events(self):
		return [(r["event"], r) for r in journal.read_all()]

	def test_a_run_is_journaled_from_start_to_end(self):
		run = FakeRun(0)

		self.assertEqual(ss.launch("default", self.due(), run=run), "ok")
		(first, start), (second, end) = self.events()

		self.assertEqual((first, second), ("start", "end"))
		self.assertEqual(start["planned"], self.PLANNED.isoformat())
		self.assertEqual(start["mode"], "scheduled")
		self.assertEqual((end["outcome"], end["exit_code"]), ("ok", 0))
		self.assertIn("seconds", end)
		self.assertEqual(run.calls, 1)

	def test_a_failing_run_is_journaled_as_failed(self):
		self.assertEqual(ss.launch("default", self.due(), run=FakeRun(1)), "failed")

		self.assertEqual(self.events()[-1][1]["outcome"], "failed")
		self.assertEqual(self.events()[-1][1]["exit_code"], 1)

	def test_a_run_that_cannot_start_is_journaled_and_does_not_raise(self):
		with self.assertLogs(ss.logger, level="ERROR"):
			outcome = ss.launch("default", self.due(), run=FakeRun(error=OSError("no python")))

		self.assertEqual(outcome, "failed")
		self.assertIsNone(self.events()[-1][1]["exit_code"])

	def test_a_catch_up_is_marked_as_one(self):
		ss.launch("default", self.due("catch_up"), run=FakeRun(0))

		self.assertEqual(self.events()[0][1]["mode"], "catch_up")

	def test_with_the_quota_already_full_no_run_is_made(self):
		journal.record("default", "search", "quota", account="default", points=100, cap=100, complete=True)
		run = FakeRun(0)

		with self.assertLogs(ss.logger, level="INFO"):
			outcome = ss.launch("default", self.due(), run=run)

		end = self.events()[-1][1]

		self.assertEqual(outcome, "skipped")
		self.assertEqual(run.calls, 0)
		self.assertEqual((end["event"], end["outcome"]), ("end", "skipped"))
		self.assertIn("quota", end["reason"])

	def test_a_skipped_run_is_not_redone_later(self):
		journal.record("default", "search", "quota", account="default", complete=True)

		with self.assertLogs(ss.logger, level="INFO"):
			ss.launch("default", self.due(), run=FakeRun(0))

		schedule_plan.write("search", "default", {"day": "2026-10-04", "times": [self.PLANNED.isoformat()]})

		self.assertEqual(ss.outstanding(DAY.replace(hour=15), "default", random.Random(1)), [])

	def test_a_paused_account_means_no_run_and_it_is_not_retried(self):
		run = FakeRun(0)

		with mock.patch.object(ss.safety, "blocked", return_value={"kind": "challenge", "reason": "x"}), self.assertLogs(ss.logger, level="ERROR"):
			outcome = ss.launch("default", self.due(), run=run)

		self.assertEqual(outcome, "skipped")
		self.assertEqual(run.calls, 0)
		self.assertEqual(self.events()[-1][1]["reason"], "paused")

	def test_a_quota_that_is_not_full_does_not_stop_a_run(self):
		journal.record("default", "search", "quota", account="default", points=60, cap=100, complete=False)

		self.assertEqual(ss.launch("default", self.due(), run=FakeRun(0)), "ok")

	def test_the_other_accounts_quota_is_not_this_ones(self):
		journal.record("second", "search", "quota", account="second", complete=True)

		self.assertEqual(ss.launch("default", self.due(), run=FakeRun(0)), "ok")


class TestLogFile(unittest.TestCase):
	def test_a_small_file_is_left_alone(self):
		with tempfile.TemporaryDirectory() as directory:
			path = os.path.join(directory, "x.log")

			with open(path, "wb") as handle:
				handle.write(b"line\n" * 10)

			self.assertFalse(log_utils.trim_log_file(path, limit=1000, keep=100))
			self.assertEqual(os.path.getsize(path), 50)

	def test_a_big_file_is_cut_back_to_whole_recent_lines(self):
		with tempfile.TemporaryDirectory() as directory:
			path = os.path.join(directory, "x.log")

			with open(path, "wb") as handle:
				handle.writelines(f"line {i:04d}\n".encode() for i in range(500))

			self.assertTrue(log_utils.trim_log_file(path, limit=1000, keep=200))

			with open(path) as handle:
				lines = handle.read().splitlines()

			self.assertLess(os.path.getsize(path), 200)
			self.assertEqual(lines[-1], "line 0499")
			self.assertTrue(all(line.startswith("line ") and len(line) == 9 for line in lines))

	def test_a_missing_file_is_nothing_to_trim(self):
		self.assertFalse(log_utils.trim_log_file(os.path.join(tempfile.gettempdir(), "no-such-file-xyz.log")))

	def test_the_file_log_carries_the_date_and_the_folder_is_made(self):
		root = logging.getLogger()
		saved_handlers, saved_level, saved_flag = list(root.handlers), root.level, log_utils._configured

		try:
			with tempfile.TemporaryDirectory() as directory:
				path = os.path.join(directory, "logs", "scheduler.log")
				log_utils._configured = False
				log_utils.setup_logging(level="INFO", log_file=path)
				logging.getLogger("t").info("hello from a build")

				for handler in root.handlers:
					handler.flush()
					handler.close()

				with open(path, encoding="utf-8") as handle:
					text = handle.read()

				self.assertRegex(text, r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} INFO")
				self.assertIn("hello from a build", text)
		finally:
			root.handlers[:] = saved_handlers
			root.setLevel(saved_level)
			log_utils._configured = saved_flag

	def test_a_log_file_that_cannot_be_opened_does_not_stop_anything(self):
		root = logging.getLogger()
		saved_handlers, saved_level, saved_flag = list(root.handlers), root.level, log_utils._configured

		try:
			log_utils._configured = False

			with self.assertLogs(log_utils.logging.getLogger(log_utils.__name__), level="WARNING"):
				log_utils.setup_logging(level="INFO", log_file=os.path.join(os.devnull, "nope", "x.log"))
		finally:
			root.handlers[:] = saved_handlers
			root.setLevel(saved_level)
			log_utils._configured = saved_flag


if __name__ == "__main__":
	unittest.main()
