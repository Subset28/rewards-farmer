"""Spreads each owner's required searches over the day in several runs of search_only.py, keeps a journal so a restart catches up, and stops runs when the quota is complete."""

import hashlib
import logging
import math
import os
import random
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta

import accounts
import footprint
import health
import gate
import journal
import log_utils
import run_lock
import safety
import schedule_plan

logger = logging.getLogger(__name__)

# Required searches spread across several random times a day instead of one
# batch, so 25 searches don't all land in the same few minutes -- the same
# reasoning as daily_loop.py's anchor+jitter, applied within a day rather than
# across days. Window is waking hours by default; a search run at 3am is more
# suspicious than the batch it is meant to avoid looking like.
RUNS_PER_DAY = int(os.environ.get("REWARDS_SEARCH_RUNS_PER_DAY", "4"))
START_HOUR = int(os.environ.get("REWARDS_SEARCH_START_HOUR", "8"))
END_HOUR = int(os.environ.get("REWARDS_SEARCH_END_HOUR", "23"))

# A plan made this late has no room for a run, and one made with less than this
# left in the window waits for tomorrow instead of squeezing a run in.
MIN_WINDOW = timedelta(minutes=30)

# A planned run that was cut off or missed is redone if it is no more than this
# late and the window is still open. A run reads the real points first, so doing
# one again only does what is left.
CATCH_UP = timedelta(hours=3)

# The least minutes between the starts of any two planned runs on this connection, tried in order.
SPACING_MINUTES = (45, 30, 15, 0)
OWN_SPACING_MINUTES = 20


@dataclass(frozen=True)
class Due:
	"""A run to make: when to start it, which planned time it stands for, and whether it is a catch-up."""

	when: datetime
	planned: datetime
	mode: str  # "scheduled" or "catch_up"


def account_names() -> list[str] | None:
	"""Names of the accounts this scheduler runs, or None when they cannot be read."""
	try:
		return [a.name for a in accounts.configured()]
	except ValueError:
		return None


# Share of runs drawn from the owner's favoured windows; the rest are spread over
# the whole waking window so the habit is a tendency, not a timetable.
HABIT_SHARE = 0.75

# Spread (minutes) of the normal jitter added to a habit draw, so two days with
# the same windows still land on different minutes and the window edges blur.
HABIT_JITTER_MINUTES = 6

# Where each favoured window may be centred (hour of day, low and high) and the
# width it may have in minutes, by time of day. Everything per account is a pick
# inside these ranges.
_HABIT_SLOTS = (
	("morning", 8.5, 11.0),
	("midday", 12.0, 15.5),
	("evening", 18.0, 22.5),
)
_HABIT_WIDTH = (60, 120)

# Weekends start later and are looser: windows slide this many minutes later and
# widen by this factor.
_WEEKEND_SHIFT_MINUTES = (60, 90)
_WEEKEND_WIDEN = 1.5


def other_runs_today(now: datetime, owner: str) -> list[datetime]:
	"""Every run already planned today by the other accounts (searches, and the daily run), to keep apart from."""
	day = now.strftime("%Y-%m-%d")
	found: list[datetime] = []

	# Every owner with a plan in the file, not just this scheduler's own accounts: each loop is started with only its
	# own account configured, so its own list never contains the others.
	for name in sorted(set(account_names() or []) | set(schedule_plan.owners("search")) | set(schedule_plan.owners("daily"))):
		if name == owner:
			continue

		saved = schedule_plan.read("search", name)
		found += _parse(saved.get("times")) if saved.get("day") == day else []
		daily = _parse([schedule_plan.read("daily", name).get("at")])
		found += [t for t in daily if t.strftime("%Y-%m-%d") == day]

	return found


def _unit(owner: str, salt: str) -> float:
	"""A number in [0, 1) that depends only on the owner and the salt (same hashing as pacing.py)."""
	digest = hashlib.sha256(f"{salt}|{owner.lower()}".encode()).digest()

	return int.from_bytes(digest[:8], "big") / 2**64


def circadian_drift_minutes(owner: str, date_iso: str) -> float:
	"""Deterministic circadian diurnal drift in [-20.0, 20.0] minutes for this owner on this date."""
	digest = hashlib.sha256(f"circadian|{owner.lower()}|{date_iso}".encode()).digest()
	unit = int.from_bytes(digest[:8], "big") / 2**64

	return (unit - 0.5) * 20.0


