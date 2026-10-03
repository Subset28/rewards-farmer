"""One run at a time on the profile.

The daily run and the search runs are separate processes in separate containers
that share one browser profile, and Edge allows one browser per profile. When
two overlap, the second fails to start ("profile in use") and its run is lost:
the 09:31 search run on 3 Oct died that way a few seconds before the daily run
had closed its browser.

A run takes this lock for its whole length, so an overlapping one waits for the
other to finish instead of colliding. The lock is an operating system file lock,
so it is released the moment the holder exits, however it exits: a killed
container cannot leave it behind.

Holding it also makes the profile's own Singleton* files stale by definition,
since no other bot browser can be running. A killed container leaves them
pointing at its hostname, and Edge then refuses every later start, so they are
cleared once the lock is held.
"""

import contextlib
import glob
import logging
import os
import time

from constants import USER_DATA_DIR

try:
	import fcntl
except ImportError:  # Windows
	fcntl = None

try:
	import msvcrt
except ImportError:
	msvcrt = None

logger = logging.getLogger(__name__)

LOCK_FILE = os.path.join(USER_DATA_DIR, ".run.lock")

# A daily run takes about 7 minutes and a search run 1-3, so this is generous.
DEFAULT_WAIT_MINUTES = 25

# Exit code for a run that gave up waiting.
TIMED_OUT = 4

STALE_PATTERNS = ("Singleton*",)


class RunLockTimeout(RuntimeError):
	"""Another run held the profile for the whole wait."""


def _try_lock(handle) -> bool:
	try:
		if fcntl:
			fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
		elif msvcrt:
			handle.seek(0)
			msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
		else:
			return True
	except OSError:
		return False

	return True


def _unlock(handle) -> None:
	try:
		if fcntl:
			fcntl.flock(handle, fcntl.LOCK_UN)
		elif msvcrt:
			handle.seek(0)
			msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
	except OSError:
		pass


def clear_stale_browser_locks(root: str | None = None) -> list[str]:
	"""Remove Edge's Singleton* files under the profile directory; returns what went."""
	root = root or USER_DATA_DIR
	removed = []

	# The profile directory itself, and one level down for named accounts.
	for directory in [root] + [d for d in glob.glob(os.path.join(root, "*")) if os.path.isdir(d)]:
		for pattern in STALE_PATTERNS:
			for path in glob.glob(os.path.join(directory, pattern)):
				try:
					os.remove(path)
					removed.append(path)
				except OSError as exc:
					logger.warning("Could not remove the stale browser lock %s: %s", path, exc)

	return removed


@contextlib.contextmanager
def run_lock(wait_seconds: float | None = None, poll_seconds: float = 5.0, lock_file: str | None = None):
	"""Hold the profile for the body, waiting up to `wait_seconds` for another run to finish."""
	lock_file = lock_file or LOCK_FILE

	if wait_seconds is None:
		wait_seconds = float(os.environ.get("REWARDS_LOCK_WAIT_MINUTES", DEFAULT_WAIT_MINUTES)) * 60

	os.makedirs(os.path.dirname(lock_file), exist_ok=True)

	handle = open(lock_file, "a+")
	deadline = time.monotonic() + wait_seconds
	announced = False

	try:
		while not _try_lock(handle):
			if time.monotonic() >= deadline:
				raise RunLockTimeout(f"another run held the profile for {wait_seconds / 60:.0f} minutes")

			if not announced:
				logger.info("Another run is using the profile. Waiting for it to finish.")
				announced = True

			time.sleep(poll_seconds)

		if announced:
			logger.info("The other run finished. Starting.")

		removed = clear_stale_browser_locks(os.path.dirname(lock_file))

		if removed:
			logger.warning("Removed %d stale browser lock file(s) left by a run that did not exit cleanly.", len(removed))

		yield
	finally:
		_unlock(handle)
		handle.close()


def run_locked(main) -> int:
	"""Run `main()` while holding the profile; a run that cannot get it exits TIMED_OUT."""
	try:
		with run_lock():
			return main()
	except RunLockTimeout as exc:
		logger.error("[FAIL] Not running: %s. The run was skipped.", exc)

		return TIMED_OUT
