"""An account runs inside its own VPN namespace, or not at all, and never on the real connection."""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import accounts
import isolation


class IsolationTestCase(unittest.TestCase):
	def setUp(self):
		directory = tempfile.TemporaryDirectory()
		self.addCleanup(directory.cleanup)
		self.state = Path(directory.name) / "namespaces.json"
		patcher = mock.patch.object(isolation, "STATE_FILE", self.state)
		patcher.start()
		self.addCleanup(patcher.stop)
		environment = mock.patch.dict(os.environ, {}, clear=False)
		environment.start()
		self.addCleanup(environment.stop)
		os.environ.pop(isolation.CHILD_ENV, None)

		self.in_process = mock.Mock(return_value=True)
		self.runner = mock.Mock(return_value=SimpleNamespace(returncode=0))

	def write(self, state):
		self.state.write_text(json.dumps(state))

	def run_it(self, account="alpha"):
		return isolation.run(account, "src/main.py", self.in_process, runner=self.runner)


class TestWithoutVpn(IsolationTestCase):
	def test_with_no_state_file_the_account_is_worked_in_process_as_before(self):
		self.assertTrue(self.run_it())

		self.in_process.assert_called_once()
		self.runner.assert_not_called()

	def test_the_result_of_the_in_process_run_is_returned(self):
		self.in_process.return_value = False

		self.assertFalse(self.run_it())

	def test_the_child_inside_a_namespace_never_starts_another(self):
		self.write({"alpha": {"netns": "/proc/9/ns/net", "up": True}})
		os.environ[isolation.CHILD_ENV] = "1"

		self.assertTrue(self.run_it())

		self.in_process.assert_called_once()
		self.runner.assert_not_called()


class TestWithVpn(IsolationTestCase):
	UP = {"netns": "/proc/42/ns/net", "up": True, "exit": "192.0.2.7", "reason": "", "timezone": ""}

	def test_an_account_with_no_namespace_is_not_run_at_all(self):
		self.write({"beta": self.UP})

		self.assertFalse(self.run_it("alpha"))

		self.in_process.assert_not_called()
		self.runner.assert_not_called()

	def test_a_damaged_state_file_runs_nothing(self):
		self.state.write_text("{not json")

		self.assertFalse(self.run_it())

		self.in_process.assert_not_called()
		self.runner.assert_not_called()

	def test_an_account_whose_tunnel_is_down_is_skipped_never_run_on_the_real_connection(self):
		self.write({"alpha": {**self.UP, "up": False, "reason": "the tunnel did not come up"}})

		self.assertFalse(self.run_it())

		self.in_process.assert_not_called()
		self.runner.assert_not_called()

	def test_a_good_account_runs_as_a_child_inside_its_namespace(self):
		self.write({"alpha": self.UP})

		self.assertTrue(self.run_it())

		command = self.runner.call_args.args[0]

		self.assertEqual(command[:2], ["nsenter", "--net=/proc/42/ns/net"])
		self.assertEqual(command[-2:], [sys.executable, "src/main.py"])
		self.in_process.assert_not_called()

	def test_the_child_is_started_without_the_capabilities_that_could_change_the_firewall(self):
		self.write({"alpha": self.UP})
		self.run_it()
		command = self.runner.call_args.args[0]

		self.assertIn("setpriv", command)
		self.assertIn("--bounding-set=-sys_admin,-net_admin,-net_raw", command)

	def test_the_child_works_exactly_one_account_and_knows_it_is_inside(self):
		self.write({"alpha": self.UP})
		self.run_it("alpha")
		env = self.runner.call_args.kwargs["env"]

		self.assertEqual(env["REWARDS_ACCOUNTS"], "alpha")
		self.assertEqual(env[isolation.CHILD_ENV], "1")

	def test_the_browser_gets_the_timezone_of_the_accounts_location(self):
		self.write({"alpha": {**self.UP, "timezone": "America/Chicago"}})
		self.run_it()

		self.assertEqual(self.runner.call_args.kwargs["env"]["TZ"], "America/Chicago")

	def test_without_a_timezone_the_containers_own_is_kept(self):
		self.write({"alpha": self.UP})

		with mock.patch.dict(os.environ, {"TZ": "America/New_York"}):
			self.run_it()

		self.assertEqual(self.runner.call_args.kwargs["env"]["TZ"], "America/New_York")

	def test_a_child_that_fails_is_a_run_that_did_not_start(self):
		self.write({"alpha": self.UP})
		self.runner.return_value = SimpleNamespace(returncode=1)

		self.assertFalse(self.run_it())


class TestDefaultAccountName(unittest.TestCase):
	def test_listing_default_means_the_unnamed_profile_not_a_new_empty_one(self):
		with mock.patch.dict(os.environ, {accounts.ENV_VAR: "default,second"}):
			found = accounts.configured()

		self.assertEqual([a.name for a in found], ["default", "second"])
		self.assertTrue(found[0].is_default)
		self.assertFalse(found[1].is_default)

	def test_default_is_recognised_whatever_its_case_and_only_once(self):
		with mock.patch.dict(os.environ, {accounts.ENV_VAR: "Default,DEFAULT"}):
			found = accounts.configured()

		self.assertEqual(len(found), 1)
		self.assertTrue(found[0].is_default)


if __name__ == "__main__":
	unittest.main()