def habit_windows(owner: str, weekend: bool, date_iso: str | None = None) -> list[tuple[float, float]]:
	"""The owner's favoured windows as (start, end) in minutes after midnight, sorted.

	Depends on the owner, weekend schedule, and optional day-to-day circadian drift.
	2 or 3 of the morning/midday/evening windows are kept, chosen per owner.
	"""
	# Which slots this owner uses: always at least two. Of the five equal parts of
	# the hash, three drop one slot each and two keep all three (index 3 matches none).
	dropped = int(_unit(owner, "habit-drop") * 5)
	slots = [slot for index, slot in enumerate(_HABIT_SLOTS) if index != dropped]
	windows = []
	drift = circadian_drift_minutes(owner, date_iso) if date_iso else 0.0

	for name, low, high in slots:
		centre = (low + (high - low) * _unit(owner, f"habit-centre-{name}")) * 60 + drift
		width = _HABIT_WIDTH[0] + (_HABIT_WIDTH[1] - _HABIT_WIDTH[0]) * _unit(owner, f"habit-width-{name}")

		if weekend:
			centre += _WEEKEND_SHIFT_MINUTES[0] + (_WEEKEND_SHIFT_MINUTES[1] - _WEEKEND_SHIFT_MINUTES[0]) * _unit(owner, f"habit-shift-{name}")
			width *= _WEEKEND_WIDEN

		windows.append((centre - width / 2, centre + width / 2))

	return sorted(windows)


def _habit_time(owner: str, begin: datetime, end: datetime, start: datetime) -> datetime:
	"""One run time in [begin, end): usually inside a favoured window, otherwise anywhere."""
	midnight = start.replace(hour=0, minute=0, second=0, microsecond=0)
	lo, hi = (begin - midnight).total_seconds() / 60, (end - midnight).total_seconds() / 60

	# Only the part of each window still ahead can take a run.
	date_iso = start.date().isoformat()
	windows = [(max(a, lo), min(b, hi)) for a, b in habit_windows(owner, start.weekday() >= 5, date_iso=date_iso)]
	windows = [(a, b) for a, b in windows if b > a]

	minute = None

	if windows and random.random() < HABIT_SHARE:
		a, b = random.choices(windows, weights=[b - a for a, b in windows])[0]

		# Jitter that lands outside the range is drawn again, not clamped to its edge: clamping
		# piled draws onto exactly the start of what is left of the day, and two runs at the
		# same instant count as one.
		for _ in range(20):
			candidate = random.uniform(a, b) + random.gauss(0, HABIT_JITTER_MINUTES)

			if lo <= candidate < hi:
				minute = candidate

				break

	if minute is None:
		minute = random.uniform(lo, hi)

	return midnight + timedelta(minutes=min(minute, hi - 1 / 60))


def draw_times(now: datetime, owner: str | None = None, avoid: list[datetime] | None = None) -> list[datetime]:
	"""Random run times in what is left of today's [START_HOUR, END_HOUR) window, sorted.

	A full day's RUNS_PER_DAY when the day has not started, proportionally fewer
	when this is made part way through (at least one while 30 minutes remain),
	and none when the window is nearly over.

	With an owner, most times fall in that owner's habitual windows (see
	habit_windows) and keep their distance from `avoid`; with none, they are uniform.
	"""
	start = now.replace(hour=START_HOUR, minute=0, second=0, microsecond=0)
	end = now.replace(hour=END_HOUR, minute=0, second=0, microsecond=0)
	begin = max(start, now + timedelta(minutes=2))

	if end - begin < MIN_WINDOW:
		return []

	fraction = (end - begin) / (end - start)
	count = RUNS_PER_DAY if fraction >= 1 else max(1, math.ceil(RUNS_PER_DAY * fraction))
	span = (end - begin).total_seconds()

	if owner is None:
		return sorted(begin + timedelta(seconds=random.uniform(0, span)) for _ in range(count))

	times: list[datetime] = []
	taken = list(avoid or [])

	# Distinct, and apart from every other run planned today (this owner's and the other accounts', which
	# share a connection): two accounts starting minutes apart is a pattern, and the journal tells runs apart
	# by their planned time. The gap is relaxed only when the day is too full to keep it, never dropped
	# before the last pass.
	for gap in SPACING_MINUTES:
		for _ in range(count * 30):
			if len(times) >= count:
				break

			candidate = _habit_time(owner, begin, end, start)

			# One account's own runs may sit closer (a person searches twice in an hour); another account's
			# run, on the same connection, is kept at the full gap.
			if all(abs((candidate - other).total_seconds()) >= gap * 60 for other in taken) and all(
				abs((candidate - other).total_seconds()) >= min(gap, OWN_SPACING_MINUTES) * 60 for other in times
			):
				times.append(candidate)

	return sorted(times)


def _parse(values) -> list[datetime]:
	times = []

	for value in values if isinstance(values, list) else []:
		try:
			times.append(datetime.fromisoformat(value))
		except (TypeError, ValueError):
			continue

	return times


