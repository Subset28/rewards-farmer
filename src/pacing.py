"""Keeps an account from looking like a machine that does the maximum every day.

An account that earns its full quota at a regular rate, every single day, from its
first day, with nothing ever skipped, is the clearest pattern an automated one
leaves. A person misses days, does a little some days and a lot on others, and
starts slowly. This makes the bot do the same, per account, deterministically
(derived from the account's name and the date), so a restart or a new build lands
on the same answer and nothing needs to be saved to stay consistent.

  * Rest days: now and then an account does nothing at all, never two days running.
  * Variable totals: on a working day an account fills a share of its search quota,
    not always all of it.
  * A ramp: for an account's first days it does less, rests never, and sticks to the
    daily set and searches.
  * Order: the accounts of one run are taken in a different order each time.

Settings (environment):

    REWARDS_REST_DAY_CHANCE        chance a day is a rest day, default 0.15 (about one a week); 0 turns it off
    REWARDS_MIN_DAILY_FRACTION     least share of the search quota filled on a working day, default 0.6; 1 means always all of it
    REWARDS_RAMP_DAYS              days of an account's ramp, default 7; 0 turns it off
    REWARDS_KEEP_ORDER             1 keeps the accounts in the order listed

An account's first day is the date of its first points reading, or the day it was
first seen; `data-dir/pacing.json` holds it and can be edited ("2026-09-01") to say an
account is not new. Nothing here ever raises: pacing that cannot be worked out means
the account runs as it would have.
"""

import hashlib
import json
import logging
import os
import random
from datetime import date, datetime, timedelta

import points_log
from constants import USER_DATA_DIR

logger = logging.getLogger(__name__)

STATE_FILE = os.path.join(USER_DATA_DIR, "pacing.json")

# Quota share on the first day of a ramp, rising to a full day at its end.
RAMP_START_FRACTION = 0.3

# During the ramp an account does these tasks and no others.
RAMP_STEPS = ("Bing daily set", "Required searches", "Bonus points")


def _float(name: str, default: float, low: float, high: float) -> float:
	try:
		return min(high, max(low, float(os.environ.get(name, default))))
	except ValueError:
		return default


def rest_chance() -> float:
	return _float("REWARDS_REST_DAY_CHANCE", 0.15, 0.0, 0.5)


def min_fraction() -> float:
	return _float("REWARDS_MIN_DAILY_FRACTION", 0.6, 0.05, 1.0)


def ramp_days() -> int:
	try:
		return max(0, int(os.environ.get("REWARDS_RAMP_DAYS", "7")))
	except ValueError:
		return 7


def _today() -> date:
	return datetime.now().date()


def _unit(account: str, day: date, salt: str) -> float:
	"""A number in [0, 1) that depends only on these three things."""
	digest = hashlib.sha256(f"{salt}|{account.lower()}|{day.isoformat()}".encode()).digest()

	return int.from_bytes(digest[:8], "big") / 2**64


def _read_state() -> dict:
	try:
		with open(STATE_FILE, encoding="utf-8") as handle:
			state = json.load(handle)
	except (OSError, ValueError):
		return {}

	return state if isinstance(state, dict) else {}


def first_day(account: str, today: date | None = None) -> date:
	"""The date this account was first worked, remembered from then on."""
	today = today or _today()
	state = _read_state()

	try:
		return date.fromisoformat(state[account]["first_day"])
	except (KeyError, TypeError, ValueError):
		pass

	first = today

	try:
		rows = points_log.history(account)

		if rows:
			first = min(first, date.fromisoformat(rows[0]["time"][:10]))
	except (ValueError, KeyError, TypeError):
		pass

	state[account] = {"first_day": first.isoformat()}

	try:
		os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)

		with open(STATE_FILE, "w", encoding="utf-8") as handle:
			json.dump(state, handle, indent=1)
	except OSError as exc:
		logger.debug("Could not save the pacing state: %s", exc)

	return first


def age_days(account: str, today: date | None = None) -> int:
	today = today or _today()

	# The first day is seeded from the real date, never from the day being asked about.
	return max(0, (today - first_day(account)).days)


def in_ramp(account: str, today: date | None = None) -> bool:
	return age_days(account, today) < ramp_days()


def _raw_rest(account: str, day: date) -> bool:
	return age_days(account, day) >= ramp_days() and _unit(account, day, "rest") < rest_chance()


def is_rest_day(account: str, today: date | None = None) -> bool:
	"""Whether this account does nothing today. Never in its ramp, never two days in a row."""
	today = today or _today()

	return _raw_rest(account, today) and not _raw_rest(account, today - timedelta(days=1))


def fraction(account: str, today: date | None = None) -> float:
	"""The share of the search quota to fill today, in (0, 1]."""
	today = today or _today()
	low = min_fraction()
	share = low + (1.0 - low) * _unit(account, today, "share")
	days = ramp_days()

	if days and age_days(account, today) < days:
		share *= RAMP_START_FRACTION + (1.0 - RAMP_START_FRACTION) * age_days(account, today) / days

	return min(1.0, max(0.05, share))


def search_target(account: str, cap: int, today: date | None = None) -> int:
	"""The points of searching to reach today: some share of `cap`, at least one search's worth, at most the cap."""
	if cap <= 0:
		return cap

	return min(cap, max(1, round(cap * fraction(account, today))))


def steps_allowed(account: str, today: date | None = None) -> tuple[str, ...] | None:
	"""The only tasks to do today, or None for all of them."""
	return RAMP_STEPS if in_ramp(account, today) else None


def ordered(items: list, rng=random) -> list:
	"""The accounts of a run in a fresh order each time, unless REWARDS_KEEP_ORDER=1."""
	items = list(items)

	if os.environ.get("REWARDS_KEEP_ORDER", "0") != "1":
		rng.shuffle(items)

	return items


def describe(account: str, today: date | None = None) -> str:
	today = today or _today()

	if is_rest_day(account, today):
		what = "rest day"
	else:
		what = f"works today, {fraction(account, today):.0%} of the search quota"

	ramp = f", ramp day {age_days(account, today) + 1} of {ramp_days()}" if in_ramp(account, today) else ""

	return f"{account}: {what}{ramp}"


if __name__ == "__main__":
	import sys

	import accounts

	names = sys.argv[1:] or [a.name for a in accounts.configured()]

	for name in names:
		print(describe(name))
