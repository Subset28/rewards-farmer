"""Where search queries come from.

Two backends. `llm` is the default and is unchanged, so nothing about an
existing setup moves. `trends` uses public feeds and needs no account, no
model and no key, which is the difference between running this in five
minutes and installing Ollama first.

    QUERY_SOURCE=trends python src/main.py

The LLM's whole job in this project is producing short strings to type into
Bing, and Bing's own autosuggest answers that question directly.
"""

import logging
import os

import query_history
import query_sources

# llm_utils is imported inside the llm branch rather than here. It imports
# ollama at module scope, so importing it eagerly would make the ollama package
# a hard requirement even for a run that never touches a model, which is the
# opposite of the point. A trends-only install, the Docker image for instance,
# does not ship it.

logger = logging.getLogger(__name__)

LLM = "llm"
TRENDS = "trends"
OPENROUTER = "openrouter"

DEFAULT_SOURCE = LLM

ENV_VAR = "QUERY_SOURCE"


def selected_source() -> str:
	"""Read on each call so a test can change it without reimporting."""
	choice = os.environ.get(ENV_VAR, DEFAULT_SOURCE).strip().lower()

	return choice if choice in (LLM, TRENDS, OPENROUTER) else DEFAULT_SOURCE


def _public_feeds() -> bool:
	"""Whether the public-feed paths apply: trends itself, and openrouter, which falls back to it."""
	return selected_source() in (TRENDS, OPENROUTER)


def search_query_for_task(task_description: str, pick: int = 0, account: str | None = None) -> str:
	"""A query for one "Search on Bing for X" card. `pick` > 0 asks for a different one.

	OpenRouter's free allowance is spent on the daily batch of search queries, so
	the cards use the same public feeds as the trends source.
	"""
	if _public_feeds():
		query = query_sources.query_from_task_description(task_description, pick=pick, avoid=query_history.recent(account))

		if query:
			return query

		# Not a network failure: an unreachable autosuggest still returns the
		# trimmed description. Nothing is left here only when the description was
		# instruction words all the way down, which leaves the sentence itself as
		# the last thing worth typing.
		logger.warning("No searchable words in the task description, using it as written.")

		return task_description.lower()

	import llm_utils

	return llm_utils.get_search_query_from_task_description(task_description)


def related_queries(count: int, account: str | None = None):
	"""`count` queries for the daily search quota, none this account searched lately."""
	if _public_feeds():
		searched = query_history.recent(account)
		queries = []

		if selected_source() == OPENROUTER:
			import openrouter_queries

			queries = openrouter_queries.related_queries(count, account=account, exclude=searched)

		# Whatever OpenRouter did not supply (no key, no budget left, an error,
		# a short reply) comes from the public feeds, so a run is never short.
		if len(queries) < count:
			queries += query_sources.related_queries(count - len(queries), exclude=searched | {q.lower() for q in queries})

		if queries:
			return queries

		logger.warning("No fresh query source reachable, falling back to the wordlist.")

		# nouns.txt is already in the repo for exactly this kind of seed.
		words = [w for w in query_sources.wordlist_queries(count * 3) if query_history.normalize(w) not in searched]

		return words[:count]

	import llm_utils

	return llm_utils.get_related_search_queries(llm_utils.get_random_noun(), num_queries=count)
