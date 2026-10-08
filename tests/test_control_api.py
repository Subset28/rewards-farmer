import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import control_api
import safety


class TestAuthorisation(unittest.TestCase):
	def test_no_token_set_means_nobody_is_let_in(self):
		self.assertFalse(control_api.authorised("Bearer anything", expected=""))

	def test_the_right_token_passes_and_others_do_not(self):
		token = "x" * 30

		self.assertTrue(control_api.authorised(f"Bearer {token}", expected=token))
		self.assertFalse(control_api.authorised(f"Bearer {token}y", expected=token))
		self.assertFalse(control_api.authorised(token, expected=token))
		self.assertFalse(control_api.authorised(None, expected=token))

	def test_a_short_token_stops_it_from_starting(self):
		with mock.patch.dict(os.environ, {"CONTROL_TOKEN": "short"}):
			self.assertEqual(control_api.main(), 1)

		with mock.patch.dict(os.environ, {"CONTROL_TOKEN": ""}):
			self.assertEqual(control_api.main(), 1)


class TestLogs(unittest.TestCase):
	def setUp(self):
		self.folder = tempfile.TemporaryDirectory()
		self.addCleanup(self.folder.cleanup)
		patch = mock.patch.object(control_api, "LOG_DIR", self.folder.name)
		patch.start()
		self.addCleanup(patch.stop)

		with open(os.path.join(self.folder.name, "scheduler.log"), "w") as handle:
			handle.write("\n".join(f"line {i}" for i in range(300)) + "\n")

		with open(os.path.join(self.folder.name, "notes.txt"), "w") as handle:
			handle.write("secret\n")

	def test_only_log_files_are_listed(self):
		self.assertEqual(control_api.log_files(), ["scheduler.log"])

	def test_the_end_of_a_log_is_returned(self):
		code, lines = control_api.handle("GET", "/logs/scheduler.log?lines=3")

		self.assertEqual((code, lines), (200, ["line 297", "line 298", "line 299"]))

	def test_other_files_and_paths_are_refused(self):
		for name in ("notes.txt", "../PAUSED", "..%2fPAUSED", "scheduler.log/../../x", "missing.log"):
			code, _ = control_api.handle("GET", f"/logs/{name}")
			self.assertEqual(code, 404, name)

	def test_the_number_of_lines_is_capped(self):
		code, lines = control_api.handle("GET", "/logs/scheduler.log?lines=100000")

		self.assertEqual((code, len(lines)), (200, 300))


class TestPause(unittest.TestCase):
	def setUp(self):
		self.folder = tempfile.TemporaryDirectory()
		self.addCleanup(self.folder.cleanup)
		patch = mock.patch.object(safety, "PAUSE_FILE", os.path.join(self.folder.name, "PAUSED"))
		patch.start()
		self.addCleanup(patch.stop)
		names = mock.patch.object(control_api, "account_names", return_value=["default", "second"])
		names.start()
		self.addCleanup(names.stop)

	def test_pausing_and_resuming_everything(self):
		code, answer = control_api.handle("POST", "/pause", {"reason": "testing"})

		self.assertEqual(code, 200)
		self.assertEqual(answer["paused"]["kind"], "manual")
		self.assertIsNotNone(safety.paused())

		code, answer = control_api.handle("POST", "/resume", {})

		self.assertEqual((code, answer["cleared"]), (200, True))
		self.assertIsNone(safety.paused())

	def test_one_account_can_be_paused_alone(self):
		control_api.handle("POST", "/pause", {"account": "second"})

		self.assertIsNotNone(safety.paused_for("second"))
		self.assertIsNone(safety.paused_for("default"))

	def test_an_unknown_account_is_refused(self):
		code, answer = control_api.handle("POST", "/pause", {"account": "../etc"})

		self.assertEqual(code, 400)
		self.assertIsNone(safety.paused())

	def test_an_existing_pause_is_kept_not_overwritten(self):
		control_api.handle("POST", "/pause", {"reason": "first"})
		_, answer = control_api.handle("POST", "/pause", {"reason": "second"})

		self.assertEqual(answer["paused"]["reason"], "first")

	def test_nothing_else_can_be_posted(self):
		for route in ("/deploy", "/run", "/exec", "/env"):
			self.assertEqual(control_api.handle("POST", route, {})[0], 404, route)

	def test_the_answers_are_json(self):
		json.dumps(control_api.handle("POST", "/pause", {"reason": "x"})[1], default=str)


class TestTasksAndSettings(unittest.TestCase):
	def test_a_task_that_fails_three_runs_running_is_failing_now(self):
		from datetime import datetime
		rows = [
			{"t": "2026-10-08 09:00:00", "account": "default", "task": "Visual search", "completed": False, "tag": "SKIP"},
			{"t": "2026-10-07 09:00:00", "account": "default", "task": "Visual search", "completed": False, "tag": "SKIP"},
			{"t": "2026-10-06 09:00:00", "account": "default", "task": "Visual search", "completed": False, "tag": "SKIP"},
			{"t": "2026-10-08 09:00:00", "account": "default", "task": "Bing daily set", "completed": True, "tag": "OK"},
		]

		with mock.patch.object(control_api.task_log, "history", return_value=rows), mock.patch.object(control_api.clock, "now", return_value=datetime(2026, 10, 8, 12, 0, 0)):
			view = control_api.tasks_view(14)["default"]

		self.assertTrue(view["Visual search"]["failing_now"])
		self.assertEqual(view["Visual search"]["completed"], 0)
		self.assertFalse(view["Bing daily set"]["failing_now"])

	def test_old_runs_are_left_out(self):
		from datetime import datetime
		rows = [{"t": "2026-01-01 09:00:00", "account": "default", "task": "Quests", "completed": True, "tag": "OK"}]

		with mock.patch.object(control_api.task_log, "history", return_value=rows), mock.patch.object(control_api.clock, "now", return_value=datetime(2026, 10, 8, 12, 0, 0)):
			self.assertEqual(control_api.tasks_view(14), {})

	def test_settings_say_whether_a_secret_is_set_but_never_what_it_is(self):
		env = {"CONTROL_TOKEN": "t" * 40, "NOTIFY_URL": "https://discord.com/api/webhooks/123/SECRETVALUE", "REWARDS_MIN_DAILY_FRACTION": "0.65", "BACKUP_GITHUB_TOKEN": "ghp_SECRET"}

		with mock.patch.dict(os.environ, env), mock.patch.object(control_api, "account_names", return_value=["default"]):
			view = control_api.settings_view()

		dump = json.dumps(view, default=str)

		self.assertTrue(view["present"]["CONTROL_TOKEN"])
		self.assertTrue(view["present"]["NOTIFY_URL"])
		self.assertEqual(view["environment"]["REWARDS_MIN_DAILY_FRACTION"], "0.65")
		self.assertNotIn("SECRETVALUE", dump)
		self.assertNotIn("ghp_SECRET", dump)
		self.assertNotIn("t" * 40, dump)

	def test_snapshots_are_listed_and_read_through_the_interface(self):
		with mock.patch.object(control_api.snapshot, "names", return_value=["a.json"]), mock.patch.object(control_api.snapshot, "read", return_value={"buttons": ["x"]}):
			self.assertEqual(control_api.handle("GET", "/snapshots"), (200, ["a.json"]))
			self.assertEqual(control_api.handle("GET", "/snapshots/a.json")[1]["buttons"], ["x"])

		self.assertEqual(control_api.handle("GET", "/snapshots/..%2f..%2fPAUSED")[0], 404)
