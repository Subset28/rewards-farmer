"""A record of what ran and how far it got, that survives a restart or a new build.

A container's own log is gone the moment it is recreated, and a new build used to
have no way of knowing whether a planned run had happened, been cut off, or been
missed. The schedulers and the runs write one JSON line per event here instead:

    {"t": "2026-10-04T14:04:30", "owner": "default", "kind": "search",
     "event": "start", "planned": "2026-10-04T14:04:27", "mode": "scheduled"}

The next build reads it: a planned run with a "start" and no "end" was cut off, one
with neither was missed, and a "quota" event with complete set means there is
nothing left to search for today. Nothing here ever raises, because a journal that
cannot be written must not stop a run.

    python src/journal.py            # today, readable
    python src/journal.py 3          # the last three days
"""

import json
import logging
import os
import sys
from datetime import datetime, timedelta

from constants import USER_DATA_DIR

logger = logging.getLogger(__name__)

JOURNAL_FILE = os.path.join(USER_DATA_DIR, "journal.jsonl")

# The oldest lines are dropped when the file grows past this.
MAX_LINES = 6000


def _now() -> datetime:
	return datetime.now()


def record(owner: str, kind: str, event: str, **details) -> None:
	"""Append one event. `owner` is the scheduler (or account) it belongs to, `kind` is "search" or "daily"."""
	line = {"t": _now().isoformat(timespec="seconds"), "owner": owner, "kind": kind, "event": event, **details}

	try:
		os.makedirs(os.path.dirname(JOURNAL_FILE), exist_ok=True)

		with open(JOURNAL_FILE, "a", encoding="utf-8") as handle:
			handle.write(json.dumps(line) + "\n")

		_trim()
	except (OSError, TypeError, ValueError) as exc:
		logger.debug("Could not write the journal: %s", exc)


def _trim() -> None:
	try:
		with open(JOURNAL_FILE, encoding="utf-8") as handle:
			lines = handle.readlines()

		if len(lines) > MAX_LINES:
			with open(JOURNAL_FILE, "w", encoding="utf-8") as handle:
				handle.writelines(lines[-(MAX_LINES // 2):])
	except OSError:
		pass


def read_all() -> list[dict]:
	"""Every readable event, oldest first. Damaged lines are skipped."""
	try:
		with open(JOURNAL_FILE, encoding="utf-8") as handle:
			rows = []

			for line in handle:
				try:
					row = json.loads(line)
				except ValueError:
					continue

				if isinstance(row, dict) and isinstance(row.get("t"), str) and "event" in row:
					rows.append(row)

			return rows
	except OSError:
		return []


def events(owner: str | None = None, kind: str | None = None, days: int = 1, now: datetime | None = None) -> list[dict]:
	"""Events from today (or the last `days` days, by the local clock), optionally for one owner and kind."""
	now = _now() if now is None else now
	first = (now - timedelta(days=days - 1)).strftime("%Y-%m-%d")

	return [
		row for row in read_all()
		if row["t"][:10] >= first
		and (owner is None or row.get("owner") == owner)
		and (kind is None or row.get("kind") == kind)
	]


def quota_complete(accounts: list[str] | None, now: datetime | None = None) -> bool:
	"""Whether every one of these accounts has reported its search quota full today.

	The last report counts, so a quota that later reads as not full (the cap rose,
	or the day turned over) stops counting.
	"""
	if not accounts:
		return False

	latest: dict[str, bool] = {}

	for row in events(kind="search", now=now):
		if row["event"] == "quota" and row.get("account") in accounts:
			latest[row["account"]] = bool(row.get("complete"))

	return all(latest.get(name) for name in accounts)


def describe(days: int = 1, now: datetime | None = None) -> str:
	"""The journal as something a person can read."""
	rows = events(days=days, now=now)

	if not rows:
		return "nothing recorded yet"

	lines = []
	day = None

	for row in rows:
		if row["t"][:10] != day:
			day = row["t"][:10]
			lines.append(day)

		extra = {k: v for k, v in row.items() if k not in ("t", "owner", "kind", "event")}
		detail = "  ".join(f"{k}={v}" for k, v in extra.items())
		lines.append(f"  {row['t'][11:]}  {row.get('owner', '?'):<8} {row.get('kind', '?'):<7} {row['event']:<8} {detail}".rstrip())

	return "\n".join(lines)


if __name__ == "__main__":
	try:
		count = int(sys.argv[1]) if len(sys.argv) > 1 else 1
	except ValueError:
		count = 1

	print(describe(max(1, count)))
