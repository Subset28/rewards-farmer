"""Run an account's browser inside that account's own VPN namespace.

vpn_config.py builds one network namespace per account, each with its own tunnel
and its own kill switch, and writes where they are to a state file. When that file
exists, an account is not worked in this process: its run is started as a child
inside the account's namespace, so the only route out is that account's tunnel.
When the file does not exist (no VPN, a laptop, the tests) nothing changes and the
account is worked in-process as before.

An account whose tunnel is down, or that has no namespace at all, is never run:
falling back to the real connection is exactly what this exists to prevent.
"""

import json
import logging
import os
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

STATE_FILE = Path(os.environ.get("VPN_STATE_FILE", "/run/vpn/namespaces.json"))

# Set in the child, which is already inside its namespace and must not start another.
CHILD_ENV = "REWARDS_ISOLATED"

# Set by the supervisor for the schedulers it starts. Once it is set, a missing or
# unreadable state file means "no account may run", not "no VPN": without it a
# deleted file would quietly send every account out on the real connection.
REQUIRED_ENV = "REWARDS_VPN_REQUIRED"

# The child is an ordinary run that has no business changing the firewall or the
# namespaces, so it is started without the capabilities that could.
DROPPED_CAPABILITIES = "-sys_admin,-net_admin,-net_raw"


def active() -> bool:
	"""Whether accounts are to be run in VPN namespaces."""
	return STATE_FILE.exists() or os.environ.get(REQUIRED_ENV) == "1"


def inside() -> bool:
	return os.environ.get(CHILD_ENV) == "1"


def read_state() -> dict:
	try:
		with open(STATE_FILE, encoding="utf-8") as handle:
			state = json.load(handle)
	except (OSError, ValueError):
		return {}

	return state if isinstance(state, dict) else {}


def command_for(account: str, script: str, entry: dict) -> list[str]:
	return [
		"nsenter", f"--net={entry['netns']}",
		"setpriv", f"--bounding-set={DROPPED_CAPABILITIES}", "--",
		sys.executable, script,
	]


def environment_for(account: str, entry: dict, base: dict | None = None) -> dict:
	env = dict(os.environ if base is None else base)
	env["REWARDS_ACCOUNTS"] = account
	env[CHILD_ENV] = "1"

	# A browser that reports the timezone of the place its address is in.
	if entry.get("timezone"):
		env["TZ"] = entry["timezone"]

	return env


def run(account: str, script: str, in_process, runner=subprocess.run) -> bool:
	"""Work one account and return whether it ran, in its namespace when there are namespaces.

	`in_process` does the work here and is used only when there is no VPN setup
	at all, or when this already is the child inside a namespace.
	"""
	if inside() or not active():
		return in_process()

	entry = read_state().get(account)

	if not isinstance(entry, dict) or not entry.get("netns"):
		logger.error("[VPN] %s has no namespace, so it is not run. It is never run on the real connection.", account)

		return False

	if not entry.get("up"):
		logger.error("[VPN] %s: its tunnel is down (%s), so this run is skipped.", account, entry.get("reason") or "restarting")

		return False

	logger.info("[VPN] %s: running inside its own namespace (exit %s)", account, entry.get("exit") or "?")

	result = runner(command_for(account, script, entry), env=environment_for(account, entry), check=False)

	return result.returncode == 0
