"""The bot's pauses between actions, spread the way a person's are.

Waiting a flat random 2 to 4 seconds after every page is a pattern: the edges are hard, the middle is no
likelier than the ends, and a sluggish day looks the same as a brisk one. A person's waits cluster around
a typical value, run long now and then (a distraction), and are all a little longer on a slow day.

For an account that has the "typing" switch on, a pause that used to be uniform(low, high) is instead drawn
from a skewed spread with the same range as its usual body, scaled by that account's pace: the day it is
(stable all day) and a tempo that wanders from one pause to the next, the same way the typing's does. For
every other account it is exactly the old uniform draw.
"""

import math
import random
import time

import clock
import features
import human_model

DISTRACTION_CHANCE = 0.04
DEFAULT_DAY_SD = 0.06
DEFAULT_DRIFT_SD = 0.10
DRIFT_PERSISTENCE = 0.85


class Pace:
	def __init__(self, account: str | None, detail: dict | None = None, rng=random):
		self.account = account
		self.rng = rng
		self.detail = detail or {}
		self.drift = 0.0

	def active(self) -> bool:
		return bool(self.account) and features.enabled("typing", self.account)

	def factor(self) -> float:
		"""How much slower (above 1) or quicker this account is being right now."""
		day = human_model.normal_for(f"day|pace|{(self.account or '').lower()}|{clock.today().isoformat()}") * self.detail.get("day_sd", DEFAULT_DAY_SD)
		spread = self.detail.get("tempo_sd", DEFAULT_DRIFT_SD)
		self.drift = DRIFT_PERSISTENCE * self.drift + math.sqrt(1 - DRIFT_PERSISTENCE ** 2) * spread * self.rng.gauss(0, 1)

		return math.exp(day + self.drift)

	def draw(self, low: float, high: float) -> float:
		"""A wait that used to be uniform(low, high)."""
		low, high = max(0.01, low), max(low, high)

		if high / low < 1.05:
			return low

		# The body of the distribution sits where the old range did: centred on its middle, about
		# 95% of it inside, with a long right tail.
		sigma = math.log(high / low) / 3.5
		wait = ((low + high) / 2) * math.exp(self.rng.gauss(0, sigma)) * self.factor()

		if self.rng.random() < DISTRACTION_CHANCE:
			wait *= self.rng.uniform(1.6, 3.0)

		return min(high * 3.5, max(low * 0.6, wait))

	def sleep(self, low: float, high: float) -> None:
		time.sleep(self.draw(low, high) if self.active() else self.rng.uniform(low, high))
