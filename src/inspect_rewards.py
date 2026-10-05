"""Read-only: save what each account's Rewards pages say, to find out what a page offers.

Opens the Rewards home, earn and dashboard pages of each account in turn, waits as a
person would, and saves the visible text of each to data-dir/inspect/<account>-<page>.txt.
It clicks nothing and claims nothing. It takes the same lock as a scheduled run, so it
never overlaps one, and it prints the lines that mention levels and bonuses.

    REWARDS_ACCOUNTS=default,second with-xvfb python src/inspect_rewards.py
"""

import logging
import os
import random
import re
import sys
import time

import accounts
import browser
import log_utils
import run_lock
from constants import USER_DATA_DIR

logger = logging.getLogger(__name__)

PAGES = (
	("home", "https://rewards.bing.com/"),
	("earn", "https://rewards.bing.com/earn"),
	("dashboard", "https://rewards.bing.com/dashboard"),
)

OUT_DIR = os.path.join(USER_DATA_DIR, "inspect")

INTERESTING = re.compile(r"bonus|level|gold|silver|member|monthly|claim|streak|star|default search|activities", re.I)


def page_text(driver) -> str:
	"""The visible text of the page, after scrolling so lazily drawn sections are there."""
	height = driver.execute_script("return document.body.scrollHeight") or 0

	for step in range(0, int(height), 700):
		driver.execute_script(f"window.scrollTo(0, {step})")
		time.sleep(random.uniform(0.4, 0.9))

	driver.execute_script("window.scrollTo(0, 0)")

	return driver.find_element("tag name", "body").text


def inspect(account: accounts.Account) -> None:
	driver = browser.start_driver(account)

	if driver is None:
		print(f"{account.name}: the browser did not start")

		return

	try:
		for name, url in PAGES:
			driver.get(url)
			time.sleep(random.uniform(5, 8))
			text = page_text(driver)
			os.makedirs(OUT_DIR, exist_ok=True)

			with open(os.path.join(OUT_DIR, f"{account.name}-{name}.txt"), "w", encoding="utf-8") as handle:
				handle.write(f"{driver.current_url}\n\n{text}\n")

			print(f"\n=== {account.name} / {name} ({driver.current_url}, {len(text)} characters)")

			for line in text.splitlines():
				if INTERESTING.search(line):
					print("   ", line.strip()[:160])
	finally:
		driver.quit()


def main() -> int:
	log_utils.setup_logging()

	for position, account in enumerate(accounts.configured()):
		if position:
			time.sleep(random.uniform(60, 180))

		inspect(account)

	return 0


if __name__ == "__main__":
	sys.exit(run_lock.run_locked(main))
