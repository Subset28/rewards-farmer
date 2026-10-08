import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import supervisor


class Fake:
	def __init__(self, command, ends_after=None):
		self.command = command
		self.args = command
		self.polls = 0
		self.ends_after = ends_after
		self.code = None
		self.terminated = False
		self.killed = False

	def poll(self):
		self.polls += 1

		if self.terminated or self.killed:
			return -15

		if self.ends_after is not None and self.polls > self.ends_after:
			return 3

		return None

	def terminate(self):
		self.terminated = True

	def kill(self):
		self.killed = True


class TestSplit(unittest.TestCase):
	def test_commands_are_separated_by_double_dash(self):
		self.assertEqual(supervisor.split(["--", "env", "A=1", "python", "a.py", "--", "python", "b.py"]), [["env", "A=1", "python", "a.py"], ["python", "b.py"]])

	def test_empty_parts_are_dropped(self):
		self.assertEqual(supervisor.split(["--", "--", "x", "--"]), [["x"]])
		self.assertEqual(supervisor.split([]), [])


class TestRun(unittest.TestCase):
	def test_when_one_command_ends_the_others_are_stopped_and_its_code_is_returned(self):
		made = []

		def popen(command):
			made.append(Fake(command, ends_after=1 if command == ["b"] else None))

			return made[-1]

		code = supervisor.run([["a"], ["b"], ["c"]], popen=popen, sleep=lambda s: None, handle_signals=False)

		self.assertEqual(code, 3)
		self.assertTrue(made[0].terminated)
		self.assertTrue(made[2].terminated)

	def test_a_command_that_ends_with_zero_still_ends_the_container_with_a_failure_code(self):
		class Zero(Fake):
			def poll(self):
				return 0

		self.assertEqual(supervisor.run([["a"]], popen=lambda c: Zero(c), sleep=lambda s: None, handle_signals=False), 1)

	def test_nothing_to_run_is_an_error(self):
		self.assertEqual(supervisor.run([], handle_signals=False), 2)

	def test_stopping_forces_what_ignores_the_request(self):
		class Stubborn(Fake):
			def poll(self):
				return None if not self.killed else -9

		process = Stubborn(["a"])
		ticks = iter(range(0, 1000, 10))
		supervisor.stop_all([process], grace=5, sleep=lambda s: None, clock=lambda: next(ticks))

		self.assertTrue(process.terminated)
		self.assertTrue(process.killed)
