"""A short note once a week, and a reminder when an account has enough points to redeem.

The bot looks after itself and health.py speaks up when something is wrong. This is the other half: a quiet
week should still say so, so the owner can see that it is running and what it earned without opening anything.

  * Sundays after 18:00, once: for each account, the points gained in the last seven days, the month so far, the
    way to the next level, and how many of its runs worked; plus anything health.py is complaining about and any
    pause. Sent to the shared destination.
  * When an account's points reach another 6,500 (about a $5 gift card at 1,200 points a dollar, with a margin),
    one message to that account's destination, with the reminder to redeem on mobile data.

It never raises: a note must not be the thing that breaks a run. What it has sent is in data-dir/weekly.json.
"""

import json
import logging
import os
from datetime import datetime, timedelta

import clock
import health
import journal
import notify
import points_log
import safety
from constants import USER_DATA_DIR

logger = logging.getLogger(__name__)

STATE_FILE = os.path.join(USER_DATA_DIR, "weekly.json")
SEND_WEEKDAY = 6  # Sunday
SEND_AFTER_HOUR = 18
REDEEM_EVERY = 6500


def _read_state() -> dict:
	try:
		with open(STATE_FILE, encoding="utf-8") as handle:
			data = json.load(handle)

		return data if isinstance(data, dict) else {}
	except (OSError, ValueError):
		return {}


def _write_state(state: dict) -> None:
	try:
		os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)

		with open(STATE_FILE, "w", encoding="utf-8") as handle:
			json.dump(state, handle, indent=1)
	except OSError as exc:
		logger.debug("Could not save the weekly state: %s", exc)


def week_key(now: datetime) -> str:
	year, week, _ = now.isocalendar()

	return f"{year}-{week:02d}"


def gained_in_week(rows: list[dict], now: datetime) -> int | None:
	"""Lifetime points gained over the last seven days, or None when there is nothing to compare."""
	readings = [r for r in rows if isinstance(r.get("lifetime"), int)]

	if len(readings) < 2:
		return None

	cutoff = (now - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
	before = [r for r in readings if r["time"] <= cutoff]
	base = before[-1] if before else readings[0]

	return max(0, readings[-1]["lifetime"] - base["lifetime"])


def run_counts(name: str, now: datetime) -> tuple[int, int]:
	"""(worked, failed) among the account's daily and search runs in the last seven days."""
	worked = failed = 0

	for kind in ("daily", "search"):
		for row in journal.events(owner=name, kind=kind, days=7, now=now):
			if row.get("event") != "end":
				continue

			if row.get("outcome") == "ok":
				worked += 1
			elif row.get("outcome") == "failed":
				failed += 1

	return worked, failed


def account_line(name: str, now: datetime) -> str:
	rows = points_log.history(name)
	last = rows[-1] if rows else {}
	gained = gained_in_week(rows, now)
	parts = [f"{name}: {'+%d this week' % gained if gained is not None else 'no points on record yet'}"]

	if isinstance(last.get("month"), int):
		parts.append(f"{last['month']} this month")
		target = points_log.target_for(name)

		if target is not None:
			left = points_log.to_next_level(last["month"], target)
			parts.append(f"{left} to the next level" if left else "next level's monthly target reached")

	worked, failed = run_counts(name, now)
	parts.append(f"{worked} runs worked" + (f", {failed} failed" if failed else ""))

	if safety.paused_for(name) is not None:
		parts.append("PAUSED")

	return ", ".join(parts)


def summary(names: list[str], now: datetime) -> str:
	lines = [account_line(name, now) for name in names]
	problems = health.findings(names, now)

	lines.append("Nothing needs anyone." if not problems else "Needs attention: " + "; ".join(f.title for f in problems))

	return "\n".join(lines)


def reminders(names: list[str], state: dict) -> list[tuple[str, int]]:
	"""(account, points) for each account that has reached another REDEEM_EVERY since it was last told."""
	told = state.setdefault("reminded", {})
	found = []

	for name in names:
		rows = [r for r in points_log.history(name) if isinstance(r.get("lifetime"), int)]

		if not rows:
			continue

		lifetime = rows[-1]["lifetime"]
		tier = lifetime // REDEEM_EVERY

		if tier > int(told.get(name, 0)):
			found.append((name, lifetime))
			told[name] = tier

	return found


def run_if_due(names: list[str] | None = None, now: datetime | None = None, send=notify.send) -> bool:
	"""Send the weekly note and any redemption reminders that are due. Returns whether anything was sent."""
	try:
		now = now or clock.now()

		if names is None:
			import status

			names = status.known_names()

		state = _read_state()
		sent = False

		for name, lifetime in reminders(names, state):
			send(
				f"{name}: about {lifetime:,} points",
				"Enough for a gift card (1,200 points a dollar, $5 minimum). Redeem on mobile data, not the home connection.",
				account=name,
			)
			sent = True

		if now.weekday() == SEND_WEEKDAY and now.hour >= SEND_AFTER_HOUR and state.get("week") != week_key(now):
			send("Weekly note", summary(names, now))
			state["week"] = week_key(now)
			sent = True

		_write_state(state)

		return sent
	except Exception as exc:  # pragma: no cover - a note must never break a run
		logger.debug("Weekly note failed: %s", exc)

		return False
