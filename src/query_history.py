"""What each account has already searched for.

Every batch of searches used to be drawn from the top of the trending feed, so
the second batch of a run repeated the first and every run that day repeated
the one before. A repeated query is wasted volume and most likely earns
nothing: the logs show rounds of 5 searches paying for 2.

One JSON line per search in data-dir/query_history.jsonl, per account, so a
query is not searched again by the same account for DAYS days. Never raises: a
search history that cannot be read or written must not stop a run.
"""

import json
import logging
import os
import time

from constants import USER_DATA_DIR

logger = logging.getLogger(__name__)

HISTORY_FILE = os.path.join(USER_DATA_DIR, "query_history.jsonl")

DAYS = 30

# Older lines are dropped when the file is rewritten, so it cannot grow forever.
MAX_LINES = 5000


def normalize(query: str) -> str:
	return " ".join((query or "").lower().split())


def _read() -> list[dict]:
	try:
		with open(HISTORY_FILE, encoding="utf-8") as handle:
			rows = []

			for line in handle:
				try:
					row = json.loads(line)
				except ValueError:
					continue

				if isinstance(row, dict) and "q" in row and "t" in row:
					rows.append(row)

			return rows
	except OSError:
		return []


def recent(account: str | None, days: int = DAYS, now: float | None = None) -> set[str]:
	"""Normalized queries this account searched in the last `days` days."""
	cutoff = (time.time() if now is None else now) - days * 86400

	return {
		normalize(row["q"])
		for row in _read()
		if row.get("account") == (account or "default") and isinstance(row.get("t"), (int, float)) and row["t"] >= cutoff
	}


# Queries another account searched this recently are left alone, so two accounts do
# not search the same things in the same few days.
OTHER_ACCOUNTS_DAYS = 7


def recent_by_others(account: str | None, days: int = OTHER_ACCOUNTS_DAYS, now: float | None = None) -> set[str]:
	"""Normalized queries any other account searched in the last `days` days."""
	cutoff = (time.time() if now is None else now) - days * 86400
	me = account or "default"

	return {
		normalize(row["q"])
		for row in _read()
		if row.get("account") != me and isinstance(row.get("t"), (int, float)) and row["t"] >= cutoff
	}


def avoid_for(account: str | None, now: float | None = None) -> set[str]:
	"""Everything this account should not search: its own recent queries and other accounts' very recent ones."""
	return recent(account, now=now) | recent_by_others(account, now=now)


def record(account: str | None, query: str, now: float | None = None) -> None:
	"""Remember one search. Trims the file now and then."""
	line = json.dumps({"t": time.time() if now is None else now, "account": account or "default", "q": query})

	try:
		os.makedirs(os.path.dirname(HISTORY_FILE), exist_ok=True)

		with open(HISTORY_FILE, "a", encoding="utf-8") as handle:
			handle.write(line + "\n")

		rows = _read()

		if len(rows) > MAX_LINES:
			with open(HISTORY_FILE, "w", encoding="utf-8") as handle:
				for row in rows[-MAX_LINES // 2:]:
					handle.write(json.dumps(row) + "\n")
	except OSError as exc:
		logger.debug("Could not record the search: %s", exc)
