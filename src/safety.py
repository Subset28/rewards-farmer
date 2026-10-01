"""The brake: stop the bot when the account shows signs of trouble.

A run that finds a sign-in page, a human check, or a restriction notice must not
carry on clicking, and the schedulers must not start the next run into the same
page. Tripping the brake writes a PAUSED file in the data directory and sends an
alert. Everything that starts a run checks for the file first. It stays until a
person looks at the account and clears it:

    python src/safety.py status
    python src/safety.py clear

The brake is shared by every account on purpose. Accounts that run from one
connection are not independent, so a warning on one is a reason to stop all.
"""

import json
import logging
import os
import re
import sys
import time
from dataclasses import dataclass

from constants import USER_DATA_DIR
import notify

logger = logging.getLogger(__name__)

PAUSE_FILE = os.path.join(USER_DATA_DIR, "PAUSED")

SIGNED_OUT = "signed-out"
CHALLENGE = "challenge"
RESTRICTED = "restricted"

# Longest stretch of page text worth reading. Notices are near the top, and a
# bound keeps a huge page from costing anything.
MAX_TEXT = 6000


@dataclass(frozen=True)
class Risk:
	kind: str
	reason: str


class AccountAtRisk(RuntimeError):
	"""The page shows something a run must not push through."""

	def __init__(self, risk: Risk):
		super().__init__(f"{risk.kind}: {risk.reason}")
		self.risk = risk


# (kind, pattern) tried against the lowercased page text. Narrow on purpose: a
# false trip stops the bot and sends an alert, which is cheap, but a pattern
# loose enough to fire on an ordinary Rewards page would stop it every day.
TEXT_RULES = (
	(RESTRICTED, re.compile(r"unusual activity")),
	(RESTRICTED, re.compile(r"account (has been|is|was) (temporarily )?(suspended|locked|blocked)")),
	(RESTRICTED, re.compile(r"(has been|is|was) temporarily (restricted|suspended)")),
	(RESTRICTED, re.compile(r"restricted from (earning|redeeming)")),
	(RESTRICTED, re.compile(r"(no longer|not) eligible (to|for) (earn|the microsoft rewards)")),
	(CHALLENGE, re.compile(r"verify (that )?you('re| are) (a )?human")),
	(CHALLENGE, re.compile(r"prove (that )?you('re| are) (not a robot|human)")),
	(CHALLENGE, re.compile(r"are you a robot")),
	(CHALLENGE, re.compile(r"press and hold")),
	(CHALLENGE, re.compile(r"solve (the|this) puzzle")),
	(CHALLENGE, re.compile(r"help us protect your account")),
)

SIGN_IN_HOSTS = ("login.live.com", "login.microsoftonline.com", "login.microsoft.com")


def classify(url: str, text: str) -> Risk | None:
	"""What, if anything, a page shows that a run must stop for. Pure.

	URL first, since it is the less ambiguous signal: Microsoft sends a locked
	account to account.live.com/Abuse and a signed-out visitor to a login host or
	to the Rewards welcome page.
	"""
	lowered_url = (url or "").lower()

	if "account.live.com/abuse" in lowered_url:
		return Risk(RESTRICTED, "redirected to Microsoft's account-locked page")

	if "captcha" in lowered_url or "account.live.com/proofs" in lowered_url:
		return Risk(CHALLENGE, "redirected to a verification page")

	host = lowered_url.split("//", 1)[-1].split("/", 1)[0]

	if host in SIGN_IN_HOSTS or host.endswith(".login.live.com"):
		return Risk(SIGNED_OUT, "redirected to the Microsoft sign-in page")

	# A signed-out visit to rewards.bing.com is sent to /about (seen on a fresh
	# profile) or /welcome. The bot never goes to either on purpose.
	if host == "rewards.bing.com" and lowered_url.split("//", 1)[-1].split("/", 1)[-1].startswith(("about", "welcome")):
		return Risk(SIGNED_OUT, "Rewards is showing its signed-out page")

	body = (text or "")[:MAX_TEXT].lower()

	for kind, pattern in TEXT_RULES:
		match = pattern.search(body)

		if match:
			return Risk(kind, f"the page says {match.group(0)!r}")

	return None


def inspect(driver) -> Risk | None:
	"""Classify the page the driver is on. A browser that cannot be read is not a risk."""
	try:
		url = driver.current_url
		text = driver.execute_script("return document.body ? document.body.innerText : ''") or ""
	except Exception as exc:
		logger.debug("Could not read the page to check it: %s", type(exc).__name__)

		return None

	return classify(url, text)


def paused() -> dict | None:
	"""The pause record if the brake is on, else None."""
	try:
		with open(PAUSE_FILE, encoding="utf-8") as handle:
			return json.load(handle)
	except FileNotFoundError:
		return None
	except (OSError, ValueError):
		# Present but unreadable still means somebody, or something, stopped
		# the bot. Treat it as on rather than quietly carrying on.
		return {"kind": "unknown", "reason": "the PAUSED file exists but could not be read", "time": ""}


def trip(risk: Risk, account_name: str) -> None:
	"""Turn the brake on and say so. Idempotent: an existing pause is kept."""
	if paused():
		return

	record = {
		"kind": risk.kind,
		"reason": risk.reason,
		"account": account_name,
		"time": time.strftime("%Y-%m-%d %H:%M:%S"),
	}

	try:
		os.makedirs(os.path.dirname(PAUSE_FILE), exist_ok=True)

		with open(PAUSE_FILE, "w", encoding="utf-8") as handle:
			json.dump(record, handle)
	except OSError as exc:
		logger.error("Could not write the pause file: %s", exc)

	logger.error("[BRAKE] %s on %s: %s. Runs are paused until `python src/safety.py clear`.", risk.kind, account_name, risk.reason)

	notify.send(
		"Rewards bot paused",
		f"{account_name}: {risk.kind} - {risk.reason}. Look at the account, then run: python src/safety.py clear",
		priority="high",
	)


def clear() -> bool:
	"""Turn the brake off. Returns whether it was on."""
	try:
		os.remove(PAUSE_FILE)
	except FileNotFoundError:
		return False

	return True


def guard(driver, account_name: str) -> None:
	"""Raise AccountAtRisk, after tripping the brake, if the page needs a human."""
	risk = inspect(driver)

	if risk:
		trip(risk, account_name)

		raise AccountAtRisk(risk)


def main(argv: list[str]) -> int:
	command = argv[1] if len(argv) > 1 else "status"
	record = paused()

	if command == "status":
		print("PAUSED: %s" % json.dumps(record) if record else "running normally")

		return 0

	if command == "clear":
		print("brake cleared" if clear() else "was not paused")

		return 0

	print("usage: safety.py [status|clear]")

	return 2


if __name__ == "__main__":
	sys.exit(main(sys.argv))