def day_plan(now: datetime, owner: str) -> list[datetime]:
	"""Every run planned for today, past ones included, the same ones across restarts.

	A plan already made today is reused, so a restart does not redraw the day.
	The first call of a day draws and saves one.
	"""
	day = now.strftime("%Y-%m-%d")
	saved = schedule_plan.read("search", owner)
	times = _parse(saved.get("times")) if saved.get("day") == day else []

	if not times:
		# Taking turns with the other accounts' schedulers, who are drawing at the same moment (schedule_plan.planning).
		with schedule_plan.planning():
			times = draw_times(now, owner, other_runs_today(now, owner))
			schedule_plan.write("search", owner, {"day": day, "times": [t.isoformat() for t in times]})

	return times


def planned_times(now: datetime, owner: str) -> list[datetime]:
	"""Today's planned run times still ahead of `now`."""
	return [t for t in day_plan(now, owner) if t > now]


def outstanding(now: datetime, owner: str, rng=random) -> list[Due]:
	"""The runs still to make, using what the journal says about the ones already planned.

	A planned run that ended is done. One still ahead is waited for. One whose time
	has passed is either cut off (it started, never ended) or missed (never started),
	and is redone shortly if it is within CATCH_UP and the window is still open; a
	run too late to be useful is dropped.
	"""
	end = now.replace(hour=END_HOUR, minute=0, second=0, microsecond=0)
	rows = journal.events(owner=owner, kind="search", now=now)
	# A run that gave up waiting for the profile ("deferred") did not happen, so it is not done.
	ended = {row.get("planned") for row in rows if row["event"] == "end" and row.get("outcome") != "deferred"}
	due = []

	for planned in day_plan(now, owner):
		if planned.isoformat() in ended:
			continue

		if planned > now:
			due.append(Due(planned, planned, "scheduled"))
		elif now - planned <= CATCH_UP and now < end:
			# Not at once and not on the dot: a small, different wait each time.
			due.append(Due(now + timedelta(seconds=rng.uniform(30, 180)), planned, "catch_up"))

	return sorted(due, key=lambda d: d.when)


def launch(owner: str, due: Due, run=subprocess.run) -> str:
	"""Make one run and journal it. Returns "ok", "failed" or "skipped"."""
	names = account_names()
	planned = due.planned.isoformat()

	if journal.quota_complete(names):
		journal.record(owner, "search", "end", planned=planned, outcome="skipped", reason="quota already complete")
		logger.info("Skipping this search run: today's quota is already complete.")

		return "skipped"

	hold = safety.blocked(names)

	if hold:
		logger.error("[BRAKE] Skipping this search run: paused (%s: %s).", hold.get("kind"), hold.get("reason"))
		journal.record(owner, "search", "end", planned=planned, outcome="skipped", reason="paused")

		return "skipped"

	logger.info("=== starting scheduled search run%s ===", " (catching up)" if due.mode == "catch_up" else "")
	journal.record(owner, "search", "start", planned=planned, mode=due.mode)
	started = time.monotonic()

	try:
		with footprint.virtual_display():
			code = run([sys.executable, "src/search_only.py"], check=False).returncode
	except Exception as exc:
		# A run that fails to even launch must not end the loop -- the
		# whole point of this process is to keep coming back later today
		# and tomorrow.
		logger.error("[FAIL] scheduled search run did not start: %s", log_utils.exception_summary(exc))
		code = None

	if code == run_lock.TIMED_OUT:
		# The profile was still busy when this run gave up waiting: nothing was searched. Not a
		# failure to alert about; it is redone shortly, as a missed run is.
		journal.record(owner, "search", "end", planned=planned, outcome="deferred", exit_code=code, reason="profile busy", seconds=round(time.monotonic() - started))
		logger.warning("The search run found the profile busy; it will be tried again.")

		return "deferred"

	if code == gate.EXIT_CODE:
		# Every account in this run is waiting for its calibration, and its owner has been told (gate.py). Not a failure.
		journal.record(owner, "search", "end", planned=planned, outcome="skipped", exit_code=code, reason="needs calibration", seconds=round(time.monotonic() - started))

		return "skipped"

	outcome = "ok" if code == 0 else "failed"

	# One failed run is not worth a message: health.py reports a run of failures, and the brake says so itself.
	if code != 0 and code != 3:
		logger.error("The scheduled search run exited with code %s.", code)

	journal.record(owner, "search", "end", planned=planned, outcome=outcome, exit_code=code, seconds=round(time.monotonic() - started))

	return outcome


