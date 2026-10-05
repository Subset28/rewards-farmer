"""Push notifications for things that need a human.

Each account can have its own destination, so one account's alerts do not land
in another's channel:

    NOTIFY_URL_DEFAULT=https://discord.com/api/webhooks/...   # the "default" account
    NOTIFY_URL_SECOND=https://discord.com/api/webhooks/...    # the "second" account
    NOTIFY_URL=https://ntfy.sh/your-topic                      # anything without its own

The name is the account's profile name, upper-cased, with anything that is not a
letter or digit turned into an underscore. A Discord webhook address is sent as a
Discord message; any other address is treated as an ntfy topic. With nothing set,
send() only logs, so nothing about a run depends on it. It never raises: an alert
that fails must not be the reason a run does.

Messages carry an account's profile name and a reason, never a credential, and a
webhook address is never logged, because anyone holding it can post to the channel.
"""

import json
import logging
import os
import re
import urllib.error
import urllib.parse
import urllib.request

logger = logging.getLogger(__name__)

TIMEOUT_SECONDS = 10

# Discord rejects a message longer than this, and rejects the default Python
# user agent outright, so both are handled here.
DISCORD_LIMIT = 2000
USER_AGENT = "rewards-farmer-notify"

DISCORD_HOSTS = ("discord.com", "discordapp.com", "canary.discord.com", "ptb.discord.com")


def env_name(account: str) -> str:
	"""The environment variable that holds this account's destination."""
	return "NOTIFY_URL_" + re.sub(r"[^A-Za-z0-9]", "_", account).upper()


def url_for(account: str | None = None) -> str:
	"""This account's own destination, else the shared one, else an empty string."""
	if account:
		own = os.environ.get(env_name(account), "").strip()

		if own:
			return own

	return os.environ.get("NOTIFY_URL", "").strip()


def is_discord(url: str) -> bool:
	parts = urllib.parse.urlsplit(url)

	return (parts.hostname or "").lower() in DISCORD_HOSTS and parts.path.startswith("/api/webhooks/")


def _discord_request(url: str, title: str, message: str, priority: str) -> urllib.request.Request:
	text = f"**{title}**\n{message}"

	if priority in ("high", "urgent"):
		text = "\N{WARNING SIGN} " + text

	body = {"content": text[:DISCORD_LIMIT], "allowed_mentions": {"parse": []}}

	return urllib.request.Request(
		url,
		data=json.dumps(body).encode("utf-8"),
		method="POST",
		headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
	)


def _ntfy_request(url: str, title: str, message: str, priority: str) -> urllib.request.Request:
	return urllib.request.Request(
		url,
		data=message.encode("utf-8"),
		method="POST",
		headers={
			# Header values are latin-1 on the wire, and a title is ours, so keep
			# it to ASCII rather than trust whatever ends up in it.
			"Title": title.encode("ascii", "replace").decode("ascii"),
			"Priority": priority,
			"User-Agent": USER_AGENT,
		},
	)


def send_each(accounts: list[str] | None, title: str, message: str, priority: str = "default") -> None:
	"""Send to every one of these accounts' destinations, or once to the shared one when there are none."""
	for account in accounts or [None]:
		send(title, message, priority=priority, account=account)


def send(title: str, message: str, priority: str = "default", account: str | None = None) -> bool:
	"""Deliver one notification. Returns whether it was handed to the server."""
	log_line = "%s: %s" % (title, message)
	url = url_for(account)

	if not url:
		logger.info("[NOTIFY] %s", log_line)

		return False

	if not url.lower().startswith(("https://", "http://")):
		logger.warning("The notification address is not an http(s) address, not sending: %s", log_line)

		return False

	build = _discord_request if is_discord(url) else _ntfy_request

	try:
		with urllib.request.urlopen(build(url, title, message, priority), timeout=TIMEOUT_SECONDS):
			pass
	except (urllib.error.URLError, OSError, ValueError) as exc:
		# The exception text can carry the address, so only its type is logged.
		logger.warning("Could not send the notification (%s): %s", type(exc).__name__, log_line)

		return False

	logger.info("[NOTIFY] sent: %s", log_line)

	return True
