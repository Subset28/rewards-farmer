"""Keeps the idle bot nearly free and each run polite to the rest of the NAS.

  * A virtual display (Xvfb) costs 35-70 MB for as long as it runs, and the schedulers sleep
    for most of the day. With REWARDS_LAZY_DISPLAY=1 one is started only for the length of
    a run and stopped afterwards. Without it nothing changes (the compose services that
    wrap their command in `with-xvfb` keep the old, always-on display).
  * The schedulers lower their own priority, which every browser they start inherits, so a
    run yields to Plex and the other containers instead of competing with them.
"""

import contextlib
import logging
import os
import shutil
import subprocess
import time

logger = logging.getLogger(__name__)

SCREEN = "1920x1080x24"
FIRST_DISPLAY = 99
LAST_DISPLAY = 140
START_WAIT_SECONDS = 10.0
NICE_LEVEL = 10


def lazy_display_wanted() -> bool:
	return os.environ.get("REWARDS_LAZY_DISPLAY", "").strip().lower() in ("1", "true", "yes")


def lower_priority(level: int = NICE_LEVEL) -> None:
	"""Be nicer than everything else on the machine. Children keep it. Silent if it cannot."""
	try:
		os.nice(level)
	except (AttributeError, OSError):
		pass


def _in_use(number: int) -> bool:
	return os.path.exists(f"/tmp/.X11-unix/X{number}") or os.path.exists(f"/tmp/.X{number}-lock")


def _free_display() -> int | None:
	span = LAST_DISPLAY - FIRST_DISPLAY + 1
	start = os.getpid() % span

	for step in range(span):
		number = FIRST_DISPLAY + (start + step) % span

		if not _in_use(number):
			return number

	return None


@contextlib.contextmanager
def virtual_display():
	"""A display for the browser for the length of the block, when asked for and not already there."""
	if not lazy_display_wanted() or os.environ.get("DISPLAY"):
		yield

		return

	xvfb = shutil.which("Xvfb")
	number = _free_display() if xvfb else None

	if not xvfb or number is None:
		logger.warning("No virtual display could be started; carrying on without one.")
		yield

		return

	process = subprocess.Popen(
		[xvfb, f":{number}", "-screen", "0", SCREEN, "-nolisten", "tcp"],
		stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
	)
	deadline = time.monotonic() + START_WAIT_SECONDS

	while not os.path.exists(f"/tmp/.X11-unix/X{number}") and process.poll() is None and time.monotonic() < deadline:
		time.sleep(0.05)

	if process.poll() is not None or not os.path.exists(f"/tmp/.X11-unix/X{number}"):
		logger.warning("The virtual display did not come up; carrying on without one.")
		_stop(process, number)
		yield

		return

	os.environ["DISPLAY"] = f":{number}"

	try:
		yield
	finally:
		os.environ.pop("DISPLAY", None)
		_stop(process, number)


def _stop(process: subprocess.Popen, number: int) -> None:
	try:
		process.terminate()
		process.wait(timeout=5)
	except Exception:
		with contextlib.suppress(Exception):
			process.kill()
			process.wait(timeout=5)

	for leftover in (f"/tmp/.X11-unix/X{number}", f"/tmp/.X{number}-lock"):
		with contextlib.suppress(OSError):
			os.unlink(leftover)
