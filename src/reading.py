"""How a person reads a page: a plan of scrolls, pauses and small movements of the hand.

The old reading was a handful of scrolls of random size at flat random intervals. A mouse wheel moves in ticks of
one fixed size; a person scrolls a few ticks in a quick burst, stops to read, now and then goes back up a little,
and between bursts the hand drifts a few pixels. This lays that out as a list of steps for a given amount of time
and does not touch the browser; mouse_trajectory.MouseUtils.read_page carries it out.

    ("scroll", dy)         one wheel tick, dy pixels (negative is up)
    ("wait", seconds)      stopping to read
    ("drift", dx, dy)      the hand moving a little without going anywhere
"""

import math
import random

WHEEL_TICK = 100                 # pixels per wheel tick, the usual size
BURST_TICKS = (2, 3, 4, 5, 6)
BURST_WEIGHTS = (22, 30, 24, 14, 10)
TICK_GAP = (0.09, 0.4)           # median seconds and spread between ticks in a burst
PAUSE = (2.1, 0.6)               # median seconds and spread of a stop to read
BACK_UP_CHANCE = 0.12
DRIFT_CHANCE = 0.35
DOUBLE_TICK_CHANCE = 0.1


def _lognormal(median: float, sigma: float, rng, low: float, high: float) -> float:
	return min(high, max(low, median * math.exp(rng.gauss(0, sigma))))


def plan(budget: float, rng=random, speed: float = 1.0, tick: int = WHEEL_TICK) -> list[tuple]:
	"""Steps that fill about `budget` seconds of reading. `speed` above 1 reads more slowly."""
	steps: list[tuple] = []
	spent = 0.0

	while spent < budget:
		ticks = rng.choices(BURST_TICKS, weights=BURST_WEIGHTS)[0]

		for n in range(ticks):
			steps.append(("scroll", tick * (2 if rng.random() < DOUBLE_TICK_CHANCE else 1)))

			if n < ticks - 1:
				gap = _lognormal(TICK_GAP[0], TICK_GAP[1], rng, 0.03, 0.4)
				steps.append(("wait", gap))
				spent += gap

		if rng.random() < BACK_UP_CHANCE:
			steps.append(("wait", _lognormal(0.5, 0.4, rng, 0.2, 1.5)))

			for _ in range(rng.choice((1, 1, 2, 3))):
				steps.append(("scroll", -tick))
				steps.append(("wait", _lognormal(TICK_GAP[0], TICK_GAP[1], rng, 0.03, 0.4)))

		pause = _lognormal(PAUSE[0] * speed, PAUSE[1], rng, 0.5, 14.0)
		steps.append(("wait", pause))
		spent += pause

		if rng.random() < DRIFT_CHANCE:
			steps.append(("drift", rng.choice((-1, 1)) * rng.uniform(4, 60), rng.choice((-1, 1)) * rng.uniform(3, 45)))
			steps.append(("wait", _lognormal(0.4, 0.5, rng, 0.1, 2.0)))
			spent += 0.4

	return steps


def duration(steps: list[tuple]) -> float:
	return sum(step[1] for step in steps if step[0] == "wait")
