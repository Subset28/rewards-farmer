"""Fetch a public page through trawl when a plain request is refused.

trawl (https://github.com/germondai/trawl) is a FlareSolverr-compatible service
that loads a page in a real, hardened browser and hands back the HTML. Here it is
only a fallback for the public feeds the search queries come from (trends,
autosuggest): when a plain request is refused or answered with a challenge page,
the same page is asked for through trawl, if TRAWL_URL says where it is.

    TRAWL_URL=http://192.168.35.12:8191

Never for an account. Microsoft's pages, sign-in and verification are not fetched
through it, by design: a verification prompt on an account is a reason to stop and
look (the brake does that), not something to click through. Anything under the
domains in REFUSED_DOMAINS is refused here whatever the caller asks.

Unset, nothing here does anything, so a run does not depend on it. It never
raises: a fetch that fails just means the next source is tried.
"""

import json
import logging
import os
import re
import urllib.error
import urllib.parse
import urllib.request

logger = logging.getLogger(__name__)

TIMEOUT_SECONDS = 60
SOLVER_TIMEOUT_MS = 45000

# Plain-request answers that mean "a challenge, not the page".
BLOCKED_CODES = (403, 429, 503)
CHALLENGE_MARKERS = ("just a moment", "cf-chl", "challenge-platform", "attention required")

# The account side of Microsoft. Fetching these through a solver is exactly what
# this must never be used for.
REFUSED_DOMAINS = (
	"microsoft.com", "live.com", "bing.com", "msn.com", "office.com", "microsoftonline.com",
	"outlook.com", "xbox.com", "windows.com", "azure.com", "msauth.net", "msftauth.net",
)

PRE = re.compile(r"<pre[^>]*>(.*?)</pre>", re.S | re.I)


def base_url() -> str:
	return os.environ.get("TRAWL_URL", "").strip().rstrip("/")


def refused(url: str) -> bool:
	"""Whether this address is one that must never go through a solver."""
	host = (urllib.parse.urlsplit(url).hostname or "").lower()

	return any(host == domain or host.endswith("." + domain) for domain in REFUSED_DOMAINS)


def looks_blocked(body: str | None) -> bool:
	"""Whether a body is a challenge page rather than what was asked for."""
	head = (body or "")[:4000].lower()

	return any(marker in head for marker in CHALLENGE_MARKERS)


def _unwrap(text: str) -> str:
	"""A browser shows a bare text or JSON answer inside a <pre>; give back what was inside it."""
	if text.lstrip().lower().startswith(("<html", "<!doctype")):
		match = PRE.search(text)

		if match:
			return match.group(1).replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")

	return text


def fetch(url: str) -> str | None:
	"""The page's body through trawl, or None when trawl is not set, refuses it, or fails."""
	service = base_url()

	if not service:
		return None

	if not service.lower().startswith(("http://", "https://")):
		logger.warning("TRAWL_URL is not an http(s) address, not using it.")

		return None

	if refused(url):
		logger.warning("Not fetching %s through trawl: it is an account-side address.", urllib.parse.urlsplit(url).hostname)

		return None

	request = urllib.request.Request(
		f"{service}/v1",
		data=json.dumps({"cmd": "request.get", "url": url, "maxTimeout": SOLVER_TIMEOUT_MS}).encode("utf-8"),
		method="POST",
		headers={"Content-Type": "application/json"},
	)

	try:
		with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
			answer = json.loads(response.read().decode("utf-8", "replace"))
	except (urllib.error.URLError, OSError, ValueError) as exc:
		logger.warning("trawl did not answer (%s).", type(exc).__name__)

		return None

	solution = answer.get("solution") if isinstance(answer, dict) else None

	if not isinstance(answer, dict) or answer.get("status") != "ok" or not isinstance(solution, dict):
		logger.warning("trawl could not fetch the page: %s", str(answer.get("message", ""))[:120] if isinstance(answer, dict) else "")

		return None

	body = solution.get("response")

	if solution.get("status") != 200 or not isinstance(body, str) or looks_blocked(body):
		return None

	logger.info("Fetched a refused page through trawl.")

	return _unwrap(body)
