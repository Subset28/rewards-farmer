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
import notify
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
	("morning", 8.5, 10.5),
	("midday", 12.0, 15.0),
	("evening", 18.0, 21.5),
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

	for name in account_names() or []:
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


def habit_windows(owner: str, weekend: bool) -> list[tuple[float, float]]:
	"""The owner's favoured windows as (start, end) in minutes after midnight, sorted.

	Depends only on the owner and whether it is a weekend, never on the date, so
	the habit is the same every weekday and every weekend. 2 or 3 of the
	morning/midday/evening windows are kept, chosen per owner.
	"""
	# Which slots this owner uses: always at least two. Of the five equal parts of
	# the hash, three drop one slot each and two keep all three (index 3 matches none).
	dropped = int(_unit(owner, "habit-drop") * 5)
	slots = [slot for index, slot in enumerate(_HABIT_SLOTS) if index != dropped]
	windows = []

	for name, low, high in slots:
		centre = (low + (high - low) * _unit(owner, f"habit-centre-{name}")) * 60
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
	windows = [(max(a, lo), min(b, hi)) for a, b in habit_windows(owner, start.weekday() >= 5)]
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

	# Exit 3 is the brake, which has already said so when it tripped.
	if code != 0 and code != 3:
		notify.send_each(names, "Search run failed", f"The scheduled search run exited with code {code}. Look at data-dir/logs/ on the NAS.", priority="high")

	journal.record(owner, "search", "end", planned=planned, outcome=outcome, exit_code=code, seconds=round(time.monotonic() - started))

	return outcome


def main() -> None:
	log_utils.setup_logging()
	footprint.lower_priority()
	owner = run_lock.owner()

	while True:
		health.check()
		now = datetime.now()
		runs = outstanding(now, owner)

		if not runs:
			# Past today's window already (started late, or window rolled by
			# while asleep), or everything planned has been done. Wait for
			# tomorrow's window instead of running late.
			tomorrow = (now + timedelta(days=1)).replace(hour=START_HOUR, minute=0, second=0, microsecond=0)
			wait = (tomorrow - now).total_seconds()
			logger.info("No more runs left today. Next window starts in %.1f hours", wait / 3600)
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

		# All of today's runs are done. Sleep past midnight so the next loop
		# iteration picks fresh random times for tomorrow instead of finding
		# an empty, already-past window and looping immediately.
		tomorrow_start = (datetime.now() + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
		time.sleep((tomorrow_start - datetime.now()).total_seconds())


if __name__ == "__main__":
	main()
