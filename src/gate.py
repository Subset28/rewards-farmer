"""The gate: an account runs only with a recorded profile of how its owner types and moves.

Without one, an account types and moves the generic way, which is the way that can be told from a person.
So it does not run. It waits, and the owner is told once a day what to do:

    python src/calibrate.py <account>

A profile that was recorded but whose own check said the bot can be told from the person (a score above
MAX_SCORE, where 0.5 is "cannot tell") is held back too: record again, with more care or on another day.
A profile with no stored score (an older one, or one rebuilt from the raw recording) is let through.

    python src/gate.py            each account: let through, or why it waits
"""

import json
import logging
import os
import sys

import behavior
import clock
from constants import USER_DATA_DIR
import notify

logger = logging.getLogger(__name__)

MAX_SCORE = 0.70

# What a run exits with when the gate held every account back: not a failure (the owner has been told), and not retried.
EXIT_CODE = 5
ALERTS_FILE = os.path.join(USER_DATA_DIR, "gate_alerts.json")


def _stored_scores(name: str) -> list[float]:
	try:
		with open(behavior.profile_path(name), encoding="utf-8") as handle:
			notes = json.load(handle).get("notes") or {}
	except (OSError, ValueError, AttributeError):
		return []

	return [float(notes[key]) for key in ("typing_score", "mouse_score") if isinstance(notes.get(key), (int, float))]


def reason(name: str) -> str | None:
	"""Why this account must not run yet, or None when it may."""
	profile = behavior.load(name)

	if profile.source != "recorded":
		return f"{name} has no recorded profile of how its owner types and moves. Run: python src/calibrate.py {name}"

	worst = max(_stored_scores(name), default=0.0)

	if worst > MAX_SCORE:
		return (
			f"{name}'s recording can still be told from a person (score {worst:.2f}, needs {MAX_SCORE:.2f} or less; 0.5 is "
			f"indistinguishable). Record again: python src/calibrate.py {name}"
		)

	return None


def _alerted_today(name: str) -> bool:
	try:
		with open(ALERTS_FILE, encoding="utf-8") as handle:
			return json.load(handle).get(name) == clock.today().isoformat()
	except (OSError, ValueError, AttributeError):
		return False


def _remember_alert(name: str) -> None:
	try:
		with open(ALERTS_FILE, encoding="utf-8") as handle:
			data = json.load(handle)
	except (OSError, ValueError):
		data = {}

	data[name] = clock.today().isoformat()

	try:
		with open(ALERTS_FILE, "w", encoding="utf-8") as handle:
			json.dump(data, handle)
	except OSError as exc:
		logger.debug("Could not remember the gate alert: %s", exc)


def blocked(name: str, send=notify.send) -> str | None:
	"""The reason this account is held back (and the owner told, once a day), or None when it may run."""
	why = reason(name)

	if why is None:
		return None

	logger.error("[GATE] Not running %s: %s", name, why)

	if not _alerted_today(name):
		send("Calibration needed", why, priority="high", account=name)
		_remember_alert(name)

	return why


def main() -> int:
	import accounts

	held = 0

	for account in accounts.configured():
		why = reason(account.name)
		print(f"{account.name}: {'runs' if why is None else 'waits: ' + why}")
		held += why is not None

	return 1 if held else 0


if __name__ == "__main__":
	sys.exit(main())
