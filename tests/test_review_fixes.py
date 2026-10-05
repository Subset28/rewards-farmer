"""Fixes from the pacing review: one clock, a points file that survives a torn line, a state file
that is never half written, and a daily run that is not lost when the profile is busy."""

import inspect
import json
import os
import sys
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import clock
import daily_loop
import isolation
import journal
import pacing
import points_log
import rewards_tasks
import run_lock
import search_scheduler as ss


class TmpTestCase(unittest.TestCase):
	def setUp(self):
		directory = tempfile.TemporaryDirectory()
		self.addCleanup(directory.cleanup)
		self.dir = Path(directory.name)

		for patcher in (
			mock.patch.object(points_log, "LOG_FILE", str(self.dir / "points.jsonl")),
			mock.patch.object(journal, "JOURNAL_FILE", str(self.dir / "journal.jsonl")),
			mock.patch.object(pacing, "STATE_FILE", str(self.dir / "pacing.json")),
			mock.patch.dict(os.environ, {"REWARDS_CLOCK_TZ": "", "REWARDS_RAMP_DAYS": "7"}),
		):
			patcher.start()
			self.addCleanup(patcher.stop)


class TestClock(TmpTestCase):
	def test_dates_follow_the_clock_zone_not_tz(self):
		with mock.patch.dict(os.environ, {"REWARDS_CLOCK_TZ": "Pacific/Auckland", "TZ": "America/New_York"}):
			auckland = clock.now()

		with mock.patch.dict(os.environ, {"REWARDS_CLOCK_TZ": "Pacific/Pago_Pago"}):
			pago = clock.now()

		# 24 hours apart at the most extreme: a day's difference in the wall clock, never equal.
		self.assertGreaterEqual((auckland - pago).total_seconds(), 22 * 3600)

	def test_an_unknown_zone_falls_back_to_local_time_instead_of_failing(self):
		with mock.patch.dict(os.environ, {"REWARDS_CLOCK_TZ": "Not/AZone"}):
			self.assertLess(abs((clock.now() - datetime.now()).total_seconds()), 5)

	def test_unset_is_plain_local_time(self):
		self.assertLess(abs((clock.now() - datetime.now()).total_seconds()), 5)

	def test_the_zone_to_hand_on_prefers_the_clock_zone_then_tz(self):
		with mock.patch.dict(os.environ, {"REWARDS_CLOCK_TZ": "", "TZ": "America/New_York"}):
			self.assertEqual(clock.parent_zone(), "America/New_York")

		with mock.patch.dict(os.environ, {"REWARDS_CLOCK_TZ": "Europe/London", "TZ": "Asia/Tokyo"}):
			self.assertEqual(clock.parent_zone(), "Europe/London")

	def test_pacing_and_the_journal_use_it(self):
		with mock.patch.dict(os.environ, {"REWARDS_CLOCK_TZ": "Pacific/Kiritimati"}):
			self.assertEqual(pacing._today(), clock.today())
			self.assertEqual(journal._now().date(), clock.today())


class TestChildEnvironment(unittest.TestCase):
	def test_the_parents_zone_is_handed_on_before_tz_is_changed_for_the_browser(self):
		env = isolation.environment_for("alpha", {"timezone": "Asia/Tokyo"}, base={"TZ": "America/New_York"})

		self.assertEqual(env["TZ"], "Asia/Tokyo")
		self.assertEqual(env["REWARDS_CLOCK_TZ"], "America/New_York")

	def test_an_existing_clock_zone_is_kept(self):
		env = isolation.environment_for("alpha", {"timezone": "Asia/Tokyo"}, base={"TZ": "Europe/Paris", "REWARDS_CLOCK_TZ": "America/Chicago"})

		self.assertEqual(env["REWARDS_CLOCK_TZ"], "America/Chicago")

	def test_with_no_zone_at_all_nothing_is_invented(self):
		env = isolation.environment_for("alpha", {}, base={})

		self.assertNotIn("REWARDS_CLOCK_TZ", env)
		self.assertNotIn("TZ", env)


