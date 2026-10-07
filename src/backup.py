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
not against the NAS dying. For that there is a second copy, off the NAS, in a PRIVATE GitHub
repository (never the code repository, which is public):

    BACKUP_REPO=Subset28/rewards-backups          in the NAS .env
    BACKUP_GITHUB_TOKEN=github_pat_...            a token limited to that one repository (contents: write)

The off-NAS copy holds NO secrets. It leaves out the signed-in sessions, the VPN logins and the
VPN configs: those can be made again (sign in once, type the VPN login), whereas the account start
dates, points history, schedule, switches and recorded behavior profiles cannot. It does keep a
tiny summary of which VPN server each account uses. Unset, nothing is uploaded.
"""

import base64
import glob
import io
import json
import logging
import os
import sys
import tarfile
from datetime import datetime

import requests

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


def files_to_keep(data_dir: str | None = None) -> list[str]:
	"""Paths (inside data_dir) of everything that goes in a backup."""
	data_dir = data_dir or USER_DATA_DIR
	found = [os.path.join(data_dir, name) for name in STATE_FILES + STATE_DIRS if os.path.exists(os.path.join(data_dir, name))]
	found += glob.glob(os.path.join(data_dir, "*", "openvpn"))
	found += [p for p in glob.glob(os.path.join(data_dir, "PAUSED*")) if os.path.isfile(p)]

	for root in _profile_roots(data_dir):
		found += [os.path.join(root, item) for item in PROFILE_ITEMS if os.path.exists(os.path.join(root, item))]

	return found


def shareable_files(data_dir: str | None = None) -> list[str]:
	"""The state files only: nothing that signs in, authenticates or configures a VPN."""
	data_dir = data_dir or USER_DATA_DIR
	found = [os.path.join(data_dir, name) for name in STATE_FILES + STATE_DIRS if os.path.exists(os.path.join(data_dir, name))]
	found += [p for p in glob.glob(os.path.join(data_dir, "PAUSED*")) if os.path.isfile(p)]

	return found


def vpn_summary(data_dir: str | None = None) -> dict:
	"""Which server and timezone each account uses, without the config or the login."""
	data_dir = data_dir or USER_DATA_DIR
	summary = {}

	for config in sorted(glob.glob(os.path.join(data_dir, "*", "openvpn", "config.ovpn"))):
		account = os.path.basename(os.path.dirname(os.path.dirname(config)))
		entry = {}

		try:
			with open(config, encoding="utf-8", errors="replace") as handle:
				remotes = [line.split()[1:3] for line in handle if line.startswith("remote ") and len(line.split()) >= 3]

			entry["remote"] = " ".join(remotes[0]) if remotes else None
			timezone = os.path.join(os.path.dirname(config), "timezone")

			if os.path.exists(timezone):
				with open(timezone, encoding="utf-8") as handle:
					entry["timezone"] = handle.read().strip()
		except OSError:
			continue

		summary[account] = entry

	return summary


def shareable_archive(data_dir: str | None = None) -> bytes:
	"""The off-NAS copy, as a gzip tar in memory."""
	data_dir = data_dir or USER_DATA_DIR
	buffer = io.BytesIO()

	with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
		for item in shareable_files(data_dir):
			archive.add(item, arcname=os.path.relpath(item, data_dir))

		raw = json.dumps(vpn_summary(data_dir), indent=1).encode()
		info = tarfile.TarInfo("vpn-servers.json")
		info.size = len(raw)
		archive.addfile(info, io.BytesIO(raw))

	return buffer.getvalue()


OFFSITE_PATH = "state/rewards-state.tar.gz"
FAILING_MARKER = ".offsite-failing"
API = "https://api.github.com"


def upload(content: bytes, repo: str, token: str, path: str = OFFSITE_PATH, message: str = "state backup", session=requests) -> bool:
	"""Put `content` at `path` in a GitHub repository, replacing what is there. True if it went."""
	headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json", "User-Agent": "rewards-farmer-backup"}
	url = f"{API}/repos/{repo}/contents/{path}"

	for _ in range(2):
		current = session.get(url, headers=headers, timeout=30)
		body = {"message": message, "content": base64.b64encode(content).decode()}

		if current.status_code == 200:
			body["sha"] = current.json().get("sha")
		elif current.status_code != 404:
			logger.warning("[BACKUP] the off-NAS copy was refused (%s) when looking for the old one.", current.status_code)

			return False

		reply = session.put(url, headers=headers, json=body, timeout=60)

		if reply.status_code in (200, 201):
			return True

		# Someone else wrote in between (another scheduler): look again once.
		if reply.status_code not in (409, 422):
			logger.warning("[BACKUP] the off-NAS copy was refused (%s).", reply.status_code)

			return False

	return False


def offsite_if_due(data_dir: str | None = None, directory: str | None = None, now: datetime | None = None, session=requests) -> bool:
	"""Send today's secret-free copy off the NAS, once a day, if it is set up. Never raises."""
	data_dir, directory = data_dir or USER_DATA_DIR, directory or BACKUP_DIR
	repo = os.environ.get("BACKUP_REPO", "").strip()
	token = os.environ.get("BACKUP_GITHUB_TOKEN", "").strip()

	if not repo or not token:
		return False

	try:
		now = now or clock.now()
		marker = os.path.join(directory, f".offsite-{now.strftime('%Y%m%d')}")

		if os.path.exists(marker):
			return False

		os.makedirs(directory, exist_ok=True)
		failing = os.path.join(directory, FAILING_MARKER)

		if not upload(shareable_archive(data_dir), repo, token, message=f"state backup {now.strftime('%Y-%m-%d %H:%M')}", session=session):
			# Remember since when it has been failing (the first failure), for health.py.
			if not os.path.exists(failing):
				with open(failing, "w", encoding="utf-8") as handle:
					handle.write(now.isoformat(timespec="seconds"))

			return False

		open(marker, "w").close()

		try:
			os.unlink(failing)
		except OSError:
			pass

		for old in sorted(glob.glob(os.path.join(directory, ".offsite-*")))[:-KEEP]:
			try:
				os.unlink(old)
			except OSError:
				pass

		logger.info("[BACKUP] sent today's state copy off the NAS to %s", repo)

		return True
	except Exception as exc:
		# The message of a requests error can include the address, never the token, but say
		# only what kind of error it was to be sure nothing about the request is logged.
		logger.warning("[BACKUP] could not send the off-NAS copy (%s).", type(exc).__name__)

		return False


