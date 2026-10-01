"""Push notifications for things that need a human.

Set NOTIFY_URL to an ntfy topic (https://ntfy.sh/your-topic, or a self-hosted
one) and these arrive on a phone. Unset, send() only logs, so nothing about a
run depends on it. It never raises: an alert that fails must not be the reason a
run does.

Messages carry an account's profile name and a reason, never a credential.
"""

import logging
import os
import urllib.error
import urllib.request

logger = logging.getLogger(__name__)

TIMEOUT_SECONDS = 10


def send(title: str, message: str, priority: str = "default") -> bool:
	"""Deliver one notification. Returns whether it was handed to the server."""
	log_line = "%s: %s" % (title, message)
	url = os.environ.get("NOTIFY_URL", "").strip()

	if not url:
		logger.info("[NOTIFY] %s", log_line)

		return False

	if not url.lower().startswith(("https://", "http://")):
		logger.warning("NOTIFY_URL is not an http(s) address, not sending: %s", log_line)

		return False

	request = urllib.request.Request(
		url,
		data=message.encode("utf-8"),
		method="POST",
		headers={
			# Header values are latin-1 on the wire, and a title is ours, so keep
			# it to ASCII rather than trust whatever ends up in it.
			"Title": title.encode("ascii", "replace").decode("ascii"),
			"Priority": priority,
		},
	)

	try:
		with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS):
			pass
	except (urllib.error.URLError, OSError, ValueError) as exc:
		logger.warning("Could not send the notification (%s): %s", type(exc).__name__, log_line)

		return False

	logger.info("[NOTIFY] sent: %s", log_line)

	return True
