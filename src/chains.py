"""Searching in tangents, the way a person does.

Real searches come in runs: a question, then a narrower question prompted by what the results just said, then
perhaps a detour into one of the results, then something unrelated an hour later. The bot used to load the Bing
homepage for every query and type the next one from a list made in advance. This plans the other thing:

  a tangent       a few searches on one thread, its length uneven (mostly one to three, sometimes ten or more)
  each step       either click one of the page's own "related searches", or type a follow-up built from them in
                  the results page's search box, or stop and start a new thread
  reading         between searches the person reads: scrolls, sometimes opens a result and looks at it, for as
                  long as people do (a skewed spread, not a flat range)
  where it starts a thread begins on the account's own interests when it has them (persona), with the news as seasoning

Nothing in here touches a browser: the page's related searches come in as text, and the decisions come back as
text and numbers, so all of it can be tested. rewards_tasks.run_chain_batch does the clicking.
"""

import math
import random

# How many searches a tangent has. Most are short; a few go on.
LENGTHS = (1, 2, 3, 4, 5, 6, 7, 8, 10, 12)
LENGTH_WEIGHTS = (28, 22, 16, 11, 8, 5, 3, 3, 2, 2)

CLICK_RELATED_CHANCE = 0.42      # when the page offers related searches
RETYPE_CHANCE = 0.46             # otherwise a follow-up typed in the results page's box
OPEN_RESULT_CHANCE = 0.22        # reading includes opening one of the results
INTEREST_SHARE = 0.55            # of new threads, those that start on the account's own interests

# Seconds spent reading a results page, and a result opened from it: log-normal, so mostly near the middle and sometimes long.
READ_MEDIAN, READ_SIGMA, READ_LOW, READ_HIGH = 26.0, 0.55, 7.0, 150.0
RESULT_MEDIAN, RESULT_SIGMA, RESULT_LOW, RESULT_HIGH = 24.0, 0.6, 8.0, 160.0
BREAK_MEDIAN, BREAK_SIGMA, BREAK_LOW, BREAK_HIGH = 55.0, 0.6, 15.0, 240.0


def tangent_length(rng=random, longest: int | None = None) -> int:
	length = rng.choices(LENGTHS, weights=LENGTH_WEIGHTS)[0]

	return min(length, longest) if longest else length


def _seconds(median: float, sigma: float, low: float, high: float, rng, speed: float = 1.0) -> float:
	return min(high, max(low, median * speed * math.exp(rng.gauss(0, sigma))))


def read_time(rng=random, speed: float = 1.0) -> float:
	"""How long to spend on a results page before the next move."""
	return _seconds(READ_MEDIAN, READ_SIGMA, READ_LOW, READ_HIGH, rng, speed)


def result_time(rng=random, speed: float = 1.0) -> float:
	"""How long to look at a result that was opened."""
	return _seconds(RESULT_MEDIAN, RESULT_SIGMA, RESULT_LOW, RESULT_HIGH, rng, speed)


def break_time(rng=random, speed: float = 1.0) -> float:
	"""The gap between one tangent and the next within a sitting."""
	return _seconds(BREAK_MEDIAN, BREAK_SIGMA, BREAK_LOW, BREAK_HIGH, rng, speed)


def opens_a_result(rng=random) -> bool:
	return rng.random() < OPEN_RESULT_CHANCE


def _words(text: str) -> set[str]:
	return {w for w in text.lower().replace("?", " ").split() if len(w) > 2}


def _clean(text: str) -> str:
	return " ".join(text.replace("​", " ").split()).strip()


def usable(options: list[str], asked: set[str], current: str) -> list[str]:
	"""Related searches worth following: not already asked, not the same thing again, not absurdly long."""
	seen, kept = set(), []
	current_words = _words(current)

	for option in options:
		text = _clean(option)
		key = text.lower()

		if not text or key in seen or key in asked or key == current.lower() or len(text.split()) > 10 or len(text) < 3:
			continue

		# A suggestion that adds nothing to what was just asked is not a follow-up.
		if current_words and _words(text) == current_words:
			continue

		seen.add(key)
		kept.append(text)

	return kept


def next_step(options: list[str], asked: set[str], current: str, rng=random) -> tuple[str, str | None]:
	"""What to do after reading the results of `current`: ("click", text), ("type", text) or ("stop", None).

	`options` are the page's related searches and questions, as text. Clicking one searches it exactly as
	written; typing builds a follow-up from one (a person rewords as they type)."""
	options = usable(options, asked, current)

	if not options:
		return ("stop", None) if rng.random() < 0.7 else ("type", None)

	roll = rng.random()

	if roll < CLICK_RELATED_CHANCE:
		return "click", rng.choice(options)

	if roll < CLICK_RELATED_CHANCE + RETYPE_CHANCE:
		return "type", reword(rng.choice(options), current, rng)

	return "stop", None


def reword(option: str, current: str, rng=random) -> str:
	"""The related search as a person types it: usually the same, sometimes shortened or lowercased."""
	text = _clean(option)

	if rng.random() < 0.15 and len(text.split()) > 4:
		text = " ".join(text.split()[:max(3, len(text.split()) - 1)])

	return text.lower() if text.isupper() else text


def starting_points(interests: list[str], trending: list[str], count: int, rng=random) -> list[str]:
	"""Where `count` threads begin: the account's own interests more often than not, the news for the rest."""
	pool_interests = [i for i in interests if i]
	pool_trending = [t for t in trending if t]
	picked: list[str] = []

	while len(picked) < count and (pool_interests or pool_trending):
		use_interest = pool_interests and (not pool_trending or rng.random() < INTEREST_SHARE)
		pool = pool_interests if use_interest else pool_trending
		choice = pool.pop(rng.randrange(len(pool)))

		if choice not in picked:
			picked.append(choice)

	return picked


def plan_sitting(budget: int, rng=random) -> list[int]:
	"""How the searches of a sitting split into tangents: lengths that add up to `budget`, the last one cut short to fit."""
	plan, left = [], budget

	while left > 0:
		length = tangent_length(rng, longest=left)
		plan.append(length)
		left -= length

	return plan
