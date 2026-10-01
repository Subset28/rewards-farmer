import logging
import os
import random
import subprocess
import sys
import time
from datetime import datetime, timedelta

import log_utils
import safety

logger = logging.getLogger(__name__)

# Required searches spread across several random times a day instead of one
# batch, so 25 searches don't all land in the same few minutes -- the same
# reasoning as daily_loop.py's anchor+jitter, applied within a day rather than
# across days. Window is waking hours by default; a search run at 3am is more
# suspicious than the batch it is meant to avoid looking like.
RUNS_PER_DAY = int(os.environ.get("REWARDS_SEARCH_RUNS_PER_DAY", "4"))
START_HOUR = int(os.environ.get("REWARDS_SEARCH_START_HOUR", "8"))
END_HOUR = int(os.environ.get("REWARDS_SEARCH_END_HOUR", "23"))


def next_run_times(now: datetime) -> list[datetime]:
	"""RUNS_PER_DAY random timestamps within today's [START_HOUR, END_HOUR)
	window, sorted, keeping only those still ahead of `now`."""
	window_start = now.replace(hour=START_HOUR, minute=0, second=0, microsecond=0)
	window_seconds = (END_HOUR - START_HOUR) * 3600

	times = sorted(
		window_start + timedelta(seconds=random.uniform(0, window_seconds))
		for _ in range(RUNS_PER_DAY)
	)

	return [t for t in times if t > now]


def main() -> None:
	log_utils.setup_logging()

	while True:
		now = datetime.now()
		todays_runs = next_run_times(now)

		if not todays_runs:
			# Past today's window already (started late, or window rolled by
			# while asleep). Wait for tomorrow's window instead of running late.
			tomorrow = (now + timedelta(days=1)).replace(hour=START_HOUR, minute=0, second=0, microsecond=0)
			wait = (tomorrow - now).total_seconds()
			logger.info("No more runs left today. Next window starts in %.1f hours", wait / 3600)
			time.sleep(wait)
			continue

		logger.info(
			"%d search run(s) scheduled today at %s",
			len(todays_runs),
			", ".join(t.strftime("%H:%M") for t in todays_runs),
		)

		for run_time in todays_runs:
			wait = (run_time - datetime.now()).total_seconds()

			if wait > 0:
				time.sleep(wait)

			hold = safety.paused()

			if hold:
				logger.error("[BRAKE] Skipping this search run: paused (%s: %s).", hold.get("kind"), hold.get("reason"))

				continue

			logger.info("=== starting scheduled search run ===")

			try:
				subprocess.run([sys.executable, "src/search_only.py"], check=False)
			except Exception as exc:
				# A run that fails to even launch must not end the loop -- the
				# whole point of this process is to keep coming back later today
				# and tomorrow.
				logger.error("[FAIL] scheduled search run did not start: %s", log_utils.exception_summary(exc))

		# All of today's runs are done. Sleep past midnight so the next loop
		# iteration picks fresh random times for tomorrow instead of finding
		# an empty, already-past window and looping immediately.
		tomorrow_start = (datetime.now() + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
		time.sleep((tomorrow_start - datetime.now()).total_seconds())


if __name__ == "__main__":
	main()
