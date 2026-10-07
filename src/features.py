"""Switches for behaviour changes that go live one at a time, per account.

Each of these changes what Microsoft sees from a real account. Tests can show the code runs;
only live results (points earned, verification prompts, the Bing Star score) show whether the
behaviour reads as natural. Turned on together, an account that gets flagged cannot be traced
to the change that did it, and a ban costs the account. So each is off until it is switched on,
for one account first (the one that matters least), a few days apart.

    data-dir/features.json    {"typing": ["second"], "query_sessions": [], "habits": []}

A feature is on for an account that is listed under it, and for every account if "*" is. Nothing
is on by default, and a missing or unreadable file means everything is off: the old behaviour,
exactly. The file is read on every use, so a change takes effect on the next search with no
restart.

    python src/features.py                      what is on, per feature
    python src/features.py on typing second     switch typing on for the "second" account
    python src/features.py off typing second    and off again
    python src/features.py on habits '*'        on for every account

REWARDS_FEATURES=typing,habits turns those on for every account regardless of the file (for
tests and one-off runs).

Features:

    typing           corrected typos, word-boundary and thinking pauses, and a recorded profile's own
                     slip rate, pauses and correction habits (mimic_typing.py)
    mouse            a recorded profile's own click hold time and hover before a click (mouse_trajectory.py)
    query_sessions   queries grouped into topical sessions with follow-ups (query_sources.py)
    habits           each owner's own favoured times of day for search runs (search_scheduler.py)
"""

import contextlib
import json
import logging
import os
import sys
import time

from constants import USER_DATA_DIR

logger = logging.getLogger(__name__)

FEATURES_FILE = os.path.join(USER_DATA_DIR, "features.json")

KNOWN = ("typing", "mouse", "query_sessions", "habits")

ENV = "REWARDS_FEATURES"
EVERY_ACCOUNT = "*"


def _read() -> dict[str, list[str]]:
	"""The file's contents, as {feature: [accounts]}. Anything unreadable or malformed is simply not on."""
	try:
		with open(FEATURES_FILE, encoding="utf-8") as handle:
			data = json.load(handle)
	except (OSError, ValueError):
		return {}

	if not isinstance(data, dict):
		return {}

	return {
		feature: [str(name) for name in names if isinstance(name, str)]
		for feature, names in data.items()
		if feature in KNOWN and isinstance(names, list)
	}


def _from_env() -> set[str]:
	return {name.strip() for name in os.environ.get(ENV, "").split(",") if name.strip()}


def _fold(name: str) -> str:
	"""Account names are matched without regard to case, as accounts.py does ("Default" is "default")."""
	return name.strip().casefold()


def enabled(feature: str, account: str | None = None) -> bool:
	"""Whether `feature` is on for `account`.

	`account` may be a scheduler's owner name such as "default,second" (the accounts it works):
	the feature is on if it is on for any of them. With no account, only "*" and the
	environment switch can turn it on."""
	if feature not in KNOWN:
		return False

	if feature in _from_env():
		return True

	listed = {_fold(name) for name in _read().get(feature, [])}

	if EVERY_ACCOUNT in listed:
		return True

	if not account:
		return False

	return any(_fold(name) in listed for name in account.split(","))


def active_for(account: str | None) -> list[str]:
	return [feature for feature in KNOWN if enabled(feature, account)]


def _write(data: dict[str, list[str]]) -> None:
	os.makedirs(os.path.dirname(FEATURES_FILE), exist_ok=True)
	temporary = f"{FEATURES_FILE}.{os.getpid()}.tmp"

	try:
		with open(temporary, "w", encoding="utf-8") as handle:
			json.dump({feature: data.get(feature, []) for feature in KNOWN}, handle, indent=1)

		# On Windows a file another process has open (a scanner, an indexer, a reader) cannot be
		# replaced for a moment; it is almost always free again straight away.
		for attempt in range(6):
			try:
				os.replace(temporary, FEATURES_FILE)

				break
			except PermissionError:
				if attempt == 5:
					raise

				time.sleep(0.05)
	except OSError:
		try:
			os.unlink(temporary)
		except OSError:
			pass

		raise


LOCK_WAIT_SECONDS = 5.0
LOCK_STALE_SECONDS = 30.0


@contextlib.contextmanager
def _locked():
	"""Hold a lock file while the file is read, changed and written back.

	Without it two switches racing could each read the old file and the later write would
	undo the earlier one. A lost "off" would leave a feature on, which is the unsafe way
	round. A lock left by a process that died is taken over once it is old enough."""
	lock = f"{FEATURES_FILE}.lock"
	os.makedirs(os.path.dirname(FEATURES_FILE), exist_ok=True)
	deadline = time.monotonic() + LOCK_WAIT_SECONDS

	while True:
		try:
			os.close(os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
			break
		except PermissionError:
			# Windows says this, not "exists", while another switch is deleting the lock it held.
			if time.monotonic() >= deadline:
				raise OSError(f"{lock} is held by another switch; try again in a moment")

			time.sleep(0.02)
		except FileExistsError:
			try:
				if time.time() - os.path.getmtime(lock) > LOCK_STALE_SECONDS:
					os.unlink(lock)
					continue
			except OSError:
				continue

			if time.monotonic() >= deadline:
				raise OSError(f"{lock} is held by another switch; try again in a moment")

			time.sleep(0.05)

	try:
		yield
	finally:
		try:
			os.unlink(lock)
		except OSError:
			pass


def switch(feature: str, account: str, on: bool) -> None:
	"""Turn `feature` on or off for `account` ("*" for every account), keeping everything else as it was."""
	if feature not in KNOWN:
		raise ValueError(f"unknown feature {feature!r}; known: {', '.join(KNOWN)}")

	account = account.strip()

	# One account per entry: "default,second" would be stored as a single name that no
	# account matches, and the feature would silently stay off.
	if not account or "," in account:
		raise ValueError(f"give one account name (or {EVERY_ACCOUNT}), not {account!r}; run it once per account")

	with _locked():
		data = _read()
		names = [n for n in data.get(feature, []) if _fold(n) != _fold(account)]

		if on:
			names.append(account)

		data[feature] = names
		_write(data)


def describe() -> str:
	data = _read()
	lines = []

	for feature in KNOWN:
		names = data.get(feature, [])
		lines.append(f"  {feature:<15} " + (", ".join(names) if names else "off for everyone"))

	if _from_env():
		lines.append(f"  (also on for everyone from {ENV}: {', '.join(sorted(_from_env()))})")

	return "\n".join(lines)


def main(args: list[str]) -> int:
	if not args:
		print(describe())

		return 0

	if len(args) == 3 and args[0] in ("on", "off"):
		try:
			switch(args[1], args[2], args[0] == "on")
		except (ValueError, OSError) as exc:
			print(exc)

			return 2

		print(describe())

		# A name no account answers to would silently do nothing, so say so.
		try:
			import status

			known = {_fold(n) for n in status.known_names()}

			if args[2] != EVERY_ACCOUNT and _fold(args[2]) not in known:
				print(f"note: {args[2]!r} is not an account this setup knows (known: {', '.join(sorted(known)) or 'none'}).")
		except Exception:
			pass

		return 0

	print("usage: features.py [on|off <feature> <account or *>]")

	return 2


if __name__ == "__main__":
	sys.exit(main(sys.argv[1:]))
