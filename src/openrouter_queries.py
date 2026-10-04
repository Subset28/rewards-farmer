"""Search queries from OpenRouter's free models, within a hard daily budget.

    QUERY_SOURCE=openrouter
    OPENROUTER_API_KEY=...        (in .env on the machine, never in chat or git)
    OPENROUTER_MODEL=google/gemma-4-26b-a4b-it:free

What it adds over the public feeds is a subject. Each account has a persona, a
handful of interests, and its searching is partly about them: sessions of two to
four queries where a topic is narrowed down or followed up, instead of a
different trending name every time. The persona is the owner's own, if they
write one (data-dir/interests/<account>.txt, one interest per line); otherwise
one is invented once and kept (data-dir/persona/<account>.json), so an account
has the same interests from one day to the next. An invented persona is a
stand-in for the real thing. It makes the searching consistent, not genuine.

Free endpoints log what they are sent and may use it to improve their models.
What goes out is the interests and recent search phrases, never a name, an
account or the key, so keep anything personal out of an interests file.

The free tier allows 50 requests a day and 20 a minute, so a request is spent on
a whole batch of sessions, kept as a pool for the day, and a day costs a handful
of requests. Every attempt counts, failed ones included, because the provider
counts them, and a hard daily cap below the allowance (OPENROUTER_DAILY_LIMIT,
40) is enforced from a file so it holds across the separate processes and
containers that share data-dir.

Nothing here can stop a run. No key, no budget left, a 429, a timeout or a reply
that is not a list of queries all return nothing, and the caller falls back to
the public feeds. After a 429 or a rejected key it also stays quiet for a while
instead of asking again.
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
PERSONA_DIR = os.path.join(USER_DATA_DIR, "persona")

DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
# A plain instruction-tuned model, with thinking off unless asked for, chosen
# from the free text models by its published numbers: over a week its round trip
# was 4.5s at the median and 20s at the 90th percentile (the 31B sibling: 60s),
# with 98-99% availability. Not "openrouter/free", which can land on a reasoning
# or coding model that spends its output limit thinking and returns no queries,
# and not the Inkling models, whose free endpoint is for agentic harnesses only.
DEFAULT_MODEL = "google/gemma-4-26b-a4b-it:free"
DEFAULT_DAILY_LIMIT = 40
SESSIONS_PER_BATCH = 8
MAX_SESSION_LENGTH = 4
# The free endpoint has a slow tail (median round trip under 5 seconds, 90th
# percentile 20, 99th about a minute), and a timed-out request is spent all the
# same, so give it room.
REQUEST_TIMEOUT = 100

# Quiet time after a rejected key, and after a 429 that names no time.
KEY_REJECTED_QUIET_SECONDS = 6 * 3600
RATE_LIMITED_QUIET_SECONDS = 15 * 60

# Answers that repeating the request cannot change, from OpenRouter's documented
# error codes: the quiet time is a few hours, and the log says what to check.
CONFIG_ERRORS = {
	400: "a request the model rejects",
	402: "credits are needed, so this is not a free model",
	404: "no such model, or no provider for it",
}
CONFIG_ERROR_QUIET_SECONDS = 6 * 3600

# Quiet time after a response that reports a cost.
BILLED_QUIET_SECONDS = 24 * 3600

# At most one call every few seconds, under 20 a minute with room to spare.
MIN_SPACING_SECONDS = 3.5
_last_call = 0.0

MAX_WORDS = 8
MAX_CHARS = 70
MAX_INTERESTS = 8

TOPICS = "news, sports, cooking, technology, travel, health, shopping, entertainment, home and garden, money, cars, science"

NOT_A_QUERY = re.compile(r"^(here|sure|okay|ok|certainly|these|below|queries?|search queries?|session)\b|:$|https?://|www\.", re.I)
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


def _name(account: str | None) -> str:
	return account or "default"


def _interests_file_lines(account: str | None) -> list[str]:
	try:
		with open(os.path.join(INTERESTS_DIR, f"{_name(account)}.txt"), encoding="utf-8") as handle:
			return [line.strip() for line in handle if line.strip() and not line.lstrip().startswith("#")][:12]
	except OSError:
		return []


def interests(account: str | None) -> list[str]:
	"""What this account is into: the owner's own list if there is one, else the persona kept for it."""
	typed = _interests_file_lines(account)

	if typed:
		return typed

	kept = _read_json(os.path.join(PERSONA_DIR, f"{_name(account)}.json")).get("interests", [])

	return [i for i in kept if isinstance(i, str)][:MAX_INTERESTS]


