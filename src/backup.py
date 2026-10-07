"""A daily copy of the small files that would be painful to lose.

Everything the bot has learned and decided is in data-dir: each account's start date (which
decides whether it is "new"), the points history, the schedule, the switches that are on, the
behavior profiles recorded from real hands, the VPN configs, the pause records. Lose that folder
and the accounts are treated as new, ramped from scratch, and each one has to be signed in again by
a person. This keeps a compressed copy of just those files (a few hundred KB, not the 400 MB of
browser caches) plus the files that hold each account's signed-in session.

    python src/backup.py            make today's backup now (if there is not one yet)
    python src/backup.py --list     what exists
    restore:  tar -xzf data-dir/backups/<file>.tar.gz -C data-dir     (with the schedulers stopped)

The scheduler calls run_if_due() once a loop, so one backup a day happens without a cron job.
Several schedulers share the folder; the first to ask for the day's backup makes it. The last
KEEP are kept. Nothing here ever raises, a backup must not be why a run fails.

This is a copy on the same disk: it guards against a bad edit, a bad deploy and a corrupted file,
not against the NAS dying. Copy data-dir/backups somewhere else for that.
"""

import glob
import logging
import os
import sys
import tarfile
from datetime import datetime

import clock
from constants import USER_DATA_DIR

logger = logging.getLogger(__name__)

BACKUP_DIR = os.path.join(USER_DATA_DIR, "backups")
KEEP = 14

STATE_FILES = (
	"features.json", "health.json", "journal.jsonl", "openrouter_pool.json", "openrouter_usage.json",
	"pacing.json", "points.jsonl", "query_history.jsonl", "schedule_plan.json",
)
STATE_DIRS = ("behavior", "persona", "interests")

# What keeps an account signed in, relative to a profile's root (the data folder itself for the
# unnamed profile, data-dir/<name> for the others). Not the caches.
PROFILE_ITEMS = (
	"Local State", "Default/Cookies", "Default/Network/Cookies", "Default/Preferences", "Default/Secure Preferences",
	"Default/Login Data", "Default/Web Data", "Default/Local Storage", "Default/Session Storage", "Default/Sessions",
)


def _profile_roots(data_dir: str) -> list[str]:
	roots = [data_dir] if os.path.exists(os.path.join(data_dir, "Local State")) else []

	try:
		for name in sorted(os.listdir(data_dir)):
			path = os.path.join(data_dir, name)

			if os.path.isdir(path) and os.path.exists(os.path.join(path, "Local State")):
				roots.append(path)
	except OSError:
		pass

	return roots


def files_to_keep(data_dir: str = USER_DATA_DIR) -> list[str]:
	"""Paths (inside data_dir) of everything that goes in a backup."""
	found = [os.path.join(data_dir, name) for name in STATE_FILES + STATE_DIRS if os.path.exists(os.path.join(data_dir, name))]
	found += glob.glob(os.path.join(data_dir, "*", "openvpn"))
	found += [p for p in glob.glob(os.path.join(data_dir, "PAUSED*")) if os.path.isfile(p)]

	for root in _profile_roots(data_dir):
		found += [os.path.join(root, item) for item in PROFILE_ITEMS if os.path.exists(os.path.join(root, item))]

	return found


def existing(directory: str = BACKUP_DIR) -> list[str]:
	return sorted(glob.glob(os.path.join(directory, "rewards-*.tar.gz")))


def _stamp(now: datetime) -> str:
	return now.strftime("%Y%m%d-%H%M")


def create(data_dir: str = USER_DATA_DIR, directory: str = BACKUP_DIR, now: datetime | None = None, keep: int = KEEP) -> str | None:
	"""Make today's backup unless one exists. Returns its path, or None if there was nothing to do."""
	now = now or clock.now()
	day = now.strftime("%Y%m%d")
	os.makedirs(directory, exist_ok=True)

	# The first scheduler to get here owns today's backup; the others see the marker and stop.
	marker = os.path.join(directory, f".{day}")

	try:
		os.close(os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
	except FileExistsError:
		return None

	path = os.path.join(directory, f"rewards-{_stamp(now)}.tar.gz")
	temporary = f"{path}.{os.getpid()}.tmp"

	try:
		with tarfile.open(temporary, "w:gz") as archive:
			for item in files_to_keep(data_dir):
				archive.add(item, arcname=os.path.relpath(item, data_dir))

		os.chmod(temporary, 0o600)
		os.replace(temporary, path)
	except Exception:
		# Let the next loop try again today.
		for leftover in (temporary, marker):
			try:
				os.unlink(leftover)
			except OSError:
				pass

		raise

	for old in existing(directory)[:-keep]:
		try:
			os.unlink(old)
		except OSError:
			pass

	for old_marker in sorted(glob.glob(os.path.join(directory, ".2*")))[:-keep]:
		try:
			os.unlink(old_marker)
		except OSError:
			pass

	return path


def run_if_due() -> str | None:
	"""Called from the scheduler loop. Never raises."""
	try:
		path = create()

		if path:
			logger.info("[BACKUP] wrote %s (%d KB)", os.path.basename(path), os.path.getsize(path) // 1024)

		return path
	except Exception as exc:
		logger.warning("[BACKUP] could not make today's backup: %s", exc)

		return None


def main(argv: list[str]) -> int:
	if "--list" in argv:
		for path in existing():
			print(f"{os.path.basename(path)}  {os.path.getsize(path) // 1024} KB")

		return 0

	path = create()
	print(f"wrote {path}" if path else "today's backup already exists")

	return 0


if __name__ == "__main__":
	sys.exit(main(sys.argv))
