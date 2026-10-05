"""One clock for every date the bot decides things by.

An account's browser is given the timezone of its VPN exit (isolation.py sets TZ for the
child process), so the browser reports a place that matches the address. But the child is
also a Python process, and Python's local time follows TZ. Left alone, an account exiting
in Tokyo would stamp its journal, pacing and points with Tokyo's date while the scheduler
that launched it works by the NAS's, and the two would disagree about what "today" is: a
quota marked complete for a Tokyo day could skip a real run in New York.

So dates are not taken from TZ. REWARDS_CLOCK_TZ names the zone the bot keeps its books in;
isolation.py sets it to the parent's own zone for each child. Unset, the process's local time
is used, which is right for a single process and for the schedulers themselves.
"""

import logging
import os
from datetime import date, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

logger = logging.getLogger(__name__)

ENV = "REWARDS_CLOCK_TZ"


def parent_zone() -> str:
	"""The zone this process keeps its books in, to hand on to a child that changes its own TZ."""
	return os.environ.get(ENV, "").strip() or os.environ.get("TZ", "").strip()


def now() -> datetime:
	"""The current wall-clock time in the bot's zone, as a naive datetime."""
	name = os.environ.get(ENV, "").strip()

	if name:
		try:
			return datetime.now(ZoneInfo(name)).replace(tzinfo=None)
		except (ZoneInfoNotFoundError, ValueError):
			logger.warning("%s=%r is not a timezone, using local time.", ENV, name)

	return datetime.now()


def today() -> date:
	return now().date()


def stamp(fmt: str = "%Y-%m-%d %H:%M:%S") -> str:
	return now().strftime(fmt)
