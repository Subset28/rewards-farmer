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
import journal
import log_utils
import notify
import pacing
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


def draw_times(now: datetime) -> list[datetime]:
	"""Random run times in what is left of today's [START_HOUR, END_HOUR) window, sorted.

	A full day's RUNS_PER_DAY when the day has not started, proportionally fewer
	when this is made part way through (at least one while 30 minutes remain),
	and none when the window is nearly over.
	"""
	start = now.replace(hour=START_HOUR, minute=0, second=0, microsecond=0)
	end = now.replace(hour=END_HOUR, minute=0, second=0, microsecond=0)
	begin = max(start, now + timedelta(minutes=2))

	if end - begin < MIN_WINDOW:
		return []

	fraction = (end - begin) / (end - start)
	count = RUNS_PER_DAY if fraction >= 1 else max(1, math.ceil(RUNS_PER_DAY * fraction))
	span = (end - begin).total_seconds()

	return sorted(begin + timedelta(seconds=random.uniform(0, span)) for _ in range(count))


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
		times = draw_times(now)
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
	ended = {row.get("planned") for row in rows if row["event"] == "end"}
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

	# An account on a rest day has nothing to do, and does not hold the run open.
	active = [n for n in names if not pacing.is_rest_day(n)] if names else names

	if names and not active:
		journal.record(owner, "search", "end", planned=planned, outcome="skipped", reason="rest day")
		logger.info("Skipping this search run: every account is resting today.")

		return "skipped"

	if journal.quota_complete(active):
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
		code = run([sys.executable, "src/search_only.py"], check=False).returncode
	except Exception as exc:
		# A run that fails to even launch must not end the loop -- the
		# whole point of this process is to keep coming back later today
		# and tomorrow.
		logger.error("[FAIL] scheduled search run did not start: %s", log_utils.exception_summary(exc))
		code = None

	outcome = "ok" if code == 0 else "failed"

	# Exit 3 is the brake, which has already said so when it tripped.
	if code != 0 and code != 3:
		notify.send_each(names, "Search run failed", f"The scheduled search run exited with code {code}. Look at data-dir/logs/ on the NAS.", priority="high")

	journal.record(owner, "search", "end", planned=planned, outcome=outcome, exit_code=code, seconds=round(time.monotonic() - started))

	return outcome


def main() -> None:
	log_utils.setup_logging()
	owner = run_lock.owner()

	while True:
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
