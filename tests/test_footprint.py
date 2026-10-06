"""The lazy display and the lowered priority: nothing changes unless asked for."""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import footprint


class TestVirtualDisplay(unittest.TestCase):
	def test_off_by_default_it_does_nothing(self):
		with mock.patch.dict(os.environ, {"REWARDS_LAZY_DISPLAY": ""}, clear=False), mock.patch.object(footprint.subprocess, "Popen") as popen:
			with footprint.virtual_display():
				pass

		popen.assert_not_called()

	def test_an_existing_display_is_left_alone(self):
		with mock.patch.dict(os.environ, {"REWARDS_LAZY_DISPLAY": "1", "DISPLAY": ":5"}), mock.patch.object(footprint.subprocess, "Popen") as popen:
			with footprint.virtual_display():
				self.assertEqual(os.environ["DISPLAY"], ":5")

		popen.assert_not_called()

	def test_without_xvfb_it_carries_on(self):
		env = {k: v for k, v in os.environ.items() if k != "DISPLAY"}
		env["REWARDS_LAZY_DISPLAY"] = "1"

		with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(footprint.shutil, "which", return_value=None):
			with footprint.virtual_display():
				self.assertNotIn("DISPLAY", os.environ)

	def test_a_display_is_set_for_the_block_and_stopped_after(self):
		env = {k: v for k, v in os.environ.items() if k != "DISPLAY"}
		env["REWARDS_LAZY_DISPLAY"] = "1"
		process = mock.Mock()
		process.poll.return_value = None

		with mock.patch.dict(os.environ, env, clear=True), \
			mock.patch.object(footprint.shutil, "which", return_value="/usr/bin/Xvfb"), \
			mock.patch.object(footprint, "_free_display", return_value=101), \
			mock.patch.object(footprint.os.path, "exists", return_value=True), \
			mock.patch.object(footprint.subprocess, "Popen", return_value=process) as popen, \
			mock.patch.object(footprint, "_stop") as stop:
			with footprint.virtual_display():
				self.assertEqual(os.environ["DISPLAY"], ":101")

			self.assertNotIn("DISPLAY", os.environ)

		self.assertIn(":101", popen.call_args.args[0])
		self.assertIn(footprint.SCREEN, popen.call_args.args[0])
		stop.assert_called_once_with(process, 101)

	def test_the_display_is_stopped_even_when_the_run_raises(self):
		env = {k: v for k, v in os.environ.items() if k != "DISPLAY"}
		env["REWARDS_LAZY_DISPLAY"] = "1"
		process = mock.Mock()
		process.poll.return_value = None

		with mock.patch.dict(os.environ, env, clear=True), \
			mock.patch.object(footprint.shutil, "which", return_value="/usr/bin/Xvfb"), \
			mock.patch.object(footprint, "_free_display", return_value=101), \
			mock.patch.object(footprint.os.path, "exists", return_value=True), \
			mock.patch.object(footprint.subprocess, "Popen", return_value=process), \
			mock.patch.object(footprint, "_stop") as stop:
			with self.assertRaises(RuntimeError):
				with footprint.virtual_display():
					raise RuntimeError("run failed")

		stop.assert_called_once()

	def test_a_display_that_does_not_come_up_is_cleaned_up_and_the_run_goes_on(self):
		env = {k: v for k, v in os.environ.items() if k != "DISPLAY"}
		env["REWARDS_LAZY_DISPLAY"] = "1"
		process = mock.Mock()
		process.poll.return_value = 1

		with mock.patch.dict(os.environ, env, clear=True), \
			mock.patch.object(footprint.shutil, "which", return_value="/usr/bin/Xvfb"), \
			mock.patch.object(footprint, "_free_display", return_value=101), \
			mock.patch.object(footprint.subprocess, "Popen", return_value=process), \
			mock.patch.object(footprint, "_stop") as stop:
			with footprint.virtual_display():
				self.assertNotIn("DISPLAY", os.environ)

		stop.assert_called_once()

	def test_a_busy_display_number_is_skipped(self):
		taken = {"/tmp/.X11-unix/X99", "/tmp/.X99-lock"}

		with mock.patch.object(footprint.os.path, "exists", side_effect=lambda p: p in taken), mock.patch.object(footprint.os, "getpid", return_value=0):
			self.assertEqual(footprint._free_display(), 100)


class TestPriority(unittest.TestCase):
	def test_it_asks_for_a_nicer_priority(self):
		with mock.patch.object(footprint.os, "nice", create=True) as nice:
			footprint.lower_priority()

		nice.assert_called_once_with(footprint.NICE_LEVEL)

	def test_it_never_raises(self):
		with mock.patch.object(footprint.os, "nice", create=True, side_effect=OSError("no")):
			footprint.lower_priority()


if __name__ == "__main__":
	unittest.main()
