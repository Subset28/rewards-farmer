"""Keeps an account from looking like a machine that does the maximum every day.

An account that earns its full quota at a regular rate, every single day, from its
first day, with nothing ever skipped, is the clearest pattern an automated one
leaves. A person misses days, does a little some days and a lot on others, and
starts slowly. This makes the bot do the same, per account, deterministically
(derived from the account's name and the date), so a restart or a new build lands
on the same answer and nothing needs to be saved to stay consistent.

  * Light days: now and then an account does only the bare minimum, never two days
    running. Never nothing: Rewards counts streaks (the daily set and a Bing search
    seven days in a row are level-up activities, and Gold needs two a month) and
    searching on 14 days a month earns the default search bonus, so a day with no
    activity at all would cost real points.
  * Variable totals: on a working day an account fills a share of its search quota,
    not always all of it.
  * A ramp: for an account's first days it searches less and works up to a full day, rests never, and claims
    every reward from the start (a new member is taken with the points and does it all, searching only a little).
  * Lazy cards: now and then an account leaves one or two search-to-claim cards alone (lazy_card_skipped).
  * Order: the accounts of one run are taken in a different order each time.

Settings (environment):

    REWARDS_REST_DAY_CHANCE        chance a day is a light day, default 0.10 (about one in ten days); 0 turns it off
    REWARDS_MIN_DAILY_FRACTION     least share of the search quota filled on a working day, default 0.8; 1 means always all of it
    REWARDS_RAMP_DAYS              days of an account's ramp, default 7; 0 turns it off
    REWARDS_KEEP_ORDER             1 keeps the accounts in the order listed

A longer ramp for one account: put "ramp_days": 21 in its entry of `data-dir/pacing.json`
(next to "first_day"). Without it the account uses REWARDS_RAMP_DAYS.

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
from datetime import date, timedelta

import clock
import points_log
from constants import USER_DATA_DIR

logger = logging.getLogger(__name__)

STATE_FILE = os.path.join(USER_DATA_DIR, "pacing.json")

# Quota share on the first day of a ramp, rising to a full day at its end.
RAMP_START_FRACTION = 0.3

# A light day keeps every streak alive and nothing more: the daily set, one small
# round of searching, and the daily claim.
LIGHT_STEPS = ("Bing daily set", "Required searches", "Bonus points")
LIGHT_SEARCH_POINTS = 10


def _float(name: str, default: float, low: float, high: float) -> float:
	try:
		return min(high, max(low, float(os.environ.get(name, default))))
	except ValueError:
		return default


def rest_chance() -> float:
	return _float("REWARDS_REST_DAY_CHANCE", 0.10, 0.0, 0.5)


def min_fraction() -> float:
	return _float("REWARDS_MIN_DAILY_FRACTION", 0.8, 0.05, 1.0)


def ramp_days(account: str | None = None) -> int:
	"""Days of the ramp: this account's own setting in pacing.json if it has one, else the global one."""
	if account:
		try:
			return max(0, int(_read_state()[account]["ramp_days"]))
		except (KeyError, TypeError, ValueError):
			pass

	try:
		return max(0, int(os.environ.get("REWARDS_RAMP_DAYS", "7")))
	except ValueError:
		return 7


def _today() -> date:
	return clock.today()


def _unit(account: str, day: date, salt: str) -> float:
	"""A number in [0, 1) that depends only on these three things."""
	digest = hashlib.sha256(f"{salt}|{account.lower()}|{day.isoformat()}".encode()).digest()

	return int.from_bytes(digest[:8], "big") / 2**64


def known_accounts() -> list[str]:
	"""Every account the pacing record has seen, including one that has not earned a point yet."""
	return sorted(name for name in _read_state() if isinstance(name, str))


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
	rows = []

	try:
		rows = points_log.history(account)

		if rows:
			first = min(first, date.fromisoformat(rows[0]["time"][:10]))
	except (ValueError, KeyError, TypeError):
		pass

	if not rows:
		logger.warning(
			"%s has no first day in %s and no points history, so it is treated as new (a %d-day ramp). "
			"If it is not new, set its first_day there.", account, STATE_FILE, ramp_days(account),
		)

	_save_first_day(account, first)

	return first


