"""A running record of points, and how far off the next level is.

One JSON line per reading in data-dir/points.jsonl, so progress and the effect
of a change show up in numbers rather than in a feeling:

    python src/points_log.py            # every account, one after another
    python src/points_log.py second     # one account
"""

import json
import os
import re
import sys
import time

from constants import USER_DATA_DIR

LOG_FILE = os.path.join(USER_DATA_DIR, "points.jsonl")

# Monthly points that reach each account's next level, as "name=points,name=points".
# Not read from the page, and accounts sit on different levels, so each has its own
# target; an account not listed gets no "to the next level" line rather than a wrong one.
LEVEL_TARGETS_ENV = "REWARDS_LEVEL_TARGETS"
DEFAULT_LEVEL_TARGETS = "default=750,second=500"


def parse_targets(text: str) -> dict[str, int]:
	"""{"default": 750, ...} out of "default=750,second=500". Entries that do not parse are skipped."""
	targets = {}

	for part in text.split(","):
		name, _, value = part.partition("=")

		try:
			targets[name.strip()] = int(value)
		except ValueError:
			continue

	targets.pop("", None)

	return targets


def target_for(account: str) -> int | None:
	"""The monthly points that reach this account's next level, or None when it has none set."""
	return parse_targets(os.environ.get(LEVEL_TARGETS_ENV, DEFAULT_LEVEL_TARGETS)).get(account)

FIELDS = (
	("today", re.compile(r"today'?s points\s*\|?\s*([\d,]+)", re.I)),
	("month", re.compile(r"this month\s*\|?\s*([\d,]+)", re.I)),
	("lifetime", re.compile(r"lifetime\s*\|?\s*([\d,]+)", re.I)),
)


def parse_breakdown(text: str) -> dict[str, int]:
	"""Today, month and lifetime points out of the points breakdown panel's text."""
	flat = " | ".join(line.strip() for line in text.splitlines() if line.strip())
	found = {}

	for name, pattern in FIELDS:
		match = pattern.search(flat)

		if match:
			found[name] = int(match.group(1).replace(",", ""))

	return found


def record(account: str, reading: dict[str, int]) -> dict:
	"""Append a reading, and return the line written."""
	line = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "account": account, **reading}

	os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)

	with open(LOG_FILE, "a", encoding="utf-8") as handle:
		handle.write(json.dumps(line) + "\n")

	return line


def history(account: str | None = None) -> list[dict]:
	try:
		with open(LOG_FILE, encoding="utf-8") as handle:
			rows = [json.loads(line) for line in handle if line.strip()]
	except FileNotFoundError:
		return []

	return [row for row in rows if account is None or row.get("account") == account]


def to_next_level(month_points: int, target: int) -> int:
	"""Points still to earn this month for the next level, never negative."""
	return max(0, target - month_points)


def summary(rows: list[dict], target: int | None = None) -> str:
	if not rows:
		return "no readings yet"

	last = rows[-1]
	month = last.get("month")
	parts = [f"{last['time']}  today {last.get('today', '?')}  month {month if month is not None else '?'}  lifetime {last.get('lifetime', '?')}"]

	if month is not None and target is not None:
		left = to_next_level(month, target)
		parts.append(f"{left} to the next level this month" if left else "next level's monthly target reached")

	first_by_day = {}

	for row in rows:
		first_by_day.setdefault(row["time"][:10], row)

	days = sorted(first_by_day)

	if len(days) >= 2 and "lifetime" in rows[0] and "lifetime" in last:
		span = max(1, len(days) - 1)
		parts.append("about %d points a day over %d days" % ((last["lifetime"] - first_by_day[days[0]]["lifetime"]) / span, span))

	return "\n".join(parts)


def digest(account: str, reading: dict[str, int]) -> str:
	"""A one-line summary of a reading, for the daily message."""
	parts = [f"{name} {reading[name]}" for name in ("today", "month", "lifetime") if name in reading]
	target = target_for(account)

	if target is not None and "month" in reading:
		left = to_next_level(reading["month"], target)
		parts.append(f"{left} to the next level" if left else "next level reached")

	return f"{account}: " + ", ".join(parts)


def report(account: str | None = None) -> str:
	"""One summary per account, each with its own next-level target, or just the one asked for."""
	rows = history()
	names = [account] if account else sorted({row.get("account") for row in rows if row.get("account")})

	if not names:
		return summary([])

	return "\n\n".join(f"{name}\n" + summary([r for r in rows if r.get("account") == name], target_for(name)) for name in names)


if __name__ == "__main__":
	print(report(sys.argv[1] if len(sys.argv) > 1 else None))
