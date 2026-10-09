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
  * a task that used to work failed its last three runs in a row (needs Claude: usually a changed page)
  * points far below the account's normal for two days running (needs Claude)

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
import pacing
import points_log
import safety
import task_log
from constants import USER_DATA_DIR

logger = logging.getLogger(__name__)

STATE_FILE = os.path.join(USER_DATA_DIR, "health.json")

NEEDS_CLAUDE = "NEEDS CLAUDE"
NEEDS_YOU = "NEEDS YOU"

REPEAT_AFTER = timedelta(hours=24)
PAUSE_REMINDER_AFTER = timedelta(hours=12)
FAILURES_TO_REPORT = 2
TASK_FAILURES_TO_REPORT = 3     # runs in a row
TASK_WORKED_BEFORE = 3          # times it completed among the ten runs before those
LOW_DAY_SHARE = 0.4             # of the recent median
LOW_DAYS_IN_A_ROW = 2
MIN_TYPICAL_DAY = 60
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


def _tasks_broken(account: str, rows: list[dict]) -> list[Finding]:
	"""A task that used to work and has failed its last few runs in a row: a changed page."""
	found = []
	by_task: dict[str, list[dict]] = {}

	for row in rows:
		by_task.setdefault(row["task"], []).append(row)

	for task, runs in by_task.items():
		recent, before = runs[-TASK_FAILURES_TO_REPORT:], runs[-TASK_FAILURES_TO_REPORT - 10:-TASK_FAILURES_TO_REPORT]

		if len(recent) < TASK_FAILURES_TO_REPORT or any(r.get("completed") for r in recent):
			continue

		if sum(1 for r in before if r.get("completed")) < TASK_WORKED_BEFORE:
			continue

		tags = ", ".join(sorted({str(r.get("tag")) for r in recent}))
		found.append(Finding(
			account, f"task:{task}", NEEDS_CLAUDE, f"\"{task}\" has failed {TASK_FAILURES_TO_REPORT} runs in a row",
			f"It worked before and has now ended {tags} in each of its last {TASK_FAILURES_TO_REPORT} daily runs. Most often Microsoft changed that page.",
			f"{account}'s \"{task}\" task keeps failing, check the bot",
		))

	return found


def _low_days(account: str, readings: list[dict], now: datetime) -> list[Finding]:
	"""Points far below what the account normally makes, two days running. One low day can be a light day."""
	if pacing.in_ramp(account):
		return []

	by_day: dict[str, int] = {}

	for reading in readings:
		try:
			by_day[str(reading["time"])[:10]] = int(reading["today"])
		except (KeyError, TypeError, ValueError):
			continue

	days = sorted(by_day)

	if len(days) < LOW_DAYS_IN_A_ROW + 4:
		return []

	latest, earlier = days[-LOW_DAYS_IN_A_ROW:], days[-LOW_DAYS_IN_A_ROW - 7:-LOW_DAYS_IN_A_ROW]
	typical = sorted(by_day[d] for d in earlier)[len(earlier) // 2]

	if typical < MIN_TYPICAL_DAY or any(by_day[d] >= typical * LOW_DAY_SHARE for d in latest):
		return []

	return [Finding(
		account, "low_days", NEEDS_CLAUDE, "points far below normal for two days",
		f"The last two daily readings were {', '.join(str(by_day[d]) for d in latest)}; a normal day here is about {typical}.",
		f"{account}'s points have been far below normal, check the bot",
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
		found += _tasks_broken(name, task_log.history(name))
		found += _low_days(name, points_log.history(name), now)

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
		# Only for the accounts this check looked at: the loops of several accounts share this file, and one that
		# does not see an account must not erase what another has already told its owner.
		looked_at = set(names)
		state = {k: v for k, v in state.items() if k in live or k.split("|", 1)[0] not in looked_at}
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
