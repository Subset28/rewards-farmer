"""Push notifications for things that need a human.

Everything goes to one destination, NOTIFY_URL (decided 10-08: one channel, not one per account or per kind of
message). Each message names its account, so a single channel stays readable:

    NOTIFY_URL=https://discord.com/api/webhooks/...

A Discord webhook address is sent as a Discord message; any other address is treated as an ntfy topic. With nothing set,
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


def url_for(account: str | None = None) -> str:
	"""The one destination, or an empty string. (`account` is kept so callers need not change; it no longer routes.)"""
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
	"""Send once, naming the accounts it is about in the title."""
	send(f"{', '.join(accounts)}: {title}" if accounts else title, message, priority=priority)


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
