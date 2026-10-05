import random
from typing import Iterable
from selenium import webdriver
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.keys import Keys

# Measured from recordpress.py + analyze_keypresses.py against this user's
# own typing (267 keypress intervals, keypress_times.txt).
FIRST_INTERVAL = (0.0, 0.1)
SECOND_INTERVAL = (0.1, 0.2)
THIRD_INTERVAL = (0.2, 0.7)

FIRST_INTERVAL_PROBABILITY = 0.5019
SECOND_INTERVAL_PROBABILITY = 0.4195
THIRD_INTERVAL_PROBABILITY = 1 - (FIRST_INTERVAL_PROBABILITY + SECOND_INTERVAL_PROBABILITY)

# Share of slips that are noticed and fixed, and the chance of a brief stall
# in the middle of a word.
CORRECTION_RATE = 0.7
HESITATION_RATE = 0.02

class KeyboardUtils:
	def __init__(self, driver: webdriver.Edge, behavior=None):
		self.driver = driver
		# The account's own typing rhythm (behavior.py). Without one, the
		# original measured constants above.
		self.weights = list(behavior.typing_weights) if behavior else [
			FIRST_INTERVAL_PROBABILITY, SECOND_INTERVAL_PROBABILITY, THIRD_INTERVAL_PROBABILITY
		]

	def _mean_interval(self) -> float:
		# The account's own average gap between keys. Every extra pause below is
		# a multiple of it, so a slow typist stays slow and a fast one fast.
		buckets = (FIRST_INTERVAL, SECOND_INTERVAL, THIRD_INTERVAL)
		total = sum(self.weights) or 1

		return sum(w * (b[0] + b[1]) / 2 for w, b in zip(self.weights, buckets)) / total

	def _plan_correction(self, typed: list, intended: list, rng):
		"""(first wrong index, keys typed before noticing) or None.

		Only a same-length slip (what with_typo makes) is corrected, so the
		Backspace count is exact and the final text is the intended one.
		"""
		if len(typed) < len(intended) or any(len(str(k)) != 1 for k in typed[:len(intended)]):
			return None

		wrong = [i for i, (a, b) in enumerate(zip(typed, intended)) if a != b]

		if not wrong or rng.random() >= CORRECTION_RATE:
			return None

		first = wrong[0]
		noticed = min(rng.randint(1, 3), len(intended) - 1 - first)

		# Everything wrong must be inside what gets retyped.
		if wrong[-1] > first + noticed:
			return None

		return first, noticed

	def send_keys(self, keys: Iterable[str], intended: str | None = None, rng=None):
		"""Type `keys` with human timing.

		`intended` is the text that was meant (without any trailing Enter). When
		`keys` has a slip against it, most slips are noticed a few keys later,
		backspaced and retyped, so the box holds the intended text. Enter, being
		after the text, never lands mid-correction.
		"""
		rng = rng or random
		keys = list(keys)
		actions = ActionChains(self.driver, duration=0)
		mean = self._mean_interval()
		buckets = [FIRST_INTERVAL, SECOND_INTERVAL, THIRD_INTERVAL]
		text_len = len(intended) if intended is not None else len(keys)

		plan = self._plan_correction(keys, list(intended), rng) if intended else None
		# None marks the pause where the typist notices the slip.
		sequence = []
		for index, key in enumerate(keys):
			sequence.append(key)

			if plan and index == plan[0] + plan[1]:
				first, noticed = plan
				# Noticing takes a beat, then the wrong keys come off again.
				sequence.append(None)

				for _ in range(noticed + 1):
					sequence.append(Keys.BACKSPACE)

				for j in range(first, first + noticed + 1):
					sequence.append(intended[j])

		# Longer queries take a longer moment to formulate, mildly.
		thinking = mean * rng.uniform(1.5, 3.0) * (1 + min(text_len, 40) / 100)
		actions.pause(thinking)

		fast = False

		for key in sequence:
			if key is None:
				actions.pause(mean * rng.uniform(2.0, 4.0))
				continue

			actions.send_keys(key)

			interval = rng.choices(buckets, weights=self.weights)[0]
			pause = rng.uniform(interval[0], interval[1])

			# A run of fast keys within a word: speed is not independent per key.
			if fast:
				pause *= 0.6

			fast = (pause < mean * 0.8) if rng.random() < 0.5 else False

			if key == " ":
				pause += mean * rng.uniform(0.3, 0.9)
				fast = False
			elif rng.random() < HESITATION_RATE:
				pause += mean * rng.uniform(2.0, 4.0)
				fast = False

			actions.pause(pause)

		actions.perform()
