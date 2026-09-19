from constants import DOTENV_PATH
import logging
import os
import sys
import dotenv
import log_utils
import accounts
import browser
import rewards_tasks

HEADLESS = browser.HEADLESS

logger = logging.getLogger(__name__)


def run_account(account: accounts.Account) -> bool:
	"""Work one account. Returns whether the browser started."""
	driver = browser.start_driver(account)

	if driver is None:
		return False

	try:
		rewards = rewards_tasks.RewardsTaskUtils(driver)
		rewards.complete_all_tasks()
	finally:
		try:
			driver.quit()
		except Exception as exc:
			# quit() raises when the browser is already gone. Letting it out
			# here would replace whatever actually went wrong with the tidy-up's
			# own error, and the process it is meant to end is dead anyway.
			logger.warning(
				"%s: the driver did not shut down cleanly: %s",
				account.name,
				log_utils.exception_summary(exc),
			)

	return True


def main() -> int:
	log_utils.setup_logging()

	try:
		configured = accounts.configured()
	except ValueError as exc:
		logger.error("[FAIL] %s", exc)

		return 2

	started = 0

	for account in configured:
		if len(configured) > 1:
			logger.info("=== account: %s ===", account.name)

		# One account must not be able to end the batch. complete_all_tasks
		# already contains a task that fails, and run_account names the profile
		# that is already open, but everything else - a driver that will not
		# start for some other reason, the browser dying mid-run, a page that
		# never loads - reached here and took the remaining accounts with it.
		# KeyboardInterrupt is deliberately not caught: Ctrl-C means stop.
		try:
			if run_account(account):
				started += 1
		except Exception as exc:
			logger.error(
				"[FAIL] %s: %s: %s",
				account.name,
				type(exc).__name__,
				log_utils.exception_summary(exc),
				exc_info=logger.isEnabledFor(logging.DEBUG),
			)

	if len(configured) > 1:
		logger.info("%s/%s accounts ran", started, len(configured))

	# Nothing is watching a container, and stdin is not a terminal there.
	if not HEADLESS:
		input("Press Enter to exit...")

	return 0 if started else 1


if __name__ == "__main__":
	if os.path.isfile(DOTENV_PATH): dotenv.load_dotenv(DOTENV_PATH)
	sys.exit(main())
