"""Checks for REWARDS_ACCOUNTS.

Five layers, cheapest first:

    1. which accounts a configuration produces
    2. the flags each one hands Edge
    3. the run loop's ordering, skip-on-failure and exit codes
    4. one account failing every way it can, without ending the batch
    5. two real Edge profiles holding two independent, persistent identities

Layers 1 to 4 are pure and need nothing installed. Layer 4 drives the real
run loop with a stand-in for the browser, so the code under test is the
shipped one and only selenium is replaced. Layer 5 starts Edge twice and
reaches bing.com, so it is opt in:

    python tests/test_multi_account.py            # layers 1-4
    python tests/test_multi_account.py --browser  # all five

Layer 5 is the one that answers "does multi-account work". Two profiles must
end up with two different identities, and each must keep its own across a
restart, because that is what a per-account sign-in is made of. It uses
bing.com's own MUID cookie rather than an injected one: a cookie added through
webdriver is not written to the profile the way a Set-Cookie is, so it proves
nothing about a sign-in surviving.
"""

import logging
import os
import shutil
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

FAILURES = []


def check(label, got, want):
	if got == want:
		print(f"  ok    {label}")

		return True

	print(f"  FAIL  {label}\n          got  {got!r}\n          want {want!r}")
	FAILURES.append(label)

	return False


def accounts_for(value):
	"""configured() under a given REWARDS_ACCOUNTS, or ValueError."""
	if value is None:
		os.environ.pop(accounts.ENV_VAR, None)
	else:
		os.environ[accounts.ENV_VAR] = value

	return accounts.configured()


# --------------------------------------------------------------------------
# 1. which accounts a configuration produces
# --------------------------------------------------------------------------

def test_configuration():
	print("\n[1] account configuration")

	default = accounts_for(None)
	check("unset gives one account", [a.name for a in default], ["default"])
	check("unset uses the existing profile directory", default[0].user_data_dir, USER_DATA_DIR)
	check("unset is the default profile", default[0].is_default, True)

	check("two names, in order", [a.name for a in accounts_for("personal,spare")], ["personal", "spare"])
	check("surrounding whitespace ignored", [a.name for a in accounts_for(" personal , spare ")], ["personal", "spare"])
	check("empty entries dropped", [a.name for a in accounts_for("personal,,spare,")], ["personal", "spare"])
	check("blank value falls back to default", [a.name for a in accounts_for("   ")], ["default"])
	check("duplicates collapse, case insensitively", [a.name for a in accounts_for("personal,PERSONAL,spare")], ["personal", "spare"])

	# Names become directory names. Anything that resolves outside the profile
	# directory, or onto a directory another entry already owns, has to be
	# refused rather than quietly writing somewhere else.
	refused = [
		# relative traversal
		"..", ".", "../escape", "..\\escape", "a/b", "a\\b",
		# absolute, drive-relative and UNC
		"/etc", "\\", "/", "C:", "C:\\Windows", "\\\\server\\share",
		# expanded elsewhere, not here
		"~", "%TEMP%", "$HOME",
		# Win32 strips trailing dots, so these are not the directories they read
		# as: "personal." is "personal", and "..." is data-dir itself
		"...", "....", "personal.", "personal..",
		# shell and filesystem metacharacters
		"a b", "a:b", "a;b", "a|b", "a*b", "a?b", "a<b",
	]

	for name in refused:
		try:
			accounts_for(f"good,{name}")
			rejected = False
		except ValueError:
			rejected = True

		check(f"refused as a directory name: {name!r}", rejected, True)

	# The leading dot is fine, it is only the trailing one that moves.
	check("a leading dot is still a usable name", [a.name for a in accounts_for(".hidden")], [".hidden"])

	named = accounts_for("personal,spare")
	check("one directory per account", len({a.user_data_dir for a in named}), 2)

	# Distinct strings are not enough. Two names can spell one directory, which
	# is what the trailing dot did, so compare where the paths actually land.
	check(
		"one directory per account after the filesystem resolves them",
		len({os.path.realpath(a.user_data_dir) for a in named}), 2
	)

	root = os.path.realpath(USER_DATA_DIR)
	inside = all(
		os.path.commonpath([root, os.path.realpath(a.user_data_dir)]) == root
		and os.path.realpath(a.user_data_dir) != root
		for a in named
	)
	check("every directory sits under the profile directory", inside, True)
	check("named accounts are not the default profile", [a.is_default for a in named], [False, False])


