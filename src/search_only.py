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

HEADLESS = browser.HEADLESS

logger = logging.getLogger(__name__)


def run_account_searches(account: accounts.Account) -> bool:
	"""Run searches only for one account. Returns whether the browser started."""
	try:
		if not desktop_utils.prepare_desktop_before_launch():
			return False
		driver = browser.start_driver(account)
	finally:
		desktop_utils.switch_back_after_launch()

	if driver is None:
		return False

	try:
		rewards = rewards_tasks.RewardsTaskUtils(driver)
		rewards.complete_required_searches()
		logger.info("[OK] Required searches")
	except Exception as exc:
		tag, reason = rewards_tasks.task_failure_report(exc)
		logger.log(
			logging.WARNING if tag == "SKIP" else logging.ERROR,
			"[%s] Required searches: %s", tag, reason,
			exc_info=logger.isEnabledFor(logging.DEBUG)
		)
	finally:
		try:
			driver.quit()
		except Exception as exc:
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

	started = 0

	for account in configured:
		if len(configured) > 1:
			logger.info("=== account: %s ===", account.name)

		try:
			if run_account_searches(account):
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
		logger.info("%s/%s accounts ran searches", started, len(configured))

	if not HEADLESS:
		input("Press Enter to exit...")

	desktop_utils.cleanup_virtual_desktop()

	return 0 if started else 1


if __name__ == "__main__":
	if os.path.isfile(DOTENV_PATH): dotenv.load_dotenv(DOTENV_PATH)
	sys.exit(main())
