"""Today's plan, kept across restarts.

Both schedulers draw random run times, and a restart used to redraw them: a
deploy at 13:11 left one search run for the rest of the day, and a restart
between the 09:00 anchor and that day's drawn start skipped the daily run
altogether. The plan is written to data-dir/schedule_plan.json, per scheduler
(a second account's scheduler has its own), and read back on start.

Reading never raises: a missing or damaged file is no plan, and the caller draws
a fresh one. Writing never raises either.
"""

import contextlib
import json
import logging
import os
import time

from constants import USER_DATA_DIR

logger = logging.getLogger(__name__)

PLAN_FILE = os.path.join(USER_DATA_DIR, "schedule_plan.json")


def _load() -> dict:
	try:
		with open(PLAN_FILE, encoding="utf-8") as handle:
			data = json.load(handle)

		return data if isinstance(data, dict) else {}
	except (OSError, ValueError):
		return {}


def owners(kind: str) -> list[str]:
	"""Every owner that has a saved plan of this kind (any scheduler in the container wrote it)."""
	section = _load().get(kind)

	return sorted(section) if isinstance(section, dict) else []


def read(kind: str, owner: str) -> dict:
	"""The saved plan of this kind for this scheduler, or {}."""
	section = _load().get(kind)
	plan = section.get(owner) if isinstance(section, dict) else None

	return plan if isinstance(plan, dict) else {}


LOCK_STALE_SECONDS = 30.0


@contextlib.contextmanager
def planning(timeout: float = 15.0):
	"""One scheduler at a time reads the others' plans, draws its own and saves it.

	All the loops of one container start a new day at the same instant, so without taking turns none of them
	can see the others' plans yet, and the runs they draw land close together (10:13 and 10:19 happened). A lock
	older than LOCK_STALE_SECONDS is a crashed scheduler's and is cleared; after `timeout` it goes ahead anyway,
	because a plan drawn without the others in view is better than no plan."""
	lock = PLAN_FILE + ".lock"
	deadline = time.monotonic() + timeout
	held = False

	while True:
		try:
			os.makedirs(os.path.dirname(lock), exist_ok=True)
			os.close(os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
			held = True

			break
		except FileExistsError:
			try:
				if time.time() - os.path.getmtime(lock) > LOCK_STALE_SECONDS:
					os.remove(lock)

					continue
			except OSError:
				pass

			if time.monotonic() > deadline:
				break

			time.sleep(0.05)
		except OSError:
			break

	try:
		yield
	finally:
		if held:
			try:
				os.remove(lock)
			except OSError:
				pass


def write(kind: str, owner: str, plan: dict) -> None:
	data = _load()
	section = data.get(kind) if isinstance(data.get(kind), dict) else {}
	section[owner] = plan
	data[kind] = section

	try:
		os.makedirs(os.path.dirname(PLAN_FILE), exist_ok=True)

		with open(PLAN_FILE, "w", encoding="utf-8") as handle:
			json.dump(data, handle)
	except OSError as exc:
		logger.debug("Could not save the schedule: %s", exc)
