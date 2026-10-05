"""Per-account typing and mouse behavior.

Each account gets its own hands. The typing rhythm (how long between keys) and
the pointer's speed (Fitts' law constants a and b) used to be module constants
measured from one person, so every account would have moved and typed
identically. A profile is looked up by account name:

1. data-dir/behavior/<account>.json, written by make_behavior_profile.py from a
   person's own recordings. This is the real thing.
2. For the account that was already running (named "default"), the original
   measured constants, so nothing about it changes.
3. Any other account with no file gets a provisional profile derived from its
   name: stable from run to run, different from every other account's, close to
   typical values. It is a placeholder, not a measurement, and says so in the
   log until a recorded profile replaces it.

A profile file that exists but is unreadable or out of range is not silently
swapped for the defaults. It falls back to the provisional profile with a
warning, so a typo cannot make two accounts identical without anyone noticing.
"""

import hashlib
import json
import logging
import os
import random
from dataclasses import dataclass

from constants import USER_DATA_DIR

logger = logging.getLogger(__name__)

PROFILE_DIR = os.path.join(USER_DATA_DIR, "behavior")

# The measurement the original constants came from: 267 keypress intervals and
# an 18-trial Fitts' law calibration of the person the first account belongs to.
BASELINE_KEYS = (0.5019, 0.4195)
BASELINE_FITTS = (0.4007, 0.1454)

# Interval buckets in seconds. Pauses beyond the last bucket are not modeled.
TYPING_BUCKETS = ((0.0, 0.1), (0.1, 0.2), (0.2, 0.7))

# Bounds a profile must sit inside to be believable as a person's. Typing
# shares are probabilities; the Fitts' law constants are in seconds.
# A measured intercept can be slightly negative, because the line only has to
# pass near zero, so a is allowed a little below it. Movement time has a floor.
MIN_FITTS_A = -0.5
MAX_FITTS_A = 2.0
MAX_FITTS_B = 1.0
MIN_FAST_SHARE = 0.05
MIN_SLOW_SHARE = 0.005


class ProfileError(ValueError):
	"""A behavior profile that cannot be used."""


@dataclass(frozen=True)
class Behavior:
	name: str
	source: str  # "recorded", "baseline" or "provisional"
	fast_share: float  # keypress intervals under 0.1s
	medium_share: float  # 0.1s to 0.2s
	fitts_a: float
	fitts_b: float

	@property
	def slow_share(self) -> float:
		return 1.0 - self.fast_share - self.medium_share

	@property
	def typing_weights(self) -> tuple[float, float, float]:
		return (self.fast_share, self.medium_share, self.slow_share)


def validate(fast_share: float, medium_share: float, fitts_a: float, fitts_b: float) -> None:
	"""Raise ProfileError unless these could be a person's."""
	for label, value in (("fast_share", fast_share), ("medium_share", medium_share), ("fitts_a", fitts_a), ("fitts_b", fitts_b)):
		if not isinstance(value, (int, float)) or isinstance(value, bool) or value != value:
			raise ProfileError(f"{label} is not a number: {value!r}")

	if fast_share < MIN_FAST_SHARE or medium_share < 0 or fast_share + medium_share > 1 - MIN_SLOW_SHARE:
		raise ProfileError(
			f"typing shares fast={fast_share}, medium={medium_share} are not a plausible split "
			f"(fast at least {MIN_FAST_SHARE}, and some intervals must be slower than 0.2s)"
		)

	if not MIN_FITTS_A <= fitts_a <= MAX_FITTS_A or not 0 < fitts_b <= MAX_FITTS_B:
		raise ProfileError(f"Fitts' law a={fitts_a}, b={fitts_b} are outside {MIN_FITTS_A} <= a <= {MAX_FITTS_A}, 0 < b <= {MAX_FITTS_B}")


def baseline(name: str = "default") -> Behavior:
	fast, medium = BASELINE_KEYS
	a, b = BASELINE_FITTS

	return Behavior(name, "baseline", fast, medium, a, b)


