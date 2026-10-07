"""The health check says who has to act, once, and stays quiet when nothing is wrong."""

import os
import sys
import unittest
from datetime import datetime, timedelta
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import health

NOW = datetime(2026, 10, 10, 12, 0, 0)


def stamp(delta: timedelta) -> str:
	return (NOW - delta).isoformat(timespec="seconds")


def event(owner, hours_ago, **details):
	return {"t": stamp(timedelta(hours=hours_ago)), "owner": owner, "kind": "search", "event": "end", **details}


def reading(hours_ago, lifetime):
	return {"time": (NOW - timedelta(hours=hours_ago)).strftime("%Y-%m-%d %H:%M:%S"), "account": "x", "lifetime": lifetime}


class HealthCase(unittest.TestCase):
	def setUp(self):
		for patcher in (
			mock.patch.object(health.safety, "paused_for", return_value=None),
			mock.patch.object(health.journal, "events", return_value=[]),
			mock.patch.object(health.points_log, "history", return_value=[]),
			mock.patch.object(health.task_log, "history", return_value=[]),
		):
			patcher.start()
			self.addCleanup(patcher.stop)

	def find(self, names=("default",)):
		return health.findings(list(names), NOW)

	def keys(self, names=("default",)):
		return sorted(f.key for f in self.find(names))


class TestNothingWrong(HealthCase):
	def test_a_quiet_healthy_account_gets_no_message(self):
		with mock.patch.object(health.journal, "events", return_value=[event("default", 2, outcome="ok", exit_code=0)]), \
			mock.patch.object(health.points_log, "history", return_value=[reading(60, 1000), reading(30, 1200), reading(1, 1400)]):
			self.assertEqual(self.find(), [])


class TestFailedRuns(HealthCase):
	def test_two_failed_runs_need_claude(self):
		rows = [event("default", 5, outcome="failed", exit_code=1), event("default", 2, outcome="failed", exit_code=1)]

		with mock.patch.object(health.journal, "events", return_value=rows):
			found = self.find()

		self.assertEqual([f.key for f in found], ["failed_runs"])
		self.assertEqual(found[0].needs, health.NEEDS_CLAUDE)
		self.assertIn("Open Claude Code", found[0].message)

	def test_one_failure_is_not_worth_a_message(self):
		with mock.patch.object(health.journal, "events", return_value=[event("default", 2, outcome="failed", exit_code=1)]):
			self.assertEqual(self.find(), [])

	def test_the_brake_and_a_busy_profile_are_not_faults(self):
		rows = [event("default", 5, outcome="failed", exit_code=3), event("default", 2, outcome="failed", exit_code=4)]

		with mock.patch.object(health.journal, "events", return_value=rows):
			self.assertEqual(self.find(), [])

	def test_another_accounts_failures_are_not_counted(self):
		rows = [event("second", 5, outcome="failed", exit_code=1), event("second", 2, outcome="failed", exit_code=1)]

		with mock.patch.object(health.journal, "events", return_value=rows):
			self.assertEqual(self.keys(("default",)), [])
			self.assertEqual(self.keys(("second",)), ["failed_runs"])

	def test_a_scheduler_serving_several_accounts_counts_for_each(self):
		rows = [event("default,second", 5, outcome="failed", exit_code=1), event("default,second", 2, outcome="failed", exit_code=1)]

		with mock.patch.object(health.journal, "events", return_value=rows):
			self.assertEqual(self.keys(("second",)), ["failed_runs"])


class TestStalledPoints(HealthCase):
	def test_no_gain_over_two_days_needs_claude(self):
		with mock.patch.object(health.points_log, "history", return_value=[reading(60, 1000), reading(30, 1000), reading(1, 1000)]), \
			mock.patch.object(health.journal, "events", return_value=[event("default", 2, outcome="ok", exit_code=0)]):
			self.assertEqual(self.keys(), ["stalled"])

	def test_any_gain_is_fine(self):
		with mock.patch.object(health.points_log, "history", return_value=[reading(60, 1000), reading(1, 1005)]):
			self.assertEqual(self.keys(), [])

	def test_a_new_account_with_no_old_reading_is_left_alone(self):
		with mock.patch.object(health.points_log, "history", return_value=[reading(20, 100), reading(1, 100)]):
			self.assertEqual(self.keys(), [])

	def test_an_old_last_reading_is_not_called_stalled(self):
		with mock.patch.object(health.points_log, "history", return_value=[reading(200, 1000), reading(150, 1000)]):
			self.assertEqual(self.keys(), [])

	def test_an_unreadable_reading_is_ignored_not_an_error(self):
		with mock.patch.object(health.points_log, "history", return_value=[reading(60, 1000), {"time": "2026-10-10 11:00:00"}]):
			self.assertEqual(self.keys(), [])


