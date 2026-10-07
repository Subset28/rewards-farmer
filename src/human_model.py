"""How much a person varies, at three levels, as numbers the bot can draw from.

A bot that is "a bit random" by a fixed amount is as easy to spot as one that is not random at
all. A person is inconsistent in layers:

  from key to key    a run of quick keys, then a slower stretch (the gap each key takes is correlated
                     with the one before it), and some letter pairs are slower than others for them
  from search to search   over a few minutes they speed up and slow down (the tempo wanders)
  from day to day   one day they are brisk, another sluggish (a factor that is the same all day)

Each layer has its own size for each person, and calibration.py measures it from their recording.
This module is the other half: it turns those measurements back into timings. It uses only the
standard library, so it adds nothing to the container. Nothing here touches a browser; it only
answers "how long should the next gap be".

Typing, in log-seconds for the gap before a key:

    ln gap = mu + offset[transition] + day + tempo(search) + wobble(key)

  mu            the person's typical log gap
  offset        how much slower or faster this kind of transition is for them (hand changes,
                the same hand, the same finger, anything involving a space or punctuation)
  day           the daily factor, N(0, day_sd), the same for the whole day
  tempo         wanders from search to search: AR(1) with persistence tempo_phi and spread tempo_sd
  wobble        within a search: AR(1) with persistence gap_phi; its spread itself varies by search

Mouse: a move takes (a + b * ID) times exp(day + drift + noise), the line being the person's Fitts'
law fit and the factor being the scatter that was left around it.
"""

import hashlib
import math
import random
from datetime import date

# QWERTY: the finger each letter is typed with, 0-3 the left hand pinky to index, 4-7 the right hand
# index to pinky. A letter pair is then alternating hands, the same hand, or the same finger.
FINGERS = {}

for _finger, _letters in enumerate(("qaz", "wsx", "edc", "rfvtgb", "yhnujm", "ik", "ol", "p")):
	for _letter in _letters:
		FINGERS[_letter] = _finger

TRANSITIONS = ("alternate", "same_hand", "same_finger", "other")

# Letter pairs that are common in English. Most people type them as a run, so they are quicker; how
# much quicker is learned for each person (offset_common_pair), not assumed.
COMMON_PAIRS = frozenset((
	"th he in er an re on at en nd ti es or te of ed is it al ar st to nt ng se ha as ou io le ve "
	"co me de hi ri ro ic ne ea ra ce li ch ll be ma si om ur"
).split())

MIN_GAP = 0.025
MAX_GAP = 0.7


def transition(previous: str, current: str) -> str:
	"""What kind of move the hands make between two keys."""
	a, b = FINGERS.get(str(previous).lower()), FINGERS.get(str(current).lower())

	if a is None or b is None or len(str(previous)) != 1 or len(str(current)) != 1:
		return "other"

	if a == b:
		return "same_finger"

	return "alternate" if (a < 4) != (b < 4) else "same_hand"


# Short, everyday words. People type them quickly, and they are where fast typists slip most ("teh").
COMMON_WORDS = frozenset((
	"the of and to in is you that it he was for on are as with his they at be this have from or one had by "
	"but not what all were we when your can said there use an each which she do how their if will up other "
	"about out many then them these so some her would make like him into time has look two more go see no "
	"way could people my than first been who its now find long down day did get come made may part best "
	"near me weather where does new used cheap good top free online price how to buy"
).split())


def common_word(word: str) -> bool:
	return word.lower() in COMMON_WORDS


def word_at(text: str, index: int) -> str:
	"""The word that contains position `index` of `text` ("" on a space)."""
	if not 0 <= index < len(text) or text[index] == " ":
		return ""

	start = text.rfind(" ", 0, index) + 1
	end = text.find(" ", index)

	return text[start:end if end != -1 else len(text)]


def slip_weights(text: str, detail: dict) -> list[float]:
	"""How likely a slip is at each position of `text`, relative to the others, for this person.

	People slip where they are quick: on the letter pairs and hand moves they run through, and in everyday
	words. Both are learned from the recording (slip_fast_slope, slip_common_pair_ratio,
	slip_common_word_ratio); a person whose slips did not lean either way gets near-equal weights."""
	slope = detail.get("slip_fast_slope", 0.0)
	pair_ratio = detail.get("slip_common_pair_ratio", 1.0)
	word_ratio = detail.get("slip_common_word_ratio", 1.0)
	sigma = max(0.1, detail.get("within_sigma", 0.35))
	common_offset = detail.get("offset_common_pair", 0.0)
	weights = []

	for index, char in enumerate(text):
		if index == 0 or not char.isalpha():
			weights.append(1.0)

			continue

		previous = text[index - 1]
		kind = transition(previous, char)
		quick = -(detail.get(f"offset_{kind}", 0.0) + (common_offset if common_pair(previous, char) and kind != "other" else 0.0)) / sigma
		weight = math.exp(slope * quick)

		if common_pair(previous, char):
			weight *= pair_ratio

		if common_word(word_at(text, index)):
			weight *= word_ratio

		weights.append(weight)

	return weights