# --------------------------------------------------------------------------
# 2. the flags each one hands Edge
# --------------------------------------------------------------------------

def test_options():
	print("\n[2] the flags Edge is handed")

	seen = []

	for account in accounts_for("personal,spare"):
		args = main.build_options(account).arguments
		user_data = [a for a in args if a.startswith("--user-data-dir=")]
		profile = [a for a in args if a.startswith("--profile-directory=")]

		check(f"{account.name}: exactly one --user-data-dir", len(user_data), 1)
		check(f"{account.name}: exactly one --profile-directory", len(profile), 1)
		seen.append(user_data[0])

	check("the two profiles differ on the command line", len(set(seen)), 2)


# --------------------------------------------------------------------------
# 3. the run loop
# --------------------------------------------------------------------------

def test_run_loop():
	print("\n[3] the run loop")

	accounts_for("personal,spare")
	os.environ["REWARDS_HEADLESS"] = "1"
	main.HEADLESS = True  # so main() does not wait on input()

	real = main.run_account
	calls = []

	try:
		# The first profile fails to start; the second must still run.
		main.run_account = lambda a: (calls.append(a.name), a.name != "personal")[1]
		code = main.main()

		check("every account attempted, in order", calls, ["personal", "spare"])
		check("a profile that fails to start does not end the run", len(calls), 2)
		check("exit code 0 while at least one ran", code, 0)

		calls.clear()
		main.run_account = lambda a: (calls.append(a.name), False)[1]
		check("exit code 1 when none ran", main.main(), 1)

		calls.clear()
		main.run_account = lambda a: (calls.append(a.name), True)[1]
		accounts_for(None)
		check("unset still runs the single profile", (main.main(), calls), (0, ["default"]))
	finally:
		main.run_account = real


# --------------------------------------------------------------------------
# 4. one account failing, without ending the batch
# --------------------------------------------------------------------------

def failing_run(fail_at, exc):
	"""Three accounts through the real run loop, with the middle one failing.

	Only selenium is replaced. run_account and main() are the shipped ones, so
	what is measured is where their exception handling actually reaches.
	"""
	started, quit_cleanly = [], []

	def account_of(options):
		flag = next(a for a in options.arguments if a.startswith("--user-data-dir="))

		return os.path.basename(flag.split("=", 1)[1])

	class Driver:
		def __init__(self, options):
			self.name = account_of(options)
			started.append(self.name)

			if self.name == "two" and fail_at == "start":
				raise exc

		def quit(self):
			if self.name == "two" and fail_at == "quit":
				raise exc

			quit_cleanly.append(self.name)

	class Tasks:
		def __init__(self, driver):
			self.driver = driver

			if driver.name == "two" and fail_at == "connect":
				raise exc

		def complete_all_tasks(self):
			if self.driver.name == "two" and fail_at == "tasks":
				raise exc

	real_edge, real_tasks = main.webdriver.Edge, main.rewards_tasks.RewardsTaskUtils
	main.webdriver.Edge, main.rewards_tasks.RewardsTaskUtils = Driver, Tasks

	try:
		accounts_for("one,two,three")
		outcome = main.main()
	except BaseException as raised:  # noqa: BLE001 - the point is to notice it
		outcome = f"raised {type(raised).__name__}"
	finally:
		main.webdriver.Edge, main.rewards_tasks.RewardsTaskUtils = real_edge, real_tasks

	return started, quit_cleanly, outcome


