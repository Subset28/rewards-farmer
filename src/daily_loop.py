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

# An anchor time plus a random offset, rather than a fixed cron time, so the
# run doesn't start at the exact same wall-clock moment every day -- the
# whole point of the human-timing work in mimic_typing.py/mouse_trajectory.py
# is undermined by a bot that is otherwise clockwork-punctual once a day.
ANCHOR_HOUR = int(os.environ.get("REWARDS_ANCHOR_HOUR", "9"))
JITTER_SECONDS = int(os.environ.get("REWARDS_JITTER_SECONDS", str(3 * 60 * 60)))


def seconds_until_next_anchor(now: datetime) -> float:
	target = now.replace(hour=ANCHOR_HOUR, minute=0, second=0, microsecond=0)

	if target <= now:
		target += timedelta(days=1)

	return (target - now).total_seconds()


def main() -> None:
	log_utils.setup_logging()

	while True:
		wait = seconds_until_next_anchor(datetime.now())
		jitter = random.uniform(0, JITTER_SECONDS)

		logger.info(
			"Next run in %.1f hours (anchor) + %.1f minutes (jitter)",
			wait / 3600,
			jitter / 60,
		)

		time.sleep(wait + jitter)

		hold = safety.paused()

		if hold:
			logger.error("[BRAKE] Skipping today's run: paused (%s: %s).", hold.get("kind"), hold.get("reason"))

			continue

		logger.info("=== starting scheduled run ===")

		try:
			subprocess.run([sys.executable, "src/main.py"], check=False)
		except Exception as exc:
			# A run that fails to even launch must not end the loop -- the
			# whole point of this process is to keep coming back tomorrow.
			logger.error("[FAIL] scheduled run did not start: %s", log_utils.exception_summary(exc))


if __name__ == "__main__":
	main()
