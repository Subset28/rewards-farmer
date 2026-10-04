"""Search queries from OpenRouter's free models, within a hard daily budget.

    QUERY_SOURCE=openrouter
    OPENROUTER_API_KEY=...        (in .env on the machine, never in chat or git)
    OPENROUTER_MODEL=openrouter/free

The free tier allows 50 requests a day and 20 a minute, so a request is spent on
a whole batch, not on a query: one call returns two dozen, they are kept as a
pool for the day, and a day costs a handful of requests. Every attempt counts,
failed ones included, because the provider counts them, and a hard daily cap
below the allowance (OPENROUTER_DAILY_LIMIT, 40) is enforced from a file so it
holds across the separate processes and containers that share data-dir.

Nothing here can stop a run. No key, no budget left, a 429, a timeout or a reply
that is not a list of queries all return nothing, and the caller falls back to
the trends source. After a 429 or a rejected key it also stays quiet for a while
instead of asking again.

An account can have interests (data-dir/interests/<account>.txt, one per line)
and the queries are then partly about those, so an account's searching has a
subject and not only whatever is trending.
"""

import json
import logging
import os
import re
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

from constants import USER_DATA_DIR

logger = logging.getLogger(__name__)

USAGE_FILE = os.path.join(USER_DATA_DIR, "openrouter_usage.json")
POOL_FILE = os.path.join(USER_DATA_DIR, "openrouter_pool.json")
INTERESTS_DIR = os.path.join(USER_DATA_DIR, "interests")

DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "openrouter/free"
DEFAULT_DAILY_LIMIT = 40
BATCH_SIZE = 24
REQUEST_TIMEOUT = 45

# Quiet time after a rejected key, and after a 429 that names no time.
KEY_REJECTED_QUIET_SECONDS = 6 * 3600
RATE_LIMITED_QUIET_SECONDS = 15 * 60

# At most one call every few seconds, under 20 a minute with room to spare.
MIN_SPACING_SECONDS = 3.5
_last_call = 0.0

MAX_WORDS = 8
MAX_CHARS = 70

TOPICS = "news, sports, cooking, technology, travel, health, shopping, entertainment, home and garden, money, cars, science"

NOT_A_QUERY = re.compile(r"^(here|sure|okay|ok|certainly|these|below|queries?|search queries?)\b|:$|https?://|www\.", re.I)
LEADING_MARKS = re.compile(r"^\s*(?:[-*•]+|\d+[.)])\s*")


def _today() -> str:
	return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _read_json(path: str) -> dict:
	try:
		with open(path, encoding="utf-8") as handle:
			data = json.load(handle)

		return data if isinstance(data, dict) else {}
	except (OSError, ValueError):
		return {}


def _write_json(path: str, data: dict) -> None:
	try:
		os.makedirs(os.path.dirname(path), exist_ok=True)

		with open(path, "w", encoding="utf-8") as handle:
			json.dump(data, handle)
	except OSError as exc:
		logger.debug("Could not write %s: %s", path, exc)


def daily_limit() -> int:
	try:
		return max(0, int(os.environ.get("OPENROUTER_DAILY_LIMIT", DEFAULT_DAILY_LIMIT)))
	except ValueError:
		return DEFAULT_DAILY_LIMIT


def requests_today() -> int:
	usage = _read_json(USAGE_FILE)

	return int(usage.get("count", 0)) if usage.get("date") == _today() else 0


def quiet_until() -> float:
	return float(_read_json(USAGE_FILE).get("quiet_until", 0) or 0)


def may_ask(now: float | None = None) -> bool:
	"""Whether a request is allowed: budget left, and not in a quiet spell."""
	if requests_today() >= daily_limit():
		return False

	return (time.time() if now is None else now) >= quiet_until()


def _spend(quiet_for: float = 0.0) -> None:
	"""Count one request against today, and optionally go quiet for a while."""
	usage = _read_json(USAGE_FILE)
	count = int(usage.get("count", 0)) if usage.get("date") == _today() else 0

	_write_json(USAGE_FILE, {
		"date": _today(),
		"count": count + 1,
		"quiet_until": max(float(usage.get("quiet_until", 0) or 0), time.time() + quiet_for) if quiet_for else usage.get("quiet_until", 0),
	})


def interests(account: str | None) -> list[str]:
	"""What this account's owner is into, from data-dir/interests/<account>.txt."""
	try:
		with open(os.path.join(INTERESTS_DIR, f"{account or 'default'}.txt"), encoding="utf-8") as handle:
			return [line.strip() for line in handle if line.strip() and not line.lstrip().startswith("#")][:12]
	except OSError:
		return []