def _save_first_day(account: str, first: date) -> None:
	"""Remember an account's first day without ever leaving a half-written file for a reader.

	Written to a temporary file and moved into place, and read again just before, so two
	processes (the schedulers, status.py) cannot overwrite each other's entries.
	"""
	state = _read_state()
	entry = state.get(account)

	if isinstance(entry, dict) and "first_day" in entry:
		return

	# Keep what is already there (a "ramp_days" set by hand), add the first day.
	state[account] = {**(entry if isinstance(entry, dict) else {}), "first_day": first.isoformat()}
	temporary = f"{STATE_FILE}.{os.getpid()}.tmp"

	try:
		os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)

		with open(temporary, "w", encoding="utf-8") as handle:
			json.dump(state, handle, indent=1)

		os.replace(temporary, STATE_FILE)
	except OSError as exc:
		logger.debug("Could not save the pacing state: %s", exc)

		try:
			os.unlink(temporary)
		except OSError:
			pass


def age_days(account: str, today: date | None = None) -> int:
	today = today or _today()

	# The first day is seeded from the real date, never from the day being asked about.
	return max(0, (today - first_day(account)).days)


def in_ramp(account: str, today: date | None = None) -> bool:
	return age_days(account, today) < ramp_days(account)


def _raw_rest(account: str, day: date) -> bool:
	return age_days(account, day) >= ramp_days(account) and _unit(account, day, "rest") < rest_chance()


def is_rest_day(account: str, today: date | None = None) -> bool:
	"""Whether today is a light day for this account. Never in its ramp, never two days in a row."""
	today = today or _today()

	return _raw_rest(account, today) and not _raw_rest(account, today - timedelta(days=1))


def fraction(account: str, today: date | None = None) -> float:
	"""The share of the search quota to fill today, in (0, 1]."""
	today = today or _today()
	low = min_fraction()
	share = low + (1.0 - low) * _unit(account, today, "share")
	days = ramp_days(account)

	if days and age_days(account, today) < days:
		share *= RAMP_START_FRACTION + (1.0 - RAMP_START_FRACTION) * age_days(account, today) / days

	return min(1.0, max(0.05, share))


def search_target(account: str, cap: int, today: date | None = None) -> int:
	"""The points of searching to reach today: some share of `cap`, at least one search's worth, at most the cap.

	On a light day it is just enough to keep the search streak and the 14-day count going."""
	if cap <= 0:
		return cap

	if is_rest_day(account, today):
		return min(cap, LIGHT_SEARCH_POINTS)

	return min(cap, max(1, round(cap * fraction(account, today))))


def steps_allowed(account: str, today: date | None = None) -> tuple[str, ...] | None:
	"""The only tasks to do today, or None for all of them.

	A new account (in its ramp) does every task from its first day: someone with a new Rewards account is taken
	with the points and claims everything on offer. Only how much it searches grows over the ramp (fraction())."""
	return LIGHT_STEPS if is_rest_day(account, today) else None


def lazy_skips(account: str, today: date | None = None) -> int:
	"""How many search-to-claim cards this account leaves alone today: usually none, now and then one or two.

	A person skips the cards that need a search before they credit; skipping them all the time, or never, is the pattern.
	Worked out from the account and the date, so a restart gives the same answer."""
	today = today or _today()
	draw = _unit(account, today, "lazy")

	return 0 if draw < 0.5 else 1 if draw < 0.85 else 2


def lazy_card_skipped(account: str, index: int, total: int, today: date | None = None) -> bool:
	"""Whether card number `index` of `total` is one of today's skipped ones. Never all of them."""
	today = today or _today()
	count = min(lazy_skips(account, today), max(0, total - 1))
	ranked = sorted(range(total), key=lambda i: _unit(account, today, f"lazy-card-{i}"))

	return index in ranked[:count]


def ordered(items: list, rng=random) -> list:
	"""The accounts of a run in a fresh order each time, unless REWARDS_KEEP_ORDER=1."""
	items = list(items)

	if os.environ.get("REWARDS_KEEP_ORDER", "0") != "1":
		rng.shuffle(items)

	return items


def describe(account: str, today: date | None = None) -> str:
	today = today or _today()

	if is_rest_day(account, today):
		what = "light day (daily set and one small search, keeping streaks alive)"
	else:
		what = f"works today, {fraction(account, today):.0%} of the search quota"

	ramp = f", ramp day {age_days(account, today) + 1} of {ramp_days(account)}" if in_ramp(account, today) else ""

	return f"{account}: {what}{ramp}"


if __name__ == "__main__":
	import sys

	import accounts

	names = sys.argv[1:] or [a.name for a in accounts.configured()]

	for name in names:
		print(describe(name))
