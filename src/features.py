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

    typing           corrected typos, word-boundary and thinking pauses (mimic_typing.py)
    query_sessions   queries grouped into topical sessions with follow-ups (query_sources.py)
    habits           each owner's own favoured times of day for search runs (search_scheduler.py)
"""

import json
import logging
import os
import sys

from constants import USER_DATA_DIR

logger = logging.getLogger(__name__)

FEATURES_FILE = os.path.join(USER_DATA_DIR, "features.json")

KNOWN = ("typing", "query_sessions", "habits")

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


def enabled(feature: str, account: str | None = None) -> bool:
	"""Whether `feature` is on for `account`.

	`account` may be a scheduler's owner name such as "default,second" (the accounts it works):
	the feature is on if it is on for any of them. With no account, only "*" and the
	environment switch can turn it on."""
	if feature not in KNOWN:
		return False

	if feature in _from_env():
		return True

	listed = _read().get(feature, [])

	if EVERY_ACCOUNT in listed:
		return True

	if not account:
		return False

	return any(name.strip() in listed for name in account.split(","))


def active_for(account: str | None) -> list[str]:
	return [feature for feature in KNOWN if enabled(feature, account)]


def _write(data: dict[str, list[str]]) -> None:
	os.makedirs(os.path.dirname(FEATURES_FILE), exist_ok=True)
	temporary = f"{FEATURES_FILE}.{os.getpid()}.tmp"

	with open(temporary, "w", encoding="utf-8") as handle:
		json.dump({feature: data.get(feature, []) for feature in KNOWN}, handle, indent=1)

	os.replace(temporary, FEATURES_FILE)


def switch(feature: str, account: str, on: bool) -> None:
	"""Turn `feature` on or off for `account` ("*" for every account), keeping everything else as it was."""
	if feature not in KNOWN:
		raise ValueError(f"unknown feature {feature!r}; known: {', '.join(KNOWN)}")

	data = _read()
	names = [n for n in data.get(feature, []) if n != account]

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


if __name__ == "__main__":
	args = sys.argv[1:]

	if not args:
		print(describe())
	elif len(args) == 3 and args[0] in ("on", "off"):
		try:
			switch(args[1], args[2], args[0] == "on")
		except ValueError as exc:
			print(exc)
			sys.exit(2)

		print(describe())
	else:
		print("usage: features.py [on|off <feature> <account or *>]")
		sys.exit(2)
