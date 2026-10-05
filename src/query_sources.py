"""Search queries from public feeds instead of a language model.

The LLM in this project has one job: produce short strings to type into Bing.
That is worth an Ollama account and a model download if you want it, but it is
not the only way to get a search query, and it is the piece that stops someone
running the bot in five minutes.

Three keyless sources, all stdlib, no new dependencies:

  Google Trends RSS   real queries people are typing right now
  Wikipedia most-read topic seeds, useful when trends is unavailable
  Bing autosuggest    expands a seed into related queries

Autosuggest is what makes the chaining work. Asking Bing what follows a term
gives queries Bing itself expects, which is closer to what the LLM prompt was
reaching for than a model guessing in the dark.

Every source degrades rather than raises. A search that does not happen costs
points; a run that dies costs the rest of the day's points too.
"""

import json
import os
import random
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, timedelta

from constants import REPO_ROOT

TRENDS_URL = "https://trends.google.com/trending/rss?geo={geo}"
WIKIPEDIA_URL = "https://en.wikipedia.org/api/rest_v1/feed/featured/{y}/{m:02d}/{d:02d}"
AUTOSUGGEST_URL = "https://api.bing.com/osjson.aspx?query={query}"

# A browser agent: trends and the Wikipedia REST feed both answer differently
# to an unfamiliar client, and one of them refuses outright.
USER_AGENT = (
	"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
	"(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

REQUEST_TIMEOUT = 15

# Words that make a query read as an instruction rather than a search. The
# task descriptions are phrased at the user, "Search on Bing to compare
# checking accounts", and typing that verbatim searches for the sentence.
INSTRUCTION_WORDS = {
	"search", "searching", "bing", "on", "to", "the", "a", "an", "for", "your",
	"you", "use", "using", "find", "get", "with", "and", "or", "of", "in",
	"at", "by", "now", "today", "this", "that", "these", "those", "learn",
	"discover", "explore", "check", "see", "our", "more", "about", "how",
}


def _fetch(url: str) -> str | None:
	"""Body of a GET, or None. Never raises: callers fall through to the next source."""
	request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})

	try:
		with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
			return response.read().decode("utf-8", "replace")
	except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError):
		return None


def trending_queries(geo: str = "US") -> list[str]:
	"""Queries currently trending on Google, most popular first.

	These are real searches rather than descriptions of searches, which is
	exactly the shape wanted here.
	"""
	body = _fetch(TRENDS_URL.format(geo=urllib.parse.quote(geo)))

	if not body:
		return []

	# The channel carries a <title> of its own before any item, so the first
	# match is the feed name rather than a query.
	titles = re.findall(r"<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>", body, re.S)

	return [_clean(t) for t in titles[1:] if _clean(t)]


def wikipedia_topics(days_ago: int = 1) -> list[str]:
	"""Most-read Wikipedia articles, as topic seeds.

	Yesterday by default: today's feed is not published until the day is over.
	"""
	day = date.today() - timedelta(days=days_ago)
	body = _fetch(WIKIPEDIA_URL.format(y=day.year, m=day.month, d=day.day))

	if not body:
		return []

	try:
		payload = json.loads(body)
	except json.JSONDecodeError:
		return []

	articles = payload.get("mostread", {}).get("articles", [])
	titles = [a.get("titles", {}).get("normalized", "") for a in articles]

	# Wikipedia's own chrome outranks real topics most days, and so do its list
	# and index pages, which nobody types into a search box.
	skipped = (
		"Main Page", "Special:", "Wikipedia:", "Portal:", "Category:", "File:", "Template:", "Help:",
		"Deaths in", "List of", "Lists of", "Index of", "Outline of", "Timeline of",
	)

	topics = []

	for title in titles:
		if not title or title.startswith(skipped):
			continue

		# "Michael McDonald (musician)" is how the encyclopedia tells two people
		# apart; a person searching types the name.
		topic = _clean(re.sub(r"\s*\([^)]*\)\s*$", "", title))

		if topic:
			topics.append(topic)

	return topics


def suggestions(seed: str) -> list[str]:
	"""What Bing suggests for a term, which is what Bing expects to be asked."""
	if not seed.strip():
		return []

	body = _fetch(AUTOSUGGEST_URL.format(query=urllib.parse.quote(seed)))

	if not body:
		return []

	try:
		payload = json.loads(body)
	except json.JSONDecodeError:
		return []

	# Opensearch shape: [term, [suggestions], ...]
	if not isinstance(payload, list) or len(payload) < 2 or not isinstance(payload[1], list):
		return []

	return [_clean(s) for s in payload[1] if _clean(s)]


def wordlist_queries(count: int) -> list[str]:
	"""Seeds from nouns.txt, the last resort when nothing is reachable.

	Read here rather than borrowed from llm_utils so that a trends-only install
	never has to import the model client.
	"""
	try:
		with open(os.path.join(REPO_ROOT, "nouns.txt"), encoding="utf-8") as handle:
			nouns = [line.strip().lower() for line in handle if len(line.strip()) >= 3]
	except OSError:
		return []

	if not nouns:
		return []

	return random.sample(nouns, min(count, len(nouns)))


def _clean(text: str) -> str:
	"""Strip markup, collapse whitespace and drop punctuation Bing does not need."""
	text = re.sub(r"<[^>]+>", " ", text or "")
	text = re.sub(r"[\"'?!,;:]", " ", text)

	return " ".join(text.split()).strip().lower()



