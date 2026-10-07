"""Tells a person, in Discord, when the bot needs them, and says what to do about it.

The bot looks after itself. This is for the few things it cannot fix: it checks the journal,
the points record and the brake, and when something is wrong it sends one message to that
account's channel that says WHO has to act and gives the exact words to use:

    [NEEDS CLAUDE]  the bot itself is broken or stuck; open Claude Code in the project and say what the message says
    [NEEDS YOU]     only a person can do it (a sign-in or a verification prompt)

What it looks for, per account:

  * the brake has been on for a long while (needs you: finish the verification, then clear it)
  * two or more failed runs in the last day (needs Claude)
  * no points gained for two days although the account ran (needs Claude)
  * no run of any kind for a day and a half (needs Claude: the scheduler is stuck or gone)

Each problem is reported once a day at most while it lasts, and again if it comes back after
clearing. It never raises: a health check must not be the thing that breaks a run.
"""

import json
import logging
import os
from datetime import datetime, timedelta

import clock
import journal
import notify
import points_log
import safety
from constants import USER_DATA_DIR

logger = logging.getLogger(__name__)

STATE_FILE = os.path.join(USER_DATA_DIR, "health.json")

NEEDS_CLAUDE = "NEEDS CLAUDE"
NEEDS_YOU = "NEEDS YOU"

REPEAT_AFTER = timedelta(hours=24)
PAUSE_REMINDER_AFTER = timedelta(hours=12)
FAILURES_TO_REPORT = 2
STALLED_AFTER = timedelta(hours=44)
SILENT_AFTER = timedelta(hours=36)

# Exit codes that are not a fault: 3 is the brake (it has already said so), 4 is a busy profile.
NOT_FAULTS = (3, 4)


class Finding:
	def __init__(self, account: str, key: str, needs: str, summary: str, detail: str, say: str):
		self.account = account
		self.key = key
		self.needs = needs
		self.summary = summary
		self.detail = detail
		self.say = say

	@property
	def title(self) -> str:
		return f"[{self.needs}] {self.account}: {self.summary}"

	@property
	def message(self) -> str:
		if self.needs == NEEDS_CLAUDE:
			return f"{self.detail}\nOpen Claude Code in the rewards-farmer folder and say: \"{self.say}\""

		return f"{self.detail}\n{self.say}"


def _belongs(owner, account: str) -> bool:
	return account.lower() in [part.strip().lower() for part in str(owner or "").split(",")]


def _parse(text: str) -> datetime | None:
	try:
		return datetime.fromisoformat(str(text).replace(" ", "T"))
	except ValueError:
		return None


