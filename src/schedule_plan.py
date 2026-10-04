"""Today's plan, kept across restarts.

Both schedulers draw random run times, and a restart used to redraw them: a
deploy at 13:11 left one search run for the rest of the day, and a restart
between the 09:00 anchor and that day's drawn start skipped the daily run
altogether. The plan is written to data-dir/schedule_plan.json, per scheduler
(a second account's scheduler has its own), and read back on start.

Reading never raises: a missing or damaged file is no plan, and the caller draws
a fresh one. Writing never raises either.
"""

import json
import logging
import os

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


def read(kind: str, owner: str) -> dict:
	"""The saved plan of this kind for this scheduler, or {}."""
	section = _load().get(kind)
	plan = section.get(owner) if isinstance(section, dict) else None

	return plan if isinstance(plan, dict) else {}


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