def persona_messages() -> list[dict]:
	return [
		{
			"role": "system",
			"content": (
				"You invent realistic, ordinary people for a study of everyday web search. "
				"Output only what is asked, one item per line, with no numbering, labels or commentary."
			),
		},
		{
			"role": "user",
			"content": (
				"Describe the interests of one ordinary adult in six lines. Each line is one specific interest "
				"in a few words: a sports team or a sport, a hobby, a kind of cooking, a genre of music or shows, "
				"something they are shopping for or planning, a practical concern. Be specific, not generic."
			),
		},
	]


def ensure_persona(account: str | None) -> list[str]:
	"""The account's interests, inventing and keeping a persona once if it has none.

	Costs one request, once per account. Returns the interests, or an empty list
	when there are none and none could be made.
	"""
	have = interests(account)

	if have or not may_ask():
		return have

	reply = _request(persona_messages())

	if not reply:
		return []

	made = []

	for line in reply.splitlines():
		line = LEADING_MARKS.sub("", line).strip().strip("\"'`")
		line = " ".join(re.sub(r"[\"?!;:()\[\]]", " ", line.lower()).split())

		if 4 <= len(line) <= 60 and not NOT_A_QUERY.search(line) and line not in made:
			made.append(line)

	made = made[:MAX_INTERESTS]

	if len(made) < 3:
		logger.warning("OpenRouter's persona reply was not a list of interests. Searching without one for now.")

		return []

	_write_json(os.path.join(PERSONA_DIR, f"{_name(account)}.json"), {"interests": made, "made": _today()})
	logger.info("%s: invented a persona to search from (%d interests).", _name(account), len(made))

	return made