def _paused(account: str, now: datetime) -> list[Finding]:
	record = safety.paused_for(account)

	if not record:
		return []

	since = _parse(record.get("time", ""))

	if since is None or now - since < PAUSE_REMINDER_AFTER:
		return []

	hours = int((now - since).total_seconds() // 3600)

	return [Finding(
		account, "paused", NEEDS_YOU, f"still paused after {hours} hours",
		f"The brake stopped this account ({record.get('kind', 'unknown')}: {record.get('reason', '')}) and nothing has cleared it.",
		f"Finish the verification in the sign-in browser, then run: python src/safety.py clear {account}. If you are not sure how, open Claude Code and say \"{account} is paused, help me clear it\".",
	)]


def _failures(account: str, rows: list[dict]) -> list[Finding]:
	failed = [
		r for r in rows
		if r.get("event") == "end" and r.get("outcome") == "failed"
		and r.get("exit_code") not in NOT_FAULTS and _belongs(r.get("owner"), account)
	]

	if len(failed) < FAILURES_TO_REPORT:
		return []

	last = failed[-1]

	return [Finding(
		account, "failed_runs", NEEDS_CLAUDE, f"{len(failed)} failed runs in the last day",
		f"The last one ({last.get('kind')}) ended with exit code {last.get('exit_code')} at {str(last.get('t', ''))[11:16]}.",
		f"{account} had {len(failed)} failed runs, check the bot",
	)]


def _stalled(account: str, readings: list[dict], now: datetime) -> list[Finding]:
	if not readings:
		return []

	latest = readings[-1]
	seen = _parse(latest.get("time", ""))

	if seen is None or now - seen > STALLED_AFTER:
		return []

	older = [r for r in readings[:-1] if (t := _parse(r.get("time", ""))) is not None and seen - t >= STALLED_AFTER]

	if not older:
		return []

	before = older[-1]

	try:
		gained = int(latest["lifetime"]) - int(before["lifetime"])
	except (KeyError, TypeError, ValueError):
		return []

	if gained > 0:
		return []

	return [Finding(
		account, "stalled", NEEDS_CLAUDE, "no points gained for two days",
		f"Lifetime points are {latest['lifetime']}, the same as at {before['time'][:16]}, although the account has been running.",
		f"{account} has earned no points for two days, check the bot",
	)]


def _silent(account: str, rows: list[dict], now: datetime, paused: bool) -> list[Finding]:
	if paused:
		return []

	mine = [r for r in rows if _belongs(r.get("owner"), account)]

	if not mine:
		return []

	last = _parse(mine[-1]["t"])

	if last is None or now - last < SILENT_AFTER:
		return []

	hours = int((now - last).total_seconds() // 3600)

	return [Finding(
		account, "silent", NEEDS_CLAUDE, f"no run for {hours} hours",
		f"The last thing recorded for this account was at {mine[-1]['t'][:16]}. The scheduler may be stuck or stopped.",
		f"{account} has not run for {hours} hours, check the bot",
	)]


def findings(names: list[str], now: datetime | None = None) -> list[Finding]:
	"""Everything wrong right now, for these accounts."""
	now = now or clock.now()
	week = journal.events(days=8, now=now)
	today = journal.events(days=2, now=now)
	found: list[Finding] = []

	for name in names:
		held = safety.paused_for(name) is not None
		found += _paused(name, now)
		found += _failures(name, [r for r in today if (_parse(r["t"]) or now) >= now - timedelta(days=1)])
		found += _stalled(name, points_log.history(name), now)
		found += _silent(name, week, now, held)

	return found


def _read_state() -> dict:
	try:
		with open(STATE_FILE, encoding="utf-8") as handle:
			state = json.load(handle)
	except (OSError, ValueError):
		return {}

	return state if isinstance(state, dict) else {}


def _write_state(state: dict) -> None:
	temporary = f"{STATE_FILE}.{os.getpid()}.tmp"

	try:
		os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)

		with open(temporary, "w", encoding="utf-8") as handle:
			json.dump(state, handle, indent=1)

		os.replace(temporary, STATE_FILE)
	except OSError as exc:
		logger.debug("Could not save the health state: %s", exc)

		try:
			os.unlink(temporary)
		except OSError:
			pass


def check(names: list[str] | None = None, now: datetime | None = None, send=notify.send) -> list[Finding]:
	"""Look for problems and tell the person about any new ones. Returns what was sent."""
	try:
		now = now or clock.now()

		if names is None:
			import status

			names = status.known_names()

		state = _read_state()
		current = findings(names, now)
		live = {f"{f.account}|{f.key}" for f in current}
		sent = []

		for finding in current:
			key = f"{finding.account}|{finding.key}"
			last = _parse(state.get(key, ""))

			if last is not None and now - last < REPEAT_AFTER:
				continue

			send(finding.title, finding.message, priority="high", account=finding.account)
			state[key] = now.isoformat(timespec="seconds")
			sent.append(finding)
			logger.warning("[HEALTH] %s", finding.title)

		# A problem that has cleared is forgotten, so it is reported again if it returns.
		state = {k: v for k, v in state.items() if k in live}
		_write_state(state)

		return sent
	except Exception as exc:  # pragma: no cover - a health check must never break a run
		logger.debug("Health check failed: %s", exc)

		return []


def main() -> None:
	import status

	found = findings(status.known_names())

	if not found:
		print("nothing needs anyone")

	for finding in found:
		print(finding.title)
		print("  " + finding.message.replace("\n", "\n  "))


if __name__ == "__main__":
	main()
