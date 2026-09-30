"""Starting Edge for an account, and saying why it did not start.

Shared by main.py and check_selectors.py so both launch the same way and both
explain a failure instead of dumping a traceback.
"""

import logging
import os

from selenium import webdriver
from selenium.common.exceptions import NoSuchDriverException, WebDriverException

import accounts
import log_utils

HEADLESS = os.environ.get("REWARDS_HEADLESS", "").strip().lower() in ("1", "true", "yes")

# Unattended, but with a real (virtual) display for Edge to draw on instead of
# --headless=new. Microsoft does not credit the "Explore on Bing" cards to a
# headless browser: the same click-and-search that completed them in a headed
# Edge left them on "Activated" every time in headless one. Only takes effect
# together with HEADLESS, which keeps meaning "nothing is watching" everywhere
# else (no prompt at exit, no cursor overlay), and expects DISPLAY to be set,
# e.g. by wrapping the process in with-xvfb.sh.
VIRTUAL_DISPLAY = os.environ.get("REWARDS_VIRTUAL_DISPLAY", "").strip().lower() in ("1", "true", "yes")

logger = logging.getLogger(__name__)

HIDE_DRIVER_MARKERS_JS = (
	"for (const k of Object.getOwnPropertyNames(window)) {"
	" if (/^cdc_/.test(k)) { try { delete window[k]; } catch (e) {} } }"
)

# Matched in order against the driver's message, lowercased. selenium reports
# nearly every one of these as SessionNotCreatedException, so the class alone
# says nothing about which it was.
EXPLANATIONS = [
	("chrome instance exited", [
		"Edge exited during startup, before the driver could connect to it.",
		"Common causes: this profile is open in another Edge window, a lock left",
		"behind by a browser that was killed, or a profile directory Edge cannot",
		"write to. Set REWARDS_DRIVER_LOG=msedgedriver.log and run again; that",
		"log has Edge's own reason.",
	]),
	("cannot create default profile directory", [
		"Edge could not create the profile directory. Check that the current user",
		"can write to it without administrator rights.",
	]),
	("still attached to a running", [
		"This profile is already open in another Edge window, including one left",
		"over from a previous run or held by a container. Close it and try again.",
	]),
	("only supports microsoft edge version", [
		"msedgedriver and Edge versions do not match. Update msedgedriver to your",
		"Edge version, or remove the old one from PATH / MSEDGEDRIVER_PATH.",
	]),
]


def build_options(account: accounts.Account) -> webdriver.EdgeOptions:
	options = webdriver.EdgeOptions()

	options.add_experimental_option("excludeSwitches", ["enable-automation"])
	options.add_experimental_option("useAutomationExtension", False)
	options.add_argument("--disable-blink-features=AutomationControlled")
	options.add_argument(f"--user-data-dir={account.user_data_dir}")
	options.add_argument(f"--profile-directory={account.profile_name}")

	# Needed to register a script that runs in every tab, including the ones a
	# card opens; see hide_driver_markers.
	options.enable_bidi = True

	if os.environ.get("EDGE_BINARY"):
		options.binary_location = os.environ["EDGE_BINARY"]

	if HEADLESS:
		# A container has no display. The window size is set explicitly because
		# the pointer code works in viewport coordinates, and the default
		# headless window is small enough to put cards out of reach.
		if VIRTUAL_DISPLAY:
			# A virtual display has no GPU, and without these Edge exposes no
			# WebGL context at all, which almost no real browser lacks. Software
			# WebGL is present but reports SwiftShader as the renderer.
			options.add_argument("--use-gl=angle")
			options.add_argument("--use-angle=swiftshader")
			options.add_argument("--enable-unsafe-swiftshader")
			options.add_argument("--ignore-gpu-blocklist")
		else:
			options.add_argument("--headless=new")

		options.add_argument("--window-size=1920,1080")
		options.add_argument("--no-sandbox")
		options.add_argument("--disable-dev-shm-usage")

	return options


def build_service() -> webdriver.EdgeService:
	driver_log = os.environ.get("REWARDS_DRIVER_LOG")

	# An explicit driver path skips Selenium Manager entirely, which is also
	# what fails in #79 when it cannot locate the Edge install.
	return webdriver.EdgeService(
		executable_path=os.environ.get("MSEDGEDRIVER_PATH") or None,
		service_args=["--verbose"] if driver_log else None,
		log_output=driver_log or None,
	)


def explain(exc: Exception) -> list[str]:
	if isinstance(exc, NoSuchDriverException):
		return [
			"selenium could not find msedgedriver or Edge on this machine.",
			"Set MSEDGEDRIVER_PATH to the full path of msedgedriver, and EDGE_BINARY",
			"to msedge if Edge is installed somewhere non-standard.",
		]

	message = str(exc).lower()

	for needle, lines in EXPLANATIONS:
		if needle in message:
			return lines

	return ["The driver's message is below; it did not match a known cause."]


def hide_driver_markers(driver, account_name: str) -> None:
	"""Remove the cdc_ properties msedgedriver puts on every page's window.

	They are the classic Selenium tell: BrowserScan's Webdriver check reads them
	and calls the browser a robot, while navigator.webdriver, which
	AutomationControlled already masks, was never what it looked at. A preload
	script registered over BiDi runs before a page's own scripts in every tab,
	unlike a per-tab CDP script, which the tabs a card opens would not have.
	Needs both halves, found by testing on a throwaway profile: the BiDi script
	only reaches tabs opened after it is registered, so the tab the browser
	starts with, where the bot begins and reaches the Rewards pages, stayed
	"Robot" until the CDP script below was added for it. A new tab reads Normal
	with the first half alone.

	Best effort on purpose, and loud: a run that cannot hide them is still
	worth doing, but must not look as though it did.
	"""
	for label, register in (
		("every new tab", lambda: driver.script.pin(HIDE_DRIVER_MARKERS_JS)),
		("the starting tab", lambda: driver.execute_cdp_cmd(
			"Page.addScriptToEvaluateOnNewDocument", {"source": HIDE_DRIVER_MARKERS_JS}
		)),
	):
		try:
			register()
		except Exception as exc:
			logger.warning(
				"%s: could not hide the driver's cdc_ window properties for %s, so "
				"this run is easier to spot as automated: %s",
				account_name,
				label,
				log_utils.exception_summary(exc),
			)


def start_driver(account: accounts.Account):
	"""An Edge driver for the account, or None after logging why it failed."""
	try:
		driver = webdriver.Edge(options=build_options(account), service=build_service())
		hide_driver_markers(driver, account.name)

		return driver
	except WebDriverException as exc:
		logger.error("[FAIL] %s: could not start Edge with this profile.", account.name)
		logger.error("       profile directory: %s", account.user_data_dir)

		for line in explain(exc):
			logger.error("       %s", line)

		logger.error("       driver said: %s", log_utils.exception_summary(exc))

		return None