def test_failure_isolation():
	print("\n[4] one account failing, without ending the batch")

	os.environ["REWARDS_HEADLESS"] = "1"
	main.HEADLESS = True

	# Every way the middle account can go wrong. Only the first of these was
	# handled by name; the rest reached main() and took account three with them.
	from selenium.common.exceptions import (
		NoSuchDriverException,
		SessionNotCreatedException,
		WebDriverException,
	)

	cases = [
		("the profile is already open", "start", SessionNotCreatedException("profile in use")),
		("the driver will not start", "start", WebDriverException("browser and driver version mismatch")),
		("there is no driver installed", "start", NoSuchDriverException("msedgedriver not found")),
		("the profile directory is unwritable", "start", PermissionError(13, "Permission denied")),
		("the first page never loads", "connect", WebDriverException("net::ERR_NAME_NOT_RESOLVED")),
		("the browser dies mid-run", "tasks", WebDriverException("chrome not reachable")),
		("the browser will not shut down", "quit", WebDriverException("browser already gone")),
	]

	# The failure paths log at error level by design; the checks below are what
	# reports the outcome, so keep the output readable.
	logging.disable(logging.CRITICAL)

	try:
		for label, fail_at, exc in cases:
			started, quit_cleanly, outcome = failing_run(fail_at, exc)

			check(f"{label}: the other accounts still run", started, ["one", "two", "three"])
			check(f"{label}: main() still returns an exit code", outcome, 0)

			# Whatever went wrong, the browsers that did start have to be shut
			# down, or their profiles stay locked against the next run.
			expected_quit = ["one", "three"] if fail_at in ("start", "quit") else ["one", "two", "three"]
			check(f"{label}: every started browser was closed", quit_cleanly, expected_quit)
	finally:
		logging.disable(logging.NOTSET)

	# A lone account is the unset, unchanged path. Its failure has nothing left
	# to protect, so it must still come out as a failing exit code rather than
	# being swallowed into a success.
	accounts_for(None)
	real = main.run_account
	logging.disable(logging.CRITICAL)

	try:
		def boom(_):
			raise WebDriverException("chrome not reachable")

		main.run_account = boom
		check("a lone account's failure is still exit code 1", main.main(), 1)
	finally:
		main.run_account = real
		logging.disable(logging.NOTSET)


# --------------------------------------------------------------------------
# 5. two real profiles, two independent identities
# --------------------------------------------------------------------------

def identity_of(account):
	"""bing.com's MUID for this profile: server set, and persistent."""
	from selenium import webdriver

	driver = webdriver.Edge(options=main.build_options(account))

	try:
		driver.get("https://www.bing.com")
		cookie = driver.get_cookie("MUID")

		return cookie["value"] if cookie else None
	finally:
		driver.quit()


def test_real_profiles():
	print("\n[5] two real Edge profiles")

	first, second = accounts_for("mat_a,mat_b")

	try:
		a_before = identity_of(first)
		b_before = identity_of(second)

		check("first profile gets an identity", a_before is not None, True)
		check("second profile gets an identity", b_before is not None, True)
		check("the two profiles are different identities", a_before != b_before, True)

		check("first profile keeps its identity across a restart", identity_of(first), a_before)
		check("second profile keeps its identity across a restart", identity_of(second), b_before)
		check("and they are still distinct", identity_of(first) != identity_of(second), True)
		check("both directories exist", [os.path.isdir(a.user_data_dir) for a in (first, second)], [True, True])
		check(
			"and they are two directories, not one",
			len({os.path.realpath(a.user_data_dir) for a in (first, second)}), 2
		)
	finally:
		for account in (first, second):
			shutil.rmtree(account.user_data_dir, ignore_errors=True)


if __name__ == "__main__":
	import accounts
	import main
	from constants import USER_DATA_DIR

	test_configuration()
	test_options()
	test_run_loop()
	test_failure_isolation()

	if "--browser" in sys.argv:
		test_real_profiles()
	else:
		print("\n[5] skipped, pass --browser to start Edge")

	print("\n" + (f"{len(FAILURES)} failed: " + "; ".join(FAILURES) if FAILURES else "all checks passed"))
	sys.exit(1 if FAILURES else 0)
