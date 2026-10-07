"""Pacing and imperfection for the required searches.

Pure functions and small classes with no browser in them, so the behavior can be
tested. Metronome timing, no pauses and flawless typing are what an automated
search loop looks like; this gives the loop the breaks and the occasional slip a
person has.
"""

import math
import random

# Keys next to each key on a QWERTY layout, for a slip of the finger.
NEIGHBORS = {
	"q": "wa", "w": "qeas", "e": "wrsd", "r": "etdf", "t": "ryfg",
	"y": "tugh", "u": "yihj", "i": "uojk", "o": "ipkl", "p": "ol",
	"a": "qwsz", "s": "awedxz", "d": "serfcx", "f": "drtgvc", "g": "ftyhbv",
	"h": "gyujnb", "j": "huikmn", "k": "jiolm", "l": "kop",
	"z": "asx", "x": "zsdc", "c": "xdfv", "v": "cfgb", "b": "vghn",
	"n": "bhjm", "m": "njk",
}

TYPO_RATE = 0.08

# Words shorter than this are left alone: a slip in a three-letter word is a
# different word rather than a typo, and Bing answers it as one.
MIN_WORD_LENGTH = 4


def with_typo(query: str, rate: float = TYPO_RATE, rng=random, neighbor_share: float = 0.5) -> str:
	"""The query, with one slip in it about `rate` of the time.

	Either a neighboring key hit instead of the right one, or two adjacent
	letters swapped. Never the first letter of a word, which people rarely get
	wrong, and never in a short word. The length is unchanged either way.
	"""
	if rng.random() >= rate:
		return query

	positions = []
	start = 0

	for word in query.split(" "):
		if len(word) >= MIN_WORD_LENGTH:
			# Letters after the first, and for a swap also not the last, so the
			# pair (i, i + 1) stays inside the word.
			positions.extend(start + i for i in range(1, len(word) - 1) if word[i].isalpha() and word[i + 1].isalpha())

		start += len(word) + 1

	if not positions:
		return query

	i = rng.choice(positions)
	letters = list(query)

	if rng.random() < neighbor_share and letters[i].lower() in NEIGHBORS:
		wrong = rng.choice(NEIGHBORS[letters[i].lower()])
		letters[i] = wrong.upper() if letters[i].isupper() else wrong
	else:
		letters[i], letters[i + 1] = letters[i + 1], letters[i]

	slipped = "".join(letters)

	# A swap of two equal letters changes nothing. Not worth a retry: it is only
	# a missed slip.
	return slipped


class CoffeeBreaks:
	"""Pauses now and then, so the searches do not arrive at a fixed rhythm.

	Most of the time a short pause after four to nine searches, less often a
	long one after ten to fifteen.
	"""

	def __init__(self, rng=random):
		self.rng = rng
		self._since_break = 0
		self._choose_next()

	def _choose_next(self):
		self._long = self.rng.random() >= 0.8
		self._after = self.rng.randint(10, 15) if self._long else self.rng.randint(4, 9)
		self._since_break = 0

	def before_search(self) -> float:
		"""Seconds to wait before the next search, 0 for none."""
		self._since_break += 1

		# Never before the first search of a run: a break needs something to
		# take a break from.
		if self._since_break <= self._after:
			return 0.0

		pause = self.rng.uniform(45, 90) if self._long else self.rng.uniform(15, 30)
		self._choose_next()

		return pause


def searches_needed(remaining_points: int, points_per_search: float) -> int:
	"""How many searches to reach `remaining_points`, at least one."""
	if points_per_search <= 0:
		return 1

	return max(1, math.ceil(remaining_points / points_per_search))


DEFAULT_ACCOUNT_GAP_MINUTES = "20-60"


def account_gap_seconds(raw: str | None = None, rng=random) -> float:
	"""Seconds to wait between one account's run and the next, from "20-60" (minutes).

	Accounts are worked strictly one after another, never together, and with a
	gap that is not the same every time. A malformed value gives the default
	rather than no gap, since no gap is the thing this exists to avoid.
	"""
	for text in (raw, DEFAULT_ACCOUNT_GAP_MINUTES):
		try:
			low, high = (float(part) for part in (text or "").split("-"))
		except ValueError:
			continue

		if low < 0 or high < 0:
			continue

		return rng.uniform(min(low, high), max(low, high)) * 60

	return 0.0