class TestJournalDay(TmpTestCase):
	NOW = datetime(2026, 10, 5, 8, 0, 0)

	def test_an_event_dated_in_the_future_is_not_todays(self):
		journal.record("default", "search", "quota", account="default", points=100, cap=100, complete=True)
		Path(journal.JOURNAL_FILE).write_text(json.dumps({
			"t": "2026-10-06T09:00:00", "owner": "default", "kind": "search", "event": "quota",
			"account": "default", "points": 100, "cap": 100, "complete": True,
		}) + "\n")

		self.assertEqual(journal.events(now=self.NOW), [])
		self.assertFalse(journal.quota_complete(["default"], now=self.NOW))

	def test_todays_and_earlier_events_still_count(self):
		Path(journal.JOURNAL_FILE).write_text(
			json.dumps({"t": "2026-10-05T07:00:00", "owner": "d", "kind": "search", "event": "x"}) + "\n"
			+ json.dumps({"t": "2026-10-04T07:00:00", "owner": "d", "kind": "search", "event": "x"}) + "\n"
		)

		self.assertEqual(len(journal.events(now=self.NOW)), 1)
		self.assertEqual(len(journal.events(days=2, now=self.NOW)), 2)


class TestTornPointsFile(TmpTestCase):
	def test_a_half_written_last_line_does_not_hide_the_rest(self):
		points_log.record("default", {"today": 1, "month": 2, "lifetime": 3})
		points_log.record("default", {"today": 4, "month": 5, "lifetime": 6})

		with open(points_log.LOG_FILE, "a", encoding="utf-8") as handle:
			handle.write('{"time": "2026-10-05 12:00:0')

		self.assertEqual([r["today"] for r in points_log.history("default")], [1, 4])

	def test_lines_that_are_not_readings_are_skipped_not_fatal(self):
		points_log.record("default", {"today": 1, "month": 2, "lifetime": 3})

		with open(points_log.LOG_FILE, "a", encoding="utf-8") as handle:
			handle.write("[1, 2]\n42\nnull\n\"text\"\n{\"no_time\": 1}\n")

		self.assertEqual(len(points_log.history()), 1)

	def test_an_old_account_with_a_torn_log_is_still_old_not_new(self):
		with open(points_log.LOG_FILE, "w", encoding="utf-8") as handle:
			handle.write(json.dumps({"time": "2026-06-01 10:00:00", "account": "old", "today": 1}) + "\n")
			handle.write('{"time": "2026-10-05 12:0')

		with mock.patch.object(pacing, "_today", return_value=date(2026, 10, 5)):
			self.assertEqual(pacing.first_day("old"), date(2026, 6, 1))
			self.assertFalse(pacing.in_ramp("old", date(2026, 10, 5)))

	def test_a_file_that_cannot_be_read_at_all_is_no_history(self):
		os.mkdir(points_log.LOG_FILE)

		self.assertEqual(points_log.history(), [])


class TestPacingState(TmpTestCase):
	TODAY = date(2026, 10, 5)

	def setUp(self):
		super().setUp()
		patcher = mock.patch.object(pacing, "_today", return_value=self.TODAY)
		patcher.start()
		self.addCleanup(patcher.stop)

	def test_the_state_file_is_replaced_whole_and_no_temporary_is_left(self):
		pacing.first_day("a")

		self.assertEqual(json.loads(Path(pacing.STATE_FILE).read_text()), {"a": {"first_day": "2026-10-05"}})
		self.assertEqual([p.name for p in self.dir.iterdir() if p.name.startswith("pacing")], ["pacing.json"])

	def test_an_entry_another_process_wrote_in_the_meantime_is_not_overwritten(self):
		real_read = pacing._read_state
		calls = []

		def racing_read():
			calls.append(1)

			# On the second read (just before the write) someone else has added an entry.
			if len(calls) == 2:
				Path(pacing.STATE_FILE).write_text(json.dumps({"other": {"first_day": "2026-01-01"}, "a": {"first_day": "2026-02-02"}}))

			return real_read()

		with mock.patch.object(pacing, "_read_state", side_effect=racing_read):
			pacing.first_day("a")

		saved = json.loads(Path(pacing.STATE_FILE).read_text())

		self.assertEqual(saved["other"]["first_day"], "2026-01-01")
		self.assertEqual(saved["a"]["first_day"], "2026-02-02")

	def test_seeding_an_account_to_today_says_so_loudly(self):
		with self.assertLogs(pacing.logger, level="WARNING") as logs:
			pacing.first_day("brand-new")

		self.assertTrue(any("treated as new" in line for line in logs.output))

	def test_an_account_with_history_is_not_warned_about(self):
		points_log.record("old", {"today": 1, "month": 1, "lifetime": 1})

		with self.assertNoLogs(pacing.logger, level="WARNING"):
			pacing.first_day("old")


