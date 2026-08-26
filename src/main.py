import os
import sys

import accounts
import rewards_tasks
import mouse_trajectory
import mimic_typing
from selenium import webdriver
from selenium.common.exceptions import SessionNotCreatedException

HEADLESS = os.environ.get("REWARDS_HEADLESS", "").strip().lower() in ("1", "true", "yes")


def build_options(account: accounts.Account) -> webdriver.EdgeOptions:
	options = webdriver.EdgeOptions()

	options.add_experimental_option("excludeSwitches", ["enable-automation"])
	options.add_experimental_option('useAutomationExtension', False)
	options.add_argument("--disable-blink-features=AutomationControlled")
	options.add_argument(f"--user-data-dir={account.user_data_dir}")
	options.add_argument(f"--profile-directory={account.profile_name}")

	if HEADLESS:
		# A container has no display. The window size is set explicitly because
		# the pointer code works in viewport coordinates, and the default
		# headless window is small enough to put cards out of reach.
		options.add_argument("--headless=new")
		options.add_argument("--window-size=1920,1080")
		options.add_argument("--no-sandbox")
		options.add_argument("--disable-dev-shm-usage")

	return options


def run_account(account: accounts.Account) -> bool:
	"""Work one account. Returns whether the browser started."""
	try:
		driver = webdriver.Edge(options=build_options(account))
	except SessionNotCreatedException as exc:
		# Chromium allows one process per user data directory. When the profile
		# is already open the driver's copy exits during startup, and selenium
		# reports it as the browser crashing with a message that names neither
		# the profile nor the other window.
		print(f"[FAIL] {account.name}: could not start Edge with this profile.")
		print(f"       profile directory: {account.user_data_dir}")
		print("       The usual cause is that this profile is already open in another")
		print("       Edge window, including one left over from a previous run.")
		print(f"       driver said: {str(exc).strip().splitlines()[0]}")

		return False

	try:
		mouse = mouse_trajectory.MouseUtils(driver)
		keyboard = mimic_typing.KeyboardUtils(driver)

		rewards = rewards_tasks.RewardsTaskUtils(driver)
		rewards.complete_all_tasks()
	finally:
		driver.quit()

	return True


def main() -> int:
	try:
		configured = accounts.configured()
	except ValueError as exc:
		print(f"[FAIL] {exc}")

		return 2

	started = 0

	for account in configured:
		if len(configured) > 1:
			print(f"\n=== account: {account.name} ===")

		if run_account(account):
			started += 1

	if len(configured) > 1:
		print(f"\n{started}/{len(configured)} accounts ran")

	# Nothing is watching a container, and stdin is not a terminal there.
	if not HEADLESS:
		input("Press Enter to exit...")

	return 0 if started else 1


if __name__ == "__main__":
	sys.exit(main())
