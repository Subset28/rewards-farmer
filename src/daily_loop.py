import logging
import os
import random
import subprocess
import sys
import time
from datetime import datetime, timedelta

import accounts
import footprint
import journal
import log_utils
import notify
import run_lock
import safety
import schedule_plan

logger = logging.getLogger(__name__)

# An anchor time plus a random offset, rather than a fixed cron time, so the
# run doesn't start at the exact same wall-clock moment every day -- the
# whole point of the human-timing work in mimic_typing.py/mouse_trajectory.py
# is undermined by a bot that is otherwise clockwork-punctual once a day.
ANCHOR_HOUR = int(os.environ.get("REWARDS_ANCHOR_HOUR", "9"))
JITTER_SECONDS = int(os.environ.get("REWARDS_JITTER_SECONDS", str(3 * 60 * 60)))

# A run that was planned but missed because the scheduler was down is run on
# the next start if it is no more than this late, rather than skipped for a day.
CATCH_UP = timedelta(hours=6)


def account_names() -> list[str] | None:
	"""Names of the accounts this scheduler runs, or None when they cannot be read."""
	try:
		return [a.name for a in accounts.configured()]
	except ValueError:
		return None


def seconds_until_next_anchor(now: datetime) -> float:
	target = now.replace(hour=ANCHOR_HOUR, minute=0, second=0, microsecond=0)

	if target <= now:
		target += timedelta(days=1)

	return (target - now).total_seconds()


def plan_next_run(now: datetime, owner: str) -> datetime:
	"""When the next run starts, the same moment across restarts.

	A run already planned and still ahead is kept, so a restart does not redraw
	the jitter (and a restart between the anchor and the drawn start no longer
	skips the day). A planned run that passed unrun while the scheduler was down is
	run at once if it is within CATCH_UP, otherwise a new one is drawn.
	"""
	saved = schedule_plan.read("daily", owner)

	try:
		planned = datetime.fromisoformat(saved.get("at")) if saved.get("at") else None
	except (TypeError, ValueError):
		planned = None

	if planned is not None and not saved.get("ran"):
		if planned > now:
			return planned

		if now - planned <= CATCH_UP:
			return now

	at = now + timedelta(seconds=seconds_until_next_anchor(now) + random.uniform(0, JITTER_SECONDS))
	schedule_plan.write("daily", owner, {"at": at.isoformat(), "ran": False})

	return at


def mark_done(planned: datetime, owner: str) -> None:
	schedule_plan.write("daily", owner, {"at": planned.isoformat(), "ran": True})


def main() -> None:
	log_utils.setup_logging()
	footprint.lower_priority()
	owner = run_lock.owner()

	while True:
		now = datetime.now()
		at = plan_next_run(now, owner)
		wait = max(0.0, (at - now).total_seconds())

		logger.info("Next run in %.1f hours (%s)", wait / 3600, at.strftime("%a %H:%M"))

		time.sleep(wait)

		hold = safety.blocked(account_names())

		if hold:
			logger.error("[BRAKE] Skipping today's run: paused (%s: %s).", hold.get("kind"), hold.get("reason"))
			journal.record(owner, "daily", "end", planned=at.isoformat(), outcome="skipped", reason="paused")
			mark_done(at, owner)

			continue

		logger.info("=== starting scheduled run ===")
		journal.record(owner, "daily", "start", planned=at.isoformat())
		started = time.monotonic()

		try:
			with footprint.virtual_display():
				code = subprocess.run([sys.executable, "src/main.py"], check=False).returncode
		except Exception as exc:
			# A run that fails to even launch must not end the loop -- the
			# whole point of this process is to keep coming back tomorrow.
			logger.error("[FAIL] scheduled run did not start: %s", log_utils.exception_summary(exc))
			code = None

		if code == run_lock.TIMED_OUT:
			# Another run still held the profile when this one gave up waiting. The day's daily
			# set was not done, and marking it done would break its streak, so it is tried
			# again shortly (plan_next_run still finds it within CATCH_UP).
			journal.record(owner, "daily", "end", planned=at.isoformat(), outcome="deferred", exit_code=code, reason="profile busy", seconds=round(time.monotonic() - started))
			logger.warning("The daily run found the profile busy; trying again in a few minutes.")
			time.sleep(random.uniform(10 * 60, 20 * 60))

			continue

		journal.record(owner, "daily", "end", planned=at.isoformat(), outcome="ok" if code == 0 else "failed", exit_code=code, seconds=round(time.monotonic() - started))

		# Exit 3 is the brake, which has already said so when it tripped.
		if code != 0 and code != 3:
			notify.send_each(account_names(), "Daily run failed", f"The scheduled daily run exited with code {code}. Look at data-dir/logs/ on the NAS.", priority="high")

		mark_done(at, owner)


if __name__ == "__main__":
	main()