MAX_SEARCH_RUNS_PER_DAY = int(os.environ.get("REWARDS_MAX_SEARCH_RUNS_PER_DAY", "8"))
FOLLOW_UP_MIN_MINUTES = float(os.environ.get("REWARDS_FOLLOW_UP_MIN_MINUTES", "45.0"))
FOLLOW_UP_MAX_MINUTES = float(os.environ.get("REWARDS_FOLLOW_UP_MAX_MINUTES", "80.0"))
END_MINUTE = int(os.environ.get("REWARDS_SEARCH_END_MINUTE", "50"))
SLEEP_START_HOUR = int(os.environ.get("REWARDS_SLEEP_START_HOUR", "2"))
SLEEP_END_HOUR = int(os.environ.get("REWARDS_SLEEP_END_HOUR", "8"))


def schedule_follow_up_run(now: datetime, owner: str, rng=random) -> datetime | None:
	"""Add an extra search run later today if quota is not yet complete and waking window is open."""
	end = now.replace(hour=END_HOUR, minute=END_MINUTE, second=0, microsecond=0)
	if end - now < MIN_WINDOW:
		return None

	names = account_names()
	if journal.quota_complete(names, now=now):
		return None

	rows = journal.events(owner=owner, kind="search", now=now)
	ended = [r for r in rows if r["event"] == "end" and r.get("outcome") != "deferred"]
	if len(ended) >= MAX_SEARCH_RUNS_PER_DAY:
		logger.info("%s: reached max search runs for today (%d/%d).", owner, len(ended), MAX_SEARCH_RUNS_PER_DAY)
		return None

	# Circuit breaker: if last 2 quota checks added 0 points, back off
	quotas = [r for r in rows if r["event"] == "quota" and r.get("account") in (names or [])]
	if len(quotas) >= 2 and quotas[-1].get("points") is not None and quotas[-1].get("points") == quotas[-2].get("points"):
		logger.warning("%s: last 2 search runs gained 0 points (cooldown or restriction). Backing off for today.", owner)
		return None

	with schedule_plan.planning():
		day = now.strftime("%Y-%m-%d")
		saved = schedule_plan.read("search", owner)
		existing = _parse(saved.get("times")) if saved.get("day") == day else []

		avoid = other_runs_today(now, owner)

		for _ in range(40):
			candidate = now + timedelta(minutes=rng.uniform(FOLLOW_UP_MIN_MINUTES, FOLLOW_UP_MAX_MINUTES))
			if candidate >= end - timedelta(minutes=10):
				continue
			if all(abs((candidate - other).total_seconds()) >= 15 * 60 for other in avoid) and all(
				abs((candidate - other).total_seconds()) >= OWN_SPACING_MINUTES * 60 for other in existing
			):
				existing.append(candidate)
				existing.sort()
				schedule_plan.write("search", owner, {"day": day, "times": [t.isoformat() for t in existing]})
				logger.info("%s: quota incomplete; scheduled follow-up search run at %s", owner, candidate.strftime("%H:%M"))
				return candidate

	return None


def main() -> None:
	log_utils.setup_logging()
	footprint.lower_priority()
	owner = run_lock.owner()

	while True:
		health.check()
		now = datetime.now()

		if SLEEP_START_HOUR <= now.hour < SLEEP_END_HOUR:
			wake_time = now.replace(hour=SLEEP_END_HOUR, minute=0, second=0, microsecond=0)
			wait = (wake_time - now).total_seconds()
			logger.info("Inside sleep window (%02d:00 - %02d:00). Resting until %02d:00 (%.1f hours).", SLEEP_START_HOUR, SLEEP_END_HOUR, SLEEP_END_HOUR, wait / 3600)
			time.sleep(wait)
			continue
		runs = outstanding(now, owner)

		if not runs:
			names = account_names()
			if not journal.quota_complete(names, now=now):
				follow_up = schedule_follow_up_run(now, owner)
				if follow_up:
					continue

			# Past today's window already, quota complete, or max runs reached.
			tomorrow = (now + timedelta(days=1)).replace(hour=START_HOUR, minute=0, second=0, microsecond=0)
			wait = (tomorrow - now).total_seconds()
			logger.info("No more runs left today for %s. Next window starts in %.1f hours", owner, wait / 3600)
			time.sleep(wait)
			continue

		logger.info(
			"%d search run(s) to make today at %s",
			len(runs),
			", ".join(due.when.strftime("%H:%M") + (" (catch-up)" if due.mode == "catch_up" else "") for due in runs),
		)

		for due in runs:
			wait = (due.when - datetime.now()).total_seconds()

			if wait > 0:
				time.sleep(wait)

			launch(owner, due)

		# If quota is still incomplete, try to schedule a follow-up run before sleeping
		names = account_names()
		if not journal.quota_complete(names, now=datetime.now()):
			follow_up = schedule_follow_up_run(datetime.now(), owner)
			if follow_up:
				continue

		# All of today's runs are done and quota is complete or window closed.
		tomorrow_start = (datetime.now() + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
		time.sleep((tomorrow_start - datetime.now()).total_seconds())


if __name__ == "__main__":
	main()
