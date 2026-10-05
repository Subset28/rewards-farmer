"""The brake: stop the bot when the account shows signs of trouble.

A run that finds a sign-in page, a human check, or a restriction notice must not
carry on clicking, and the schedulers must not start the next run into the same
page. Tripping the brake writes a PAUSED file in the data directory and sends an
alert. Everything that starts a run checks for the file first. It stays until a
person looks at the account and clears it:

    python src/safety.py status
    python src/safety.py clear

A human check or a restriction notice stops every account, on purpose: accounts
that run from one connection are not independent, so a warning on one is a reason
to stop all. A plain sign-out is different. It means that one account's login
lapsed, says nothing about the others, and must not stop them, so it pauses only
the account it happened on (PAUSED.<account>).
"""

import glob
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


def _file_for(account: str | None) -> str:
	return PAUSE_FILE if account is None else f"{PAUSE_FILE}.{account}"


def _read(path: str) -> dict | None:
	try:
		with open(path, encoding="utf-8") as handle:
			return json.load(handle)
	except FileNotFoundError:
		return None
	except (OSError, ValueError):
		# Present but unreadable still means somebody, or something, stopped
		# the bot. Treat it as on rather than quietly carrying on.
		return {"kind": "unknown", "reason": f"{os.path.basename(path)} exists but could not be read", "time": ""}


def paused(accounts: list[str] | None = None) -> dict | None:
	"""The pause record if the whole brake is on (or, given accounts, any of them), else None."""
	record = _read(PAUSE_FILE)

	if record:
		return record

	for account in accounts or []:
		record = _read(_file_for(account))

		if record:
			return record

	return None


def paused_for(account: str) -> dict | None:
	"""The pause record for one account alone, or the whole brake's."""
	return _read(PAUSE_FILE) or _read(_file_for(account))


def blocked(accounts: list[str] | None) -> dict | None:
	"""Why a scheduler should not start a run for these accounts, or None.

	The whole brake blocks it. An account's own pause blocks it only when that
	leaves nothing to run: the entry points skip a paused account themselves.
	"""
	record = _read(PAUSE_FILE)

	if record:
		return record

	if accounts and all(_read(_file_for(a)) for a in accounts):
		return _read(_file_for(accounts[0]))

	return None


def trip(risk: Risk, account_name: str) -> None:
	"""Turn the brake on and say so. Idempotent: an existing pause is kept."""
	scope = account_name if risk.kind == SIGNED_OUT else None
	path = _file_for(scope)

	if _read(path) or _read(PAUSE_FILE):
		return

	record = {
		"kind": risk.kind,
		"reason": risk.reason,
		"account": account_name,
		"time": time.strftime("%Y-%m-%d %H:%M:%S"),
	}

	try:
		os.makedirs(os.path.dirname(path), exist_ok=True)

		with open(path, "w", encoding="utf-8") as handle:
			json.dump(record, handle)
	except OSError as exc:
		logger.error("Could not write the pause file: %s", exc)

	command = "python src/safety.py clear" if scope is None else f"python src/safety.py clear {scope}"
	stops = "Runs are" if scope is None else f"Runs for {scope} are"

	logger.error("[BRAKE] %s on %s: %s. %s paused until `%s`.", risk.kind, account_name, risk.reason, stops, command)

	notify.send(
		"Rewards bot paused",
		f"{account_name}: {risk.kind} - {risk.reason}. Look at the account, then run: {command}",
		priority="high",
		account=account_name,
	)


def clear(account: str | None = None) -> bool:
	"""Turn the brake off: the whole brake and every account's pause, or one account's. Returns whether any was on."""
	if account is not None:
		targets = [_file_for(account)]
	else:
		targets = [PAUSE_FILE] + glob.glob(f"{glob.escape(PAUSE_FILE)}.*")

	removed = False

	for path in targets:
		try:
			os.remove(path)
			removed = True
		except FileNotFoundError:
			pass

	return removed


def guard(driver, account_name: str) -> None:
	"""Raise AccountAtRisk, after tripping the brake, if the page needs a human."""
	risk = inspect(driver)

	if risk:
		trip(risk, account_name)

		raise AccountAtRisk(risk)


def main(argv: list[str]) -> int:
	command = argv[1] if len(argv) > 1 else "status"
	account = argv[2] if len(argv) > 2 else None

	if command == "status":
		records = [r for r in [_read(PAUSE_FILE)] + [_read(p) for p in sorted(glob.glob(f"{glob.escape(PAUSE_FILE)}.*"))] if r]

		print(chr(10).join("PAUSED: %s" % json.dumps(r) for r in records) if records else "running normally")

		return 0

	if command == "clear":
		print("cleared" if clear(account) else "was not paused")

		return 0

	print("usage: safety.py [status | clear [account]]")

	return 2


if __name__ == "__main__":
	sys.exit(main(sys.argv))