class TestSilence(HealthCase):
	def test_nothing_recorded_for_a_day_and_a_half_needs_claude(self):
		with mock.patch.object(health.journal, "events", return_value=[event("default", 40, outcome="ok", exit_code=0)]):
			self.assertEqual(self.keys(), ["silent"])

	def test_recent_activity_is_fine(self):
		with mock.patch.object(health.journal, "events", return_value=[event("default", 20, outcome="ok", exit_code=0)]):
			self.assertEqual(self.keys(), [])

	def test_an_account_never_seen_is_not_silent(self):
		self.assertEqual(self.keys(), [])

	def test_a_paused_account_is_not_also_reported_silent(self):
		pause = {"kind": "captcha", "reason": "x", "time": stamp(timedelta(hours=1))}

		with mock.patch.object(health.safety, "paused_for", return_value=pause), \
			mock.patch.object(health.journal, "events", return_value=[event("default", 40, outcome="skipped")]):
			self.assertEqual(self.keys(), [])


def task_rows(task, completed_flags, tag_for_failure="SKIP"):
	return [{"t": f"2026-10-0{n % 9 + 1} 10:00:00", "account": "default", "task": task, "completed": ok, "tag": "OK" if ok else tag_for_failure, "gained": 5 if ok else 0} for n, ok in enumerate(completed_flags)]


class TestTaskBroken(HealthCase):
	def find_with(self, rows):
		with mock.patch.object(health.task_log, "history", return_value=rows):
			return self.find()

	def test_a_task_that_worked_and_now_fails_three_times_needs_claude(self):
		found = self.find_with(task_rows("Explore on Bing", [True] * 8 + [False] * 3))

		self.assertEqual([f.key for f in found], ["task:Explore on Bing"])
		self.assertEqual(found[0].needs, health.NEEDS_CLAUDE)
		self.assertIn("Explore on Bing", found[0].message)

	def test_two_failures_are_not_enough(self):
		self.assertEqual(self.find_with(task_rows("Explore on Bing", [True] * 8 + [False] * 2)), [])

	def test_a_task_that_never_worked_is_left_alone(self):
		# Visual search is skipped as "not available" on every run; that is how it is, not a break.
		self.assertEqual(self.find_with(task_rows("Visual search", [False] * 12)), [])

	def test_a_task_that_only_rarely_worked_is_left_alone(self):
		self.assertEqual(self.find_with(task_rows("Quests", [True, False, False, False, False, False, False, False, False, False, False, False])), [])

	def test_one_success_among_the_last_three_clears_it(self):
		self.assertEqual(self.find_with(task_rows("Explore on Bing", [True] * 8 + [False, True, False])), [])

	def test_each_task_is_judged_on_its_own(self):
		rows = task_rows("Bing daily set", [True] * 11) + task_rows("Explore on Bing", [True] * 8 + [False] * 3)

		self.assertEqual(self.keys_for(rows), ["task:Explore on Bing"])

	def keys_for(self, rows):
		with mock.patch.object(health.task_log, "history", return_value=rows):
			return self.keys()


def day_readings(totals):
	return [{"time": f"2026-10-{n + 1:02d} 10:00:00", "account": "x", "today": value, "lifetime": 1000 + n * 100} for n, value in enumerate(totals)]


