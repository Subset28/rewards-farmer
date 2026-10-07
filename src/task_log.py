"""A record of how each task went in each daily run, so a task that has quietly broken can be seen.

Microsoft changes its pages without warning. When it does, the usual sign is not a crash: a task
that has always worked starts failing every day, the run still ends "ok", and the points just get
smaller. health.py reads this file and says so after three failures in a row of a task that used
to work.

One line per task per run in data-dir/task_log.jsonl: when, account, task, whether it completed,
its outcome tag ("OK", "SKIP", "FAIL"...), and the points it seemed to earn (None if it could not
be read; credit can land on the next task's line, so this is only a hint and nothing relies on
it). The oldest lines are dropped as the file grows. Nothing here ever raises.
"""

import json
import logging
import os

import clock
from constants import USER_DATA_DIR

logger = logging.getLogger(__name__)

LOG_FILE = os.path.join(USER_DATA_DIR, "task_log.jsonl")
MAX_LINES = 3000


def record(account: str, task: str, completed: bool, tag: str, gained: int | None = None) -> None:
	line = {"t": clock.stamp(), "account": account, "task": task, "completed": bool(completed), "tag": tag, "gained": gained}

	try:
		os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)

		with open(LOG_FILE, "a", encoding="utf-8") as handle:
			handle.write(json.dumps(line) + "\n")

		_trim()
	except (OSError, TypeError, ValueError) as exc:
		logger.debug("Could not write the task log: %s", exc)


def _trim() -> None:
	try:
		with open(LOG_FILE, encoding="utf-8") as handle:
			lines = handle.readlines()

		if len(lines) > MAX_LINES:
			with open(LOG_FILE, "w", encoding="utf-8") as handle:
				handle.writelines(lines[-(MAX_LINES // 2):])
	except OSError:
		pass


def history(account: str | None = None) -> list[dict]:
	"""Every readable line, oldest first. A damaged line is skipped."""
	rows = []

	try:
		with open(LOG_FILE, encoding="utf-8", errors="replace") as handle:
			for line in handle:
				try:
					row = json.loads(line)
				except ValueError:
					continue

				if isinstance(row, dict) and isinstance(row.get("task"), str) and isinstance(row.get("t"), str):
					rows.append(row)
	except OSError:
		return []

	return [r for r in rows if account is None or r.get("account") == account]