class TestStepNames(unittest.TestCase):
	def test_every_step_pacing_allows_exists_in_the_task_loop(self):
		"""A renamed step would leave the filter matching nothing, and the account would do no tasks."""
		source = inspect.getsource(rewards_tasks.RewardsTaskUtils.complete_all_tasks)

		for name in set(pacing.RAMP_STEPS) | set(pacing.LIGHT_STEPS):
			self.assertIn(f'"{name}"', source, name)


class TestBusyProfile(TmpTestCase):
	"""A run that gave up waiting for the profile did not happen, so it is neither done nor a failure."""

	def due(self):
		planned = datetime(2026, 10, 5, 14, 4)

		return ss.Due(planned, planned, "scheduled")

	def test_a_search_run_that_found_the_profile_busy_is_deferred_not_failed(self):
		with mock.patch.object(ss, "account_names", return_value=["default"]), \
			mock.patch.object(ss.safety, "blocked", return_value=None), \
			mock.patch.object(ss.notify, "send_each") as alert:
			result = ss.launch("default", self.due(), run=lambda *a, **k: SimpleNamespace(returncode=run_lock.TIMED_OUT))

		self.assertEqual(result, "deferred")
		alert.assert_not_called()
		self.assertEqual([r for r in journal.read_all() if r["event"] == "end"][-1]["outcome"], "deferred")

	def test_a_deferred_run_is_redone_as_a_catch_up_not_counted_as_done(self):
		planned = datetime(2026, 10, 5, 14, 4)
		now = datetime(2026, 10, 5, 14, 30)

		with mock.patch.object(journal, "_now", return_value=now):
			journal.record("default", "search", "end", planned=planned.isoformat(), outcome="deferred", exit_code=4)

		with mock.patch.object(ss, "day_plan", return_value=[planned]):
			due = ss.outstanding(now, "default")

		self.assertEqual([(d.planned, d.mode) for d in due], [(planned, "catch_up")])

	def test_an_ordinary_end_is_still_done(self):
		planned = datetime(2026, 10, 5, 14, 4)
		now = datetime(2026, 10, 5, 14, 30)

		with mock.patch.object(journal, "_now", return_value=now):
			journal.record("default", "search", "end", planned=planned.isoformat(), outcome="failed", exit_code=1)

		with mock.patch.object(ss, "day_plan", return_value=[planned]):
			self.assertEqual(ss.outstanding(now, "default"), [])

	def test_a_daily_run_that_found_the_profile_busy_is_not_marked_done_and_is_tried_again(self):
		codes = iter([run_lock.TIMED_OUT, 0])
		done = []
		sleeps = []

		class Stop(Exception):
			pass

		def mark_done(planned, owner):
			done.append(planned)

			raise Stop

		with mock.patch.object(daily_loop, "plan_next_run", side_effect=lambda now, owner: now), \
			mock.patch.object(daily_loop, "mark_done", side_effect=mark_done), \
			mock.patch.object(daily_loop.safety, "blocked", return_value=None), \
			mock.patch.object(daily_loop, "account_names", return_value=["default"]), \
			mock.patch.object(daily_loop.log_utils, "setup_logging"), \
			mock.patch.object(daily_loop.run_lock, "owner", return_value="default"), \
			mock.patch.object(daily_loop.notify, "send_each"), \
			mock.patch.object(daily_loop.time, "sleep", side_effect=sleeps.append), \
			mock.patch.object(daily_loop.subprocess, "run", side_effect=lambda *a, **k: SimpleNamespace(returncode=next(codes))), \
			self.assertRaises(Stop):
			daily_loop.main()

		ends = [r for r in journal.read_all() if r["kind"] == "daily" and r["event"] == "end"]

		self.assertEqual([r["outcome"] for r in ends], ["deferred", "ok"])
		self.assertEqual(len(done), 1)
		self.assertTrue(any(600 <= s <= 1200 for s in sleeps))


if __name__ == "__main__":
	unittest.main()
