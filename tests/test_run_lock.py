"""Tests for the one-run-at-a-time lock on the profile.

	python -m unittest discover -s tests
"""

import os
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import run_lock


class LockTestCase(unittest.TestCase):
	def setUp(self):
		directory = tempfile.TemporaryDirectory()
		self.addCleanup(directory.cleanup)
		self.directory = directory.name
		self.lock_file = os.path.join(self.directory, ".run.lock")

	def touch(self, *parts):
		path = os.path.join(self.directory, *parts)
		os.makedirs(os.path.dirname(path), exist_ok=True)

		with open(path, "w") as handle:
			handle.write("x")

		return path


class TestRunLock(LockTestCase):
	def test_it_is_taken_and_released(self):
		with run_lock.run_lock(wait_seconds=1, lock_file=self.lock_file):
			pass

		with run_lock.run_lock(wait_seconds=1, lock_file=self.lock_file):
			pass

	def test_a_second_run_waits_and_then_gives_up(self):
		with run_lock.run_lock(wait_seconds=1, lock_file=self.lock_file):
			started = time.monotonic()

			with self.assertRaises(run_lock.RunLockTimeout):
				with run_lock.run_lock(wait_seconds=0.6, poll_seconds=0.1, lock_file=self.lock_file):
					self.fail("two runs held the profile at once")

			self.assertGreaterEqual(time.monotonic() - started, 0.5)

	def test_a_waiting_run_starts_once_the_first_finishes(self):
		order = []
		holding = threading.Event()

		def first():
			with run_lock.run_lock(wait_seconds=5, lock_file=self.lock_file):
				order.append("first in")
				holding.set()
				time.sleep(0.4)
				order.append("first out")

		thread = threading.Thread(target=first)
		thread.start()
		holding.wait(5)

		with run_lock.run_lock(wait_seconds=5, poll_seconds=0.05, lock_file=self.lock_file):
			order.append("second in")

		thread.join()
		self.assertEqual(order, ["first in", "first out", "second in"])

	def test_the_lock_is_released_when_the_body_raises(self):
		with self.assertRaises(ValueError):
			with run_lock.run_lock(wait_seconds=1, lock_file=self.lock_file):
				raise ValueError("boom")

		with run_lock.run_lock(wait_seconds=1, lock_file=self.lock_file):
			pass

	def test_the_wait_comes_from_the_environment_in_minutes(self):
		with mock.patch.dict(os.environ, {"REWARDS_LOCK_WAIT_MINUTES": "0.01"}):
			with run_lock.run_lock(lock_file=self.lock_file):
				started = time.monotonic()

				with self.assertRaises(run_lock.RunLockTimeout):
					with run_lock.run_lock(poll_seconds=0.1, lock_file=self.lock_file):
						pass

				self.assertLess(time.monotonic() - started, 5)


class TestStaleBrowserLocks(LockTestCase):
	def test_singleton_files_are_removed_and_everything_else_is_kept(self):
		stale = [self.touch("SingletonLock"), self.touch("SingletonCookie"), self.touch("SingletonSocket"), self.touch("work", "SingletonLock")]
		kept = [self.touch("Default", "Cookies"), self.touch("Local State"), self.touch("work", "Preferences")]

		removed = run_lock.clear_stale_browser_locks(self.directory)

		self.assertEqual(sorted(removed), sorted(stale))
		for path in stale:
			self.assertFalse(os.path.exists(path))
		for path in kept:
			self.assertTrue(os.path.exists(path))

	def test_nothing_to_remove_is_fine(self):
		self.assertEqual(run_lock.clear_stale_browser_locks(self.directory), [])

	def test_holding_the_lock_clears_a_stale_browser_lock_first(self):
		stale = self.touch("SingletonLock")

		with self.assertLogs(run_lock.logger, level="WARNING"):
			with run_lock.run_lock(wait_seconds=1, lock_file=self.lock_file):
				self.assertFalse(os.path.exists(stale))

	def test_a_run_that_could_not_get_the_lock_leaves_the_browser_files_alone(self):
		# Not holding the lock means another run may be using that browser.
		live = self.touch("SingletonLock")

		with run_lock.run_lock(wait_seconds=1, lock_file=self.lock_file):
			live = self.touch("SingletonLock")

			with self.assertRaises(run_lock.RunLockTimeout):
				with run_lock.run_lock(wait_seconds=0.3, poll_seconds=0.1, lock_file=self.lock_file):
					pass

			self.assertTrue(os.path.exists(live))


class TestRunLocked(LockTestCase):
	def test_it_returns_what_the_run_returns(self):
		with mock.patch.object(run_lock, "LOCK_FILE", self.lock_file):
			self.assertEqual(run_lock.run_locked(lambda: 7), 7)

	def test_a_run_that_cannot_get_the_profile_exits_with_the_timeout_code(self):
		ran = []

		with mock.patch.object(run_lock, "LOCK_FILE", self.lock_file), \
			mock.patch.dict(os.environ, {"REWARDS_LOCK_WAIT_MINUTES": "0.005"}):
			with run_lock.run_lock(wait_seconds=1, lock_file=self.lock_file):
				with self.assertLogs(run_lock.logger, level="ERROR"):
					code = run_lock.run_locked(lambda: ran.append("ran") or 0)

		self.assertEqual(code, run_lock.TIMED_OUT)
		self.assertEqual(ran, [])


if __name__ == "__main__":
	unittest.main()