def common_pair(previous: str, current: str) -> bool:
	return (str(previous) + str(current)).lower() in COMMON_PAIRS


def normal_for(seed_text: str) -> float:
	"""A standard normal number that depends only on the text, for a daily factor that is stable."""
	digest = hashlib.sha256(seed_text.encode()).digest()

	return random.Random(int.from_bytes(digest[:8], "big")).gauss(0, 1)


def day_factor(account: str, sd: float, today: date | None = None, salt: str = "day") -> float:
	"""exp(N(0, sd)), the same for an account all day and different from day to day."""
	today = today or date.today()

	return math.exp(sd * normal_for(f"{salt}|{account.lower()}|{today.isoformat()}"))


def _ar1(previous: float, persistence: float, spread: float, rng) -> float:
	"""The next value of a wandering quantity with a stationary spread of `spread`."""
	return persistence * previous + math.sqrt(max(0.0, 1 - persistence ** 2)) * spread * rng.gauss(0, 1)


class TypingRhythm:
	"""The gaps between keys for one person, across the searches of one run."""

	def __init__(self, detail: dict, rng=random, day_z: float = 0.0):
		self.rng = rng
		self.mu = detail["log_gap_mu"]
		self.sigma = detail.get("within_sigma", 0.35)
		self.sigma_sd = detail.get("sigma_sd", 0.05)
		self.tempo_sd = detail.get("tempo_sd", 0.12)
		self.tempo_phi = detail.get("tempo_phi", 0.3)
		self.gap_phi = detail.get("gap_phi", 0.25)
		self.offsets = {name: detail.get(f"offset_{name}", 0.0) for name in TRANSITIONS}
		self.common = detail.get("offset_common_pair", 0.0)
		self.day = day_z * detail.get("day_sd", 0.0)
		self.tempo = rng.gauss(0, self.tempo_sd)
		self.wobble = 0.0
		self.spread = self.sigma

	def start_search(self) -> None:
		"""A new search: the tempo has moved on a little, and this search has its own steadiness."""
		self.tempo = _ar1(self.tempo, self.tempo_phi, self.tempo_sd, self.rng)
		self.spread = max(0.08, self.rng.gauss(self.sigma, self.sigma_sd))
		self.wobble = self.rng.gauss(0, self.spread)

	def next_gap(self, previous: str, current: str) -> float:
		"""Seconds before `current` is pressed, after `previous`."""
		self.wobble = _ar1(self.wobble, self.gap_phi, self.spread, self.rng)
		kind = transition(previous, current)
		pair = self.common if kind != "other" and common_pair(previous, current) else 0.0
		log_gap = self.mu + self.offsets[kind] + pair + self.day + self.tempo + self.wobble

		return min(MAX_GAP, max(MIN_GAP, math.exp(log_gap)))


class MoveTempo:
	"""How much longer or shorter than the Fitts' law line the next pointer move takes."""

	DRIFT_PERSISTENCE = 0.9

	def __init__(self, detail: dict, rng=random, day_z: float = 0.0):
		self.rng = rng
		sd = detail.get("move_rel_sd", 0.15)
		persistent = max(0.0, min(1.0, detail.get("move_phi", 0.2)))
		self.drift_sd = sd * math.sqrt(persistent)
		self.noise_sd = sd * math.sqrt(1 - persistent)
		self.day = day_z * detail.get("day_sd", 0.0)
		self.drift = rng.gauss(0, self.drift_sd)

	def next_factor(self) -> float:
		self.drift = _ar1(self.drift, self.DRIFT_PERSISTENCE, self.drift_sd, self.rng)

		return math.exp(self.day + self.drift + self.rng.gauss(0, self.noise_sd))


def lognormal_ms(mu: float, sigma: float, rng=random, low: float = 0.0, high: float = 10_000.0, offset: float = 0.0) -> float:
	"""A duration in milliseconds from a log-normal fit (exp(N(mu, sigma)) - offset), kept inside a believable range."""
	return min(high, max(low, math.exp(rng.gauss(mu, sigma)) - offset))
