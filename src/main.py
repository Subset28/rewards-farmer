from constants import DOTENV_PATH
import logging
import os
import sys
import dotenv
import log_utils
import accounts
import browser
import desktop_utils
import rewards_tasks
import safety
import run_lock
import notify
import isolation
import points_log
import search_behavior
import time

HEADLESS = browser.HEADLESS

logger = logging.getLogger(__name__)


def run_account(account: accounts.Account) -> bool:
	"""Work one account. Returns whether the browser started."""
	try:
		if not desktop_utils.prepare_desktop_before_launch():
			return False
		driver = browser.start_driver(account)
	finally:
		desktop_utils.switch_back_after_launch()

	if driver is None:
		return False

	try:
		rewards = rewards_tasks.RewardsTaskUtils(driver, account.name)
		# Set when search_scheduler.py is running separately, so the once-a-day
		# run does not redo what is already being spread across the day.
		skip_searches = os.environ.get("REWARDS_SKIP_SEARCHES", "0") == "1"
		rewards.complete_all_tasks(skip_searches=skip_searches)

		try:
			reading = rewards.read_points_summary()

			if reading:
				points_log.record(account.name, reading)
				logger.info("Points: %s", reading)
				notify.send("Daily points", points_log.digest(account.name, reading), account=account.name)
		except Exception as exc:
			logger.warning("Could not record today's points: %s", log_utils.exception_summary(exc))
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
	desktop_utils.reset_virtual_desktop_state()

	if desktop_utils.is_virtual_desktop_enabled() and not desktop_utils.is_windows():
		logger.warning("USE_VIRTUAL_DESKTOP is enabled, but virtual desktops are only supported on Windows.")

	try:
		configured = accounts.configured()
	except ValueError as exc:
		logger.error("[FAIL] %s", exc)

		return 2

	hold = safety.blocked([a.name for a in configured])

	if hold:
		logger.error("[BRAKE] Not running: paused (%s: %s). Clear it with `python src/safety.py clear` once the account has been checked.", hold.get("kind"), hold.get("reason"))

		return 3

	started = 0

	for position, account in enumerate(configured):
		if position:
			# One account at a time with a different gap each time, never
			# back to back and never together.
			gap = search_behavior.account_gap_seconds(os.environ.get("REWARDS_ACCOUNT_GAP_MINUTES"))
			logger.info("Waiting %.0f minutes before the next account.", gap / 60)
			time.sleep(gap)

		one = safety.paused_for(account.name)

		if one:
			logger.error("[BRAKE] Skipping %s: paused (%s: %s). `python src/safety.py clear %s` once it is signed in again.", account.name, one.get("kind"), one.get("reason"), account.name)

			continue

		if len(configured) > 1:
			logger.info("=== account: %s ===", account.name)

		# One account must not be able to end the batch. complete_all_tasks
		# already contains a task that fails, and run_account names the profile
		# that is already open, but everything else - a driver that will not
		# start for some other reason, the browser dying mid-run, a page that
		# never loads - reached here and took the remaining accounts with it.
		# KeyboardInterrupt is deliberately not caught: Ctrl-C means stop.
		try:
			if isolation.run(account.name, "src/main.py", lambda: run_account(account)):
				started += 1
		except safety.AccountAtRisk as exc:
			# The brake is shared: a warning on one account stops the rest too.
			logger.error("[BRAKE] %s: %s. Stopping every account.", account.name, exc)

			break
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

	desktop_utils.cleanup_virtual_desktop()

	return 0 if started else 1


if __name__ == "__main__":
	if os.path.isfile(DOTENV_PATH): dotenv.load_dotenv(DOTENV_PATH)

	# Inside an account's VPN namespace the parent already holds the lock.
	sys.exit(main() if isolation.inside() else run_lock.run_locked(main))