def provisional(name: str) -> Behavior:
	"""A stable placeholder for an account with no recorded profile.

	Seeded from the name, so the same account gets the same values every run and
	two accounts get different ones. Close to the baseline, not a copy of it.
	"""
	seed = int.from_bytes(hashlib.sha256(f"behavior:{name.lower()}".encode()).digest()[:8], "big")
	rng = random.Random(seed)

	fast_base, medium_base = BASELINE_KEYS
	a_base, b_base = BASELINE_FITTS

	fast = fast_base + rng.uniform(-0.10, 0.10)
	medium = medium_base + rng.uniform(-0.08, 0.08)
	# Keep a real slow tail whatever the draw.
	medium = min(medium, 0.99 - fast)
	a = a_base * rng.uniform(0.80, 1.25)
	b = b_base * rng.uniform(0.80, 1.25)

	validate(fast, medium, a, b)

	return Behavior(name, "provisional", round(fast, 4), round(medium, 4), round(a, 4), round(b, 4))


def profile_path(name: str) -> str:
	return os.path.join(PROFILE_DIR, f"{name}.json")


def from_json(name: str, raw: dict) -> Behavior:
	try:
		typing = raw["typing"]
		mouse = raw["mouse"]
		fast, medium = float(typing["fast_share"]), float(typing["medium_share"])
		a, b = float(mouse["fitts_a"]), float(mouse["fitts_b"])
	except (KeyError, TypeError, ValueError) as exc:
		raise ProfileError(f"missing or malformed field: {exc!r}") from exc

	validate(fast, medium, a, b)

	return Behavior(name, "recorded", fast, medium, a, b)


def load(name: str) -> Behavior:
	"""The behavior profile for an account; never raises."""
	path = profile_path(name)

	try:
		with open(path, encoding="utf-8") as handle:
			raw = json.load(handle)
	except FileNotFoundError:
		raw = None
	except (OSError, ValueError) as exc:
		logger.warning("%s: the behavior profile %s cannot be read (%s). Using a provisional one.", name, path, exc)

		return provisional(name)

	if raw is not None:
		try:
			return from_json(name, raw)
		except ProfileError as exc:
			logger.warning("%s: the behavior profile %s is not usable (%s). Using a provisional one.", name, path, exc)

			return provisional(name)

	if name.lower() == "default":
		return baseline(name)

	logger.warning(
		"%s: no recorded behavior profile at %s, so this account types and moves with a provisional one. "
		"Record yours with src/typing_test.py and src/fitts_law.py, then src/make_behavior_profile.py.",
		name, path,
	)

	return provisional(name)


def save(name: str, fast_share: float, medium_share: float, fitts_a: float, fitts_b: float, notes: dict | None = None) -> str:
	"""Write a recorded profile; returns its path. Validates before writing."""
	validate(fast_share, medium_share, fitts_a, fitts_b)

	os.makedirs(PROFILE_DIR, exist_ok=True)
	path = profile_path(name)

	with open(path, "w", encoding="utf-8") as handle:
		json.dump(
			{
				"typing": {"fast_share": fast_share, "medium_share": medium_share},
				"mouse": {"fitts_a": fitts_a, "fitts_b": fitts_b},
				"notes": notes or {},
			},
			handle,
			indent="\t",
		)

	return path


def typing_shares(intervals: list[float]) -> tuple[float, float]:
	"""(fast_share, medium_share) from keypress intervals in seconds.

	The same derivation the original constants came from: the share of
	intervals under 0.1s and from 0.1s to 0.2s, out of those under 0.7s. Longer
	gaps are pauses to think, not typing rhythm, and are left out.
	"""
	typed = [i for i in intervals if 0 <= i < TYPING_BUCKETS[-1][1]]

	if len(typed) < 30:
		raise ProfileError(f"only {len(typed)} usable keypress intervals, need at least 30 (a few minutes of typing)")

	fast = sum(i < 0.1 for i in typed) / len(typed)
	medium = sum(0.1 <= i < 0.2 for i in typed) / len(typed)

	return round(fast, 4), round(medium, 4)