def build_messages(sessions: int, account: str | None, avoid: list[str]) -> list[dict]:
	about = interests(account)

	system = (
		"You write realistic web search queries, the way people actually type them into a search box: "
		"short, lower case, no punctuation, no quotation marks, sometimes just a few words, never a full sentence. "
		f"A session is two to {MAX_SESSION_LENGTH} searches in a row where the person narrows a topic down or follows up on it. "
		"Output only the queries, one per line, with a blank line between sessions and no numbering, labels or commentary."
	)

	if about:
		focus = f"The person is interested in: {', '.join(about)}. About half of the sessions should be about those interests, the rest about other everyday things."
	else:
		focus = f"Mix everyday topics: {TOPICS}."

	recent = f" Do not repeat or closely rephrase any of these: {', '.join(avoid[:40])}." if avoid else ""

	user = (
		f"Write {sessions} different search sessions someone might do over a single day. {focus} "
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


def parse_sessions(text: str, avoid=None) -> list[list[str]]:
	"""Sessions out of a reply: blocks of queries split by blank lines, no query used twice.

	A reply with no blank lines is one long list, cut into sessions of three
	rather than thrown away or kept as one marathon.
	"""
	skip = {" ".join(a.lower().split()) for a in (avoid or ())}
	blocks = [b for b in re.split(r"\n\s*\n", text or "") if b.strip()]
	sessions: list[list[str]] = []

	for block in blocks:
		queries = parse_queries(block, avoid=skip)

		if not queries:
			continue

		skip |= set(queries)
		sessions.append(queries)

	if len(sessions) == 1 and len(sessions[0]) > MAX_SESSION_LENGTH:
		flat = sessions[0]
		sessions = [flat[i:i + 3] for i in range(0, len(flat), 3)]

	return [s[:MAX_SESSION_LENGTH] for s in sessions]


def model_to_use() -> str:
	"""The model to ask, always a free one unless paid use is switched on.

	OpenRouter lists the paid model under the same name without the ":free"
	suffix, so one missing suffix in OPENROUTER_MODEL would start charging.
	Anything that is not free falls back to the default, with a warning;
	OPENROUTER_ALLOW_PAID=1 is the way to mean it.
	"""
	model = os.environ.get("OPENROUTER_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL

	if model.endswith(":free") or model == "openrouter/free" or os.environ.get("OPENROUTER_ALLOW_PAID", "").strip() == "1":
		return model

	logger.warning(
		"OPENROUTER_MODEL=%r is not a free model (no ':free' suffix) and would be billed. Using %s instead. "
		"Set OPENROUTER_ALLOW_PAID=1 to allow paid models.",
		model, DEFAULT_MODEL,
	)

	return DEFAULT_MODEL


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
	model = model_to_use()
	body = json.dumps({
		"model": model,
		"messages": messages,
		"temperature": 1.0,
		"max_tokens": 900,
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
			logger.warning("OpenRouter rejected the key or blocked access (%s). Using the trends source.", exc.code)
		elif exc.code in CONFIG_ERRORS:
			# A malformed request, a model that needs credits, or one that does not
			# exist: asking again sends the same request and gets the same answer.
			_spend(quiet_for=CONFIG_ERROR_QUIET_SECONDS)
			logger.warning(
				"OpenRouter answered %s (%s), which asking again will not fix. Using the trends source for a few hours. "
				"Check OPENROUTER_MODEL (%s).",
				exc.code, CONFIG_ERRORS[exc.code], model,
			)
		else:
			_spend()
			logger.warning("OpenRouter answered %s (an upstream failure, usually brief). Using the trends source for this batch.", exc.code)

		exc.close()

		return None
	except (urllib.error.URLError, OSError, ValueError) as exc:
		_spend()
		logger.warning("OpenRouter could not be reached (%s). Using the trends source for this batch.", type(exc).__name__)

		return None

	# A free model should never report a cost. If one does, something is being
	# billed: use this reply, since it is already paid for, and stop asking for a day.
	usage = payload.get("usage") if isinstance(payload, dict) else None
	cost = usage.get("cost") if isinstance(usage, dict) else None
	billed = isinstance(cost, (int, float)) and not isinstance(cost, bool) and cost > 0

	_spend(quiet_for=BILLED_QUIET_SECONDS if billed else 0.0)

	if billed:
		logger.warning(
			"OpenRouter reported a cost of %s for %s, so this request was billed. Not asking again for a day. "
			"Check OPENROUTER_MODEL and the account's credits.",
			cost, model,
		)

	try:
		content = payload["choices"][0]["message"]["content"]
	except (KeyError, IndexError, TypeError):
		logger.warning("OpenRouter's reply had no text. Using the trends source for this batch.")

		return None

	return content if isinstance(content, str) else None


def _pool(account: str | None) -> list[list[str]]:
	entry = _read_json(POOL_FILE).get(_name(account), {})

	if entry.get("date") != _today():
		return []

	# An older pool was a flat list of queries; each becomes a session of one.
	sessions = entry.get("sessions")

	if sessions is None:
		sessions = [[q] for q in entry.get("queries", [])]

	return [[q for q in s if isinstance(q, str)] for s in sessions if isinstance(s, list)]


def _save_pool(account: str | None, sessions: list[list[str]]) -> None:
	pools = _read_json(POOL_FILE)
	pools[_name(account)] = {"date": _today(), "sessions": sessions}
	_write_json(POOL_FILE, pools)


def related_queries(count: int, account: str | None = None, exclude=None) -> list[str]:
	"""Up to `count` queries for this account, none in `exclude`, in session order.

	Fewer (or none) come back when OpenRouter cannot supply them. Served from
	today's pool when it has enough; otherwise a persona is made if the account
	has none and one batch of sessions is asked for. Whatever is left after this
	call is kept for the next, and the rest of a session that was cut short comes
	first next time, so consecutive searches stay on one topic.
	"""
	avoid = {" ".join(q.lower().split()) for q in (exclude or ())}
	sessions = [[q for q in s if q not in avoid] for s in _pool(account)]
	sessions = [s for s in sessions if s]

	if sum(len(s) for s in sessions) < count and may_ask():
		ensure_persona(account)

		if may_ask():
			reply = _request(build_messages(SESSIONS_PER_BATCH, account, sorted(avoid)[:40]))

			if reply:
				pooled = {q for s in sessions for q in s}
				fresh = parse_sessions(reply, avoid | pooled)

				if fresh:
					sessions += fresh
				else:
					logger.warning("OpenRouter's reply held no usable queries. Using the trends source for this batch.")

	taken: list[str] = []

	while sessions and len(taken) < count:
		session = sessions.pop(0)
		room = count - len(taken)
		taken += session[:room]

		if len(session) > room:
			sessions.insert(0, session[room:])

	_save_pool(account, sessions)

	return taken