def offsite_failing_since(directory: str | None = None) -> str | None:
	"""When the off-NAS copy started failing, or None if it is fine or not set up."""
	directory = directory or BACKUP_DIR
	try:
		with open(os.path.join(directory, FAILING_MARKER), encoding="utf-8") as handle:
			return handle.read().strip() or None
	except OSError:
		return None


def existing(directory: str | None = None) -> list[str]:
	directory = directory or BACKUP_DIR
	return sorted(glob.glob(os.path.join(directory, "rewards-*.tar.gz")))


def _stamp(now: datetime) -> str:
	return now.strftime("%Y%m%d-%H%M")


def create(data_dir: str | None = None, directory: str | None = None, now: datetime | None = None, keep: int = KEEP) -> str | None:
	"""Make today's backup unless one exists. Returns its path, or None if there was nothing to do."""
	data_dir, directory = data_dir or USER_DATA_DIR, directory or BACKUP_DIR
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
	path = None

	try:
		path = create()

		if path:
			logger.info("[BACKUP] wrote %s (%d KB)", os.path.basename(path), os.path.getsize(path) // 1024)
	except Exception as exc:
		logger.warning("[BACKUP] could not make today's backup: %s", exc)

	# Tried every loop until it has gone once today, so a failed upload is retried.
	offsite_if_due()

	return path


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
