"""How close the container is to its memory limit, so a long run can back off before it is killed.

A browser run was measured climbing from about 590 MB to 1,267 MB over eight searches, against a 1,536 MB limit.
Searching in tangents, and opening result pages from other sites, can take it further. The kernel does not warn:
it kills the browser and leaves a stale lock. This reads the container's own figures (cgroup v1 or v2) and gives
the run three decisions, each with a margin below the limit:

    can_open_a_result()   a third-party page is the heaviest thing a run does; skip it when memory is already high
    needs_relief()        between tangents, drop the old pages (the caller navigates to a blank tab)
    must_stop()           end the run now, cleanly, and leave the rest of the quota to the next one

What counts is memory the program really holds (resident memory and shared memory), not the file cache, which the
kernel gives back whenever it is needed. Unreadable figures (not in a container, no limit set) mean "no pressure".
"""

import logging
import os

logger = logging.getLogger(__name__)

OPEN_RESULT_BELOW = 0.62
RELIEF_ABOVE = 0.74
STOP_ABOVE = 0.88

V1 = "/sys/fs/cgroup/memory"
V2 = "/sys/fs/cgroup"
UNLIMITED = 1 << 60


def _number(path: str) -> int | None:
	try:
		with open(path, encoding="ascii") as handle:
			text = handle.read().strip()
	except OSError:
		return None

	if text == "max":
		return None

	try:
		return int(text)
	except ValueError:
		return None


def _stat(path: str) -> dict[str, int]:
	values = {}

	try:
		with open(path, encoding="ascii") as handle:
			for line in handle:
				name, _, value = line.partition(" ")

				if value.strip().isdigit():
					values[name] = int(value)
	except OSError:
		pass

	return values


def reading(v1: str = V1, v2: str = V2) -> tuple[int, int] | None:
	"""(bytes held, limit in bytes), or None if either is unknown."""
	limit = _number(os.path.join(v1, "memory.limit_in_bytes"))
	stat = _stat(os.path.join(v1, "memory.stat"))

	if limit and stat:
		held = stat.get("total_rss", stat.get("rss", 0)) + stat.get("total_shmem", stat.get("shmem", 0))
	else:
		limit = _number(os.path.join(v2, "memory.max"))
		stat = _stat(os.path.join(v2, "memory.stat"))
		held = stat.get("anon", 0) + stat.get("shmem", 0)

	if not limit or limit >= UNLIMITED or not stat:
		return None

	return held, limit


def fraction(v1: str = V1, v2: str = V2) -> float | None:
	"""Share of the limit held (0 to 1), or None when it cannot be told."""
	figures = reading(v1, v2)

	return None if figures is None else figures[0] / figures[1]


def can_open_a_result(**paths) -> bool:
	share = fraction(**paths)

	return share is None or share < OPEN_RESULT_BELOW


def needs_relief(**paths) -> bool:
	share = fraction(**paths)

	return share is not None and share >= RELIEF_ABOVE


def must_stop(**paths) -> bool:
	share = fraction(**paths)

	return share is not None and share >= STOP_ABOVE


def describe(**paths) -> str:
	figures = reading(**paths)

	return "memory unknown" if figures is None else f"{figures[0] // 2**20} of {figures[1] // 2**20} MB"
