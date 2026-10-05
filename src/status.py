"""Everything worth knowing about right now, in one place.

    python src/status.py

For each account: what today holds (pacing), where its points stand and what the
monthly bonuses paid last month, and how its search quota stands today. Then the brake,
and today's runs. Read-only: it opens no browser and changes nothing.
"""

import logging

import accounts
import journal
import pacing
import points_log
import safety


def quota_line(name: str) -> str:
	"""Today's search quota for an account, from the last report the journal has."""
	rows = [r for r in journal.events(kind="search") if r["event"] == "quota" and r.get("account") == name]

	if not rows:
		return "no search report yet today"

	last = rows[-1]
	aim = f", aiming for {last['target']}" if last.get("target") is not None else ""
	state = "done for today" if last.get("complete") else "more to do"

	return f"searches {last.get('points', '?')}/{last.get('cap', '?')}{aim}: {state} (as of {last['t'][11:16]})"


def bonus_line(name: str) -> str:
	rows = points_log.history(name)
	last = rows[-1] if rows else {}
	found = {key: last[f"{key}_last_month"] for key, _ in points_log.BONUSES if f"{key}_last_month" in last}

	if not found:
		return "monthly bonuses: not read yet"

	return "bonuses last month: " + ", ".join(f"{key.replace('_', ' ')} {value}" for key, value in found.items())


def account_block(name: str) -> str:
	rows = points_log.history(name)
	points = points_log.summary(rows, points_log.target_for(name)) if rows else "no points reading yet"

	return "\n".join([
		name,
		f"  {pacing.describe(name)}",
		"  " + points.replace("\n", "\n  "),
		f"  {bonus_line(name)}",
		f"  {quota_line(name)}",
	])


def known_names() -> list[str]:
	"""The accounts this container is set up for, then any other with points on record.

	Each scheduler container is configured for only some of the accounts, but the data
	is shared, so a status run from any of them should show all of it.
	"""
	names = [a.name for a in accounts.configured()]

	for row in points_log.history():
		name = row.get("account")

		if name and name not in names:
			names.append(name)

	return names


def report() -> str:
	try:
		names = known_names()
	except ValueError as exc:
		return f"cannot read the accounts: {exc}"

	hold = safety.blocked(names)
	brake = f"PAUSED ({hold.get('kind')}: {hold.get('reason')})" if hold else "running normally"
	parts = [account_block(name) for name in names]
	parts.append(f"brake: {brake}")
	parts.append("today's runs:\n" + journal.describe())

	return "\n\n".join(parts)


if __name__ == "__main__":
	logging.disable(logging.CRITICAL)
	print(report())