# Cards that ask for "a word", "a time zone", "a stock" or "your favorite song"
# and not a topic. Searching the sentence itself never searched for an actual
# word, place, ticker or song, and those cards stayed uncredited while the ones
# worded as a concrete search credited. Each rule turns the placeholder into one
# ordinary real search.
PLACEHOLDER_RULES = (
	(
		re.compile(r"meaning of a word|word you don.?t understand|define a word", re.I),
		"define {}",
		("serendipity", "ephemeral", "ubiquitous", "eloquent", "resilient", "nostalgia", "ambiguous", "benevolent", "pragmatic", "meticulous"),
	),
	(
		re.compile(r"time zone", re.I),
		"current time in {}",
		("Tokyo", "London", "Sydney", "Dubai", "Paris", "Los Angeles", "Singapore", "Mumbai", "Berlin", "Toronto"),
	),
	(
		re.compile(r"price of a (specific )?stock|a specific stock", re.I),
		"{} stock price",
		("MSFT", "AAPL", "GOOGL", "AMZN", "NVDA", "TSLA", "META", "NFLX"),
	),
	(
		re.compile(r"items on your shopping list|your shopping list", re.I),
		"buy {}",
		("laundry detergent", "paper towels", "olive oil", "coffee beans", "dish soap", "toothpaste", "peanut butter", "basmati rice"),
	),
	(
		re.compile(r"favou?rite song", re.I),
		"{} lyrics",
		("Bohemian Rhapsody", "Imagine John Lennon", "Hotel California", "Yesterday Beatles", "Billie Jean", "Hey Jude", "Rolling in the Deep", "Shape of You", "Stairway to Heaven", "Let It Be"),
	),
)


def concrete_query(description: str, pick: int = 0, rng=random, avoid=None) -> str | None:
	"""A real search for a card that names a placeholder, else None.

	`pick` > 0 gives a different one from the same list, for the retry of a card
	that did not credit. Anything in `avoid` (what this account already searched)
	is passed over while the list has something else.
	"""
	skip = {" ".join(q.lower().split()) for q in (avoid or ())}

	for pattern, template, choices in PLACEHOLDER_RULES:
		if pattern.search(description or ""):
			options = [c for c in choices if " ".join(template.format(c).lower().split()) not in skip] or list(choices)

			return template.format(options[(rng.randrange(len(options)) + pick) % len(options)])

	return None


def query_from_task_description(description: str, pick: int = 0, avoid=None) -> str | None:
	"""A search query for a task phrased as an instruction.

	"Search on Bing to compare checking and savings account options" becomes
	the content words, then whatever Bing suggests for them, so the query is
	one Bing already recognises rather than the sentence itself.
	"""
	concrete = concrete_query(description, pick, avoid=avoid)

	if concrete:
		return concrete

	words = [w for w in _clean(description).split() if w not in INSTRUCTION_WORDS]

	if not words:
		return None

	seed = " ".join(words[:6])
	options = suggestions(seed)

	# Prefer a suggestion, since it is a query Bing has seen. The trimmed
	# sentence is a reasonable fallback and still beats typing the imperative.
	# `pick` asks for a different one than last time, for a card that did not
	# credit; when Bing has fewer suggestions it falls to the last one it has.
	if options:
		return options[min(pick, len(options) - 1)]

	return seed


# How many of the feed's entries are worth drawing from. The tail of a trends
# feed is as real as its head; only taking the head repeated the same few.
POOL_LIMIT = 30

# Chance that a search follows up on the one before it with one of Bing's own
# suggestions for it, the way a person narrows a query, instead of jumping to
# an unrelated topic every time.
REFINE_CHANCE = 0.25


def _norm(query: str) -> str:
	return " ".join((query or "").lower().split())


def related_queries(count: int, seed: str | None = None, exclude=None, rng=random) -> list[str]:
	"""`count` distinct queries, none of them in `exclude`.

	Drawn at random from the whole trending feed, topped up with Wikipedia's
	most-read topics, rather than from the top of the feed in order: taking the
	top in order made the second batch of a run repeat the first, and every run
	that day repeat the one before. Now and then a query is a follow-up to the
	one before it instead of a new topic.

	Fewer than `count` come back when nothing reachable is fresh; the caller
	decides what to do then, rather than typing junk.
	"""
	avoid = {_norm(q) for q in (exclude or ())}
	collected: list[str] = []

	def fresh(candidate: str) -> bool:
		return bool(candidate) and len(candidate) > 2 and _norm(candidate) not in avoid

	def add(candidate: str) -> None:
		collected.append(candidate)
		avoid.add(_norm(candidate))

	def unique(candidates) -> list[str]:
		seen, out = set(), []

		for candidate in candidates:
			key = _norm(candidate)

			if fresh(candidate) and key not in seen:
				seen.add(key)
				out.append(candidate)

		return out

	pool = unique((suggestions(seed) if seed else []) + trending_queries()[:POOL_LIMIT])

	if len(pool) < count * 3:
		pool = unique(pool + wikipedia_topics()[:POOL_LIMIT])

	rng.shuffle(pool)

	while len(collected) < count:
		candidate = None

		if collected and rng.random() < REFINE_CHANCE:
			options = [s for s in suggestions(collected[-1]) if fresh(s)]

			if options:
				candidate = rng.choice(options[:5])

		while candidate is None and pool:
			picked = pool.pop()

			if fresh(picked):
				candidate = picked

		if candidate is None:
			# The pool is spent: ask Bing what follows something already searched.
			for term in reversed(collected):
				options = [s for s in suggestions(term) if fresh(s)]

				if options:
					candidate = rng.choice(options[:5])

					break

		if candidate is None:
			break

		add(candidate)

	return collected