def build_messages(count: int, account: str | None, avoid: list[str]) -> list[dict]:
	about = interests(account)

	system = (
		"You write realistic web search queries, the way people actually type them into a search box: "
		"short, lower case, no punctuation, no quotation marks, sometimes just a few words, never a full sentence. "
		"Output only the queries, one per line, with no numbering, labels or commentary."
	)

	if about:
		focus = f"The person is interested in: {', '.join(about)}. About half of the queries should be about those interests, the rest about other everyday things."
	else:
		focus = f"Mix everyday topics: {TOPICS}."

	recent = f" Do not repeat or closely rephrase any of these: {', '.join(avoid[:40])}." if avoid else ""

	user = (
		f"Write {count} different search queries someone might type over a single day. {focus} "
		f"Each query is at most {MAX_WORDS} words and about something specific. The current year is {datetime.now().year}.{recent}"
	)

	return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def parse_queries(text: str, avoid=None) -> list[str]:
	"""Queries out of a model's reply, cleaned, deduplicated and held to what a person would type."""
	skip = {" ".join(a.lower().split()) for a in (avoid or ())}
	seen, out = set(), []

	for line in (text or "").splitlines():
		line = LEADING_MARKS.sub("", line).strip().strip("\"'`")

		if not line or NOT_A_QUERY.search(line):
			continue

		query = " ".join(re.sub(r"[\"'?!,;:.()\[\]]", " ", line.lower()).split())
		words = query.split()

		if not 5 <= len(query) <= MAX_CHARS or not 1 <= len(words) <= MAX_WORDS or not any(len(w) >= 3 for w in words):
			continue

		if query in seen or query in skip:
			continue

		seen.add(query)
		out.append(query)

	return out


def _request(messages: list[dict]) -> str | None:
	"""One chat completion. Returns the reply text, or None after recording why not."""
	global _last_call

	key = os.environ.get("OPENROUTER_API_KEY", "").strip()

	if not key:
		logger.warning("QUERY_SOURCE=openrouter but OPENROUTER_API_KEY is not set. Using the trends source.")

		return None

	wait = MIN_SPACING_SECONDS - (time.monotonic() - _last_call)

	if wait > 0:
		time.sleep(wait)

	base = os.environ.get("OPENROUTER_BASE_URL", DEFAULT_BASE_URL).strip().rstrip("/")
	body = json.dumps({
		"model": os.environ.get("OPENROUTER_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL,
		"messages": messages,
		"temperature": 1.0,
		"max_tokens": 700,
	}).encode("utf-8")
	request = urllib.request.Request(
		f"{base}/chat/completions",
		data=body,
		method="POST",
		headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
	)

	_last_call = time.monotonic()

	try:
		with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
			payload = json.loads(response.read().decode("utf-8", "replace"))
	except urllib.error.HTTPError as exc:
		if exc.code == 429:
			try:
				quiet = float(exc.headers.get("Retry-After", RATE_LIMITED_QUIET_SECONDS))
			except (TypeError, ValueError):
				quiet = RATE_LIMITED_QUIET_SECONDS

			_spend(quiet_for=max(quiet, 60))
			logger.warning("OpenRouter says slow down (429). Using the trends source for a while.")
		elif exc.code in (401, 403):
			_spend(quiet_for=KEY_REJECTED_QUIET_SECONDS)
			logger.warning("OpenRouter rejected the key (%s). Using the trends source.", exc.code)
		else:
			_spend()
			logger.warning("OpenRouter answered %s. Using the trends source for this batch.", exc.code)

		exc.close()

		return None
	except (urllib.error.URLError, OSError, ValueError) as exc:
		_spend()
		logger.warning("OpenRouter could not be reached (%s). Using the trends source for this batch.", type(exc).__name__)

		return None

	_spend()

	try:
		content = payload["choices"][0]["message"]["content"]
	except (KeyError, IndexError, TypeError):
		logger.warning("OpenRouter's reply had no text. Using the trends source for this batch.")

		return None

	return content if isinstance(content, str) else None


def _pool(account: str | None) -> list[str]:
	entry = _read_json(POOL_FILE).get(account or "default", {})

	return list(entry.get("queries", [])) if entry.get("date") == _today() else []


def _save_pool(account: str | None, queries: list[str]) -> None:
	pools = _read_json(POOL_FILE)
	pools[account or "default"] = {"date": _today(), "queries": queries}
	_write_json(POOL_FILE, pools)


def related_queries(count: int, account: str | None = None, exclude=None) -> list[str]:
	"""Up to `count` queries for this account, none in `exclude`; fewer (or none) when OpenRouter cannot supply them.

	Served from today's pool when it has enough; otherwise one batch is asked for
	and what is left after this call is kept for the next.
	"""
	avoid = {" ".join(q.lower().split()) for q in (exclude or ())}
	pool = [q for q in _pool(account) if q not in avoid]

	if len(pool) < count and may_ask():
		reply = _request(build_messages(BATCH_SIZE, account, sorted(avoid)[:40]))

		if reply:
			fresh = parse_queries(reply, avoid | set(pool))

			if fresh:
				pool += fresh
			else:
				logger.warning("OpenRouter's reply held no usable queries. Using the trends source for this batch.")

	taken, rest = pool[:count], pool[count:]
	_save_pool(account, rest)

	return taken
