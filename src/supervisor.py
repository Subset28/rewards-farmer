"""Runs several commands in one container and stops when any of them ends, so Docker restarts the container.

    python src/supervisor.py -- env REWARDS_ACCOUNTS=default python src/daily_loop.py -- env REWARDS_ACCOUNTS=second python src/daily_loop.py

The home-connection accounts used to have a container for each scheduler (a daily one and a search one per
account). They are all the same program with different settings, so one container runs them side by side. Each
account still has its own loop, its own settings, its own log and its own schedule; the run lock in data-dir
keeps their browsers strictly one at a time.

The VPN container is separate on purpose (vpn_config.py): it holds extra network rights and a firewall for the
account behind the VPN, and a VPN outage must not stop the accounts that are not behind it.

A command that ends, for any reason, ends the lot: a loop that has died is not something to carry on without.
The exception is a command whose first word is `optional` (the control interface): it is started again after a pause
when it ends, and never takes the others down.
SIGTERM (docker stop) is passed on to every command and waited for.
"""

import signal
import subprocess
import sys
import time

POLL_SECONDS = 2.0
OPTIONAL = "optional"
RESTART_AFTER_SECONDS = 60.0
STOP_GRACE_SECONDS = 20.0


def split(argv: list[str]) -> list[list[str]]:
	"""The commands in `argv`, which are separated by `--`. Empty parts are dropped."""
	commands, current = [], []

	for part in argv:
		if part == "--":
			if current:
				commands.append(current)

			current = []
		else:
			current.append(part)

	if current:
		commands.append(current)

	return commands


def stop_all(running: list, grace: float = STOP_GRACE_SECONDS, sleep=time.sleep, clock=time.monotonic) -> None:
	"""Ask every command to stop, then force the ones that are still there after `grace` seconds."""
	for process in running:
		if process.poll() is None:
			process.terminate()

	deadline = clock() + grace

	while clock() < deadline and any(p.poll() is None for p in running):
		sleep(0.2)

	for process in running:
		if process.poll() is None:
			process.kill()


def run(commands: list[list[str]], popen=subprocess.Popen, sleep=time.sleep, handle_signals: bool = True, clock=time.monotonic) -> int:
	"""Start every command; return the exit code of the first required one to end (or 128 + the signal when told to stop)."""
	if not commands:
		print("supervisor: nothing to run", file=sys.stderr)

		return 2

	optional = [c[1:] for c in commands if c[0] == OPTIONAL and len(c) > 1]
	required = [c for c in commands if c[0] != OPTIONAL]

	if not required:
		print("supervisor: nothing required to run", file=sys.stderr)

		return 2

	running = [popen(command) for command in required]
	extras = [{"command": command, "process": popen(command), "again": None} for command in optional]
	stopping: list[int] = []

	if handle_signals:
		def on_signal(signum, frame):
			stopping.append(signum)

		signal.signal(signal.SIGTERM, on_signal)
		signal.signal(signal.SIGINT, on_signal)

	while True:
		everything = running + [e["process"] for e in extras]

		for process in running:
			code = process.poll()

			if code is not None:
				print(f"supervisor: {process.args if hasattr(process, 'args') else 'a command'} ended with {code}; stopping the rest", file=sys.stderr)
				stop_all(everything)

				return code or 1

		for extra in extras:
			if extra["process"].poll() is None:
				continue

			if extra["again"] is None:
				print(f"supervisor: optional {extra['command'][-1]} ended; trying again in {RESTART_AFTER_SECONDS:.0f}s", file=sys.stderr)
				extra["again"] = clock() + RESTART_AFTER_SECONDS
			elif clock() >= extra["again"]:
				extra["process"] = popen(extra["command"])
				extra["again"] = None

		if stopping:
			stop_all(everything)

			return 128 + stopping[0]

		sleep(POLL_SECONDS)


def main(argv: list[str]) -> int:
	return run(split(argv))


if __name__ == "__main__":
	sys.exit(main(sys.argv[1:]))