class TestLowDays(HealthCase):
	def setUp(self):
		super().setUp()
		patcher = mock.patch.object(health.pacing, "in_ramp", return_value=False)
		patcher.start()
		self.addCleanup(patcher.stop)

	def keys_for(self, totals):
		with mock.patch.object(health.points_log, "history", return_value=day_readings(totals)):
			return self.keys()

	def test_two_very_low_days_in_a_row_need_claude(self):
		self.assertEqual(self.keys_for([230, 240, 220, 250, 235, 225, 245, 40, 35]), ["low_days"])

	def test_one_low_day_is_a_light_day_not_a_problem(self):
		self.assertEqual(self.keys_for([230, 240, 220, 250, 235, 225, 245, 240, 40]), [])

	def test_normal_days_are_fine(self):
		self.assertEqual(self.keys_for([230, 240, 220, 250, 235, 225, 245, 240, 200]), [])

	def test_an_account_that_never_made_much_is_left_alone(self):
		self.assertEqual(self.keys_for([20, 25, 22, 18, 24, 21, 23, 2, 1]), [])

	def test_not_enough_history_says_nothing(self):
		self.assertEqual(self.keys_for([230, 240, 40, 35]), [])

	def test_a_new_account_in_its_ramp_is_left_alone(self):
		with mock.patch.object(health.pacing, "in_ramp", return_value=True):
			self.assertEqual(self.keys_for([230, 240, 220, 250, 235, 225, 245, 40, 35]), [])


class TestPause(HealthCase):
	def pause(self, hours):
		return {"kind": "captcha", "reason": "human check", "time": (NOW - timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")}

	def test_a_long_pause_needs_the_person(self):
		with mock.patch.object(health.safety, "paused_for", return_value=self.pause(20)):
			found = self.find()

		self.assertEqual([f.key for f in found], ["paused"])
		self.assertEqual(found[0].needs, health.NEEDS_YOU)
		self.assertIn("safety.py clear default", found[0].message)

	def test_a_fresh_pause_has_already_alerted_so_it_is_left_alone(self):
		with mock.patch.object(health.safety, "paused_for", return_value=self.pause(2)):
			self.assertEqual(self.find(), [])

	def test_an_unreadable_pause_time_is_left_alone(self):
		with mock.patch.object(health.safety, "paused_for", return_value={"kind": "unknown", "reason": "x", "time": ""}):
			self.assertEqual(self.find(), [])


class TestSendingOnce(HealthCase):
	def setUp(self):
		super().setUp()
		self.rows = [event("default", 5, outcome="failed", exit_code=1), event("default", 2, outcome="failed", exit_code=1)]
		self.send = mock.Mock()

	def run_check(self, now):
		with mock.patch.object(health.journal, "events", return_value=self.rows):
			return health.check(["default"], now, self.send)

	def test_it_is_sent_to_that_accounts_channel_at_high_priority(self):
		self.run_check(NOW)

		self.assertEqual(self.send.call_count, 1)
		self.assertEqual(self.send.call_args.kwargs["account"], "default")
		self.assertEqual(self.send.call_args.kwargs["priority"], "high")
		self.assertIn("NEEDS CLAUDE", self.send.call_args.args[0])

	def test_the_same_problem_is_not_sent_again_within_a_day(self):
		self.run_check(NOW)
		self.run_check(NOW + timedelta(hours=3))

		self.assertEqual(self.send.call_count, 1)

	def test_it_is_sent_again_a_day_later_if_still_there(self):
		self.run_check(NOW)

		# A day later the same trouble is still happening: failures from the last few hours.
		self.rows = [
			{**row, "t": (datetime.fromisoformat(row["t"]) + timedelta(hours=25)).isoformat(timespec="seconds")}
			for row in self.rows
		]
		self.run_check(NOW + timedelta(hours=25))

		self.assertEqual(self.send.call_count, 2)

	def test_a_problem_that_clears_and_returns_is_sent_again(self):
		self.run_check(NOW)
		self.rows = []
		self.run_check(NOW + timedelta(hours=2))
		self.rows = [event("default", 5, outcome="failed", exit_code=1), event("default", 2, outcome="failed", exit_code=1)]
		self.run_check(NOW + timedelta(hours=4))

		self.assertEqual(self.send.call_count, 2)

	def test_a_damaged_state_file_does_not_stop_a_message(self):
		with open(health.STATE_FILE, "w") as handle:
			handle.write("{broken")

		self.run_check(NOW)

		self.assertEqual(self.send.call_count, 1)

	def test_a_failing_sender_does_not_raise(self):
		self.send.side_effect = RuntimeError("discord down")

		self.assertEqual(self.run_check(NOW), [])


if __name__ == "__main__":
	unittest.main()
