"""Types text the way a person does: the account's own rhythm, and, when switched on, slips that are noticed and fixed."""

import os
import random
import statistics
import urllib.parse
from typing import Iterable
from selenium import webdriver
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.keys import Keys

import clock
import features
import human_model
import search_behavior

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

def _driver_latency() -> tuple[float, float]:
	"""(seconds the driver adds to a gap, seconds it adds to a hold). Measured with typing_events_probe.py latency on the
	NAS: asking for a 150 ms gap and an 80 ms hold gave 157 and 85, steadily (about 2 ms either way). Scheduling
	that much less puts what the page sees on the person's own figures. REWARDS_DRIVER_LATENCY_MS="gap,hold" overrides it."""
	raw = os.environ.get("REWARDS_DRIVER_LATENCY_MS", "9,13")

	try:
		gap, hold = (float(part) / 1000 for part in raw.split(","))
	except ValueError:
		gap, hold = 0.009, 0.013

	return max(0.0, gap), max(0.0, hold)


LATENCY_PAGE = (
	"<!doctype html><title>t</title><input id=b autofocus><script>window.__log=[];"
	"for(const t of ['keydown','keyup'])document.getElementById('b').addEventListener(t,e=>window.__log.push([t,e.key,performance.now()]));"
	"</script>"
)

# Keys overlap only when the next one follows quickly, and no key is held longer than this.
ROLLOVER_ONLY_BELOW = 0.30
MAX_HOLD = 0.34


def rhythm_start_default(rng) -> float:
	"""A beat before the first key for someone whose own wait was not recorded."""
	return rng.uniform(0.6, 1.6)


class KeyboardUtils:
	def __init__(self, driver: webdriver.Edge, behavior=None, account: str | None = None):
		self.driver = driver
		self.account = account
		# The account's own typing rhythm (behavior.py). Without one, the
		# original measured constants above.
		self.weights = list(behavior.typing_weights) if behavior else [
			FIRST_INTERVAL_PROBABILITY, SECOND_INTERVAL_PROBABILITY, THIRD_INTERVAL_PROBABILITY
		]
		# What a recording of this person's own typing measured beyond the rhythm (calibration.py).
		self.recorded = dict(getattr(behavior, "typing_detail", None) or {})
		self._rhythm = None
		self._rhythm_detail = None
		self._latency = None

	def personal(self) -> dict:
		"""The recorded measurements, once the typing feature is on for this account; else nothing."""
		return self.recorded if self.recorded and features.enabled("typing", self.account) else {}

	def slip_weights(self, text: str) -> list[float] | None:
		"""Where in `text` this person is likelier to slip, or None until their recorded habits are switched on."""
		detail = self.personal()

		if not any(key in detail for key in ("slip_fast_slope", "slip_common_pair_ratio", "slip_common_word_ratio")):
			return None

		return human_model.slip_weights(text, detail)

	def slip_settings(self, text: str | None = None) -> tuple[float, float]:
		"""(how often a search has a slip in it, how often that slip is a neighbouring key).

		A recorded person slips at a rate per key typed, so a longer query is likelier to have one than a short one."""
		detail = self.personal()
		neighbor = detail.get("neighbor_share", 0.5)

		if "slip_per_char" in detail and text:
			return 1 - (1 - detail["slip_per_char"]) ** len(text), neighbor

		return detail.get("slip_rate", search_behavior.TYPO_RATE), neighbor

	def _mean_interval(self) -> float:
		# The account's own average gap between keys. Every extra pause below is
		# a multiple of it, so a slow typist stays slow and a fast one fast.
		buckets = (FIRST_INTERVAL, SECOND_INTERVAL, THIRD_INTERVAL)
		total = sum(self.weights) or 1

		return sum(w * (b[0] + b[1]) / 2 for w, b in zip(self.weights, buckets)) / total

	def _plan_correction(self, typed: list, intended: list, rng, detail: dict | None = None):
		"""(first wrong index, keys typed before noticing) or None.

		Only a same-length slip (what with_typo makes) is corrected, so the
		Backspace count is exact and the final text is the intended one.
		"""
		# Only the same text with letters changed (plus the Enter after it): an inserted or
		# dropped character would leave the Backspace count wrong and a stray letter behind.
		if not (len(typed) == len(intended) or (len(typed) == len(intended) + 1 and typed[-1] == Keys.ENTER)):
			return None

		if any(len(str(k)) != 1 for k in typed[:len(intended)]):
			return None

		wrong = [i for i, (a, b) in enumerate(zip(typed, intended)) if a != b]

		detail = detail or {}

		if not wrong or rng.random() >= detail.get("correction_rate", CORRECTION_RATE):
			return None

		first = wrong[0]
		weights = detail.get("noticed_weights")
		how_many = rng.choices((1, 2, 3, 4), weights=weights)[0] if weights else rng.randint(1, 3)
		noticed = min(how_many, len(intended) - 1 - first)

		# Everything wrong must be inside what gets retyped.
		if wrong[-1] > first + noticed:
			return None

		return first, noticed

	def send_keys(self, keys: Iterable[str], intended: str | None = None, rng=None):
		"""Type `keys`: the original timing, or the more human one when the "typing" feature is on for this account.

		The new typing changes what Microsoft sees, so it goes live one account at a time
		(features.py); until it is switched on the original code runs, unchanged."""
		if not features.enabled("typing", self.account):
			return self._send_keys_classic(keys)

		return self._send_keys_human(keys, intended, rng)

	def _send_keys_classic(self, keys: Iterable[str]):
		actions = ActionChains(self.driver, duration=0)

		for key in keys:
			actions.send_keys(key)

			interval = random.choices(
				[FIRST_INTERVAL, SECOND_INTERVAL, THIRD_INTERVAL],
				weights=self.weights
			)[0]

			actions.pause(random.uniform(interval[0], interval[1]))

		actions.perform()

	def rhythm(self, detail: dict, rng) -> human_model.TypingRhythm:
		"""This person's typist, kept for the whole run so the tempo wanders from search to search."""
		if self._rhythm is None or self._rhythm_detail is not detail:
			day = human_model.normal_for(f"day|{(self.account or '').lower()}|{clock.today().isoformat()}")
			self._rhythm = human_model.TypingRhythm(detail, rng, day)
			self._rhythm_detail = detail

		return self._rhythm

	def measured_latency(self, detail: dict, rng) -> tuple[float, float]:
		"""(seconds the driver adds to a gap, seconds it adds to a hold) with THIS person's own timing, measured once per run.

		The driver's delay is not a constant: with keys overlapping and presses close together it differs from a
		slow fixed pattern. So a short sample of the person's own typing is sent to a scratch tab and compared with
		what the page saw. REWARDS_DRIVER_LATENCY_MS="gap,hold" skips the measurement; if it cannot be made the
		figures measured on the NAS are used."""
		if self._latency is not None:
			return self._latency

		# A per-run measurement was tried and scored worse than fixed figures against a real recording (it moved
		# from run to run by more than the delay itself), so it is off unless asked for.
		if os.environ.get("REWARDS_DRIVER_LATENCY_MS") or not os.environ.get("REWARDS_MEASURE_LATENCY"):
			self._latency = _driver_latency()

			return self._latency

		try:
			self._latency = self._measure_latency(detail, rng)
		except Exception:
			self._latency = _driver_latency()

		return self._latency

	def _measure_latency(self, detail: dict, rng) -> tuple[float, float]:
		main = self.driver.current_window_handle
		sample = "the quick brown fox jumps over a lazy dog"
		self.driver.switch_to.new_window("tab")

		try:
			self.driver.get("data:text/html;charset=utf-8," + urllib.parse.quote(LATENCY_PAGE))
			self.driver.find_element("id", "b").click()
			rhythm = human_model.TypingRhythm(detail, rng, 0.0)
			rhythm.start_search()
			now, timeline = 0.4, [(0.4, sample[0])]

			for previous, char in zip(sample, sample[1:]):
				now += rhythm.next_gap(previous, char)
				timeline.append((now, char))

			events = self.key_events(timeline, detail, rng, latency=(0.0, 0.0))
			actions = ActionChains(self.driver, duration=0)
			elapsed = 0.0

			for when, down, key in events:
				actions.pause(max(0.0, when - elapsed))
				actions.key_down(key) if down else actions.key_up(key)
				elapsed = when

			actions.perform()
			log = self.driver.execute_script("return window.__log")
		finally:
			try:
				self.driver.close()
			finally:
				self.driver.switch_to.window(main)

		downs = [e[2] for e in log if e[0] == "keydown"]
		ups: dict[str, list[float]] = {}

		for kind, key, moment in log:
			if kind == "keyup":
				ups.setdefault(key, []).append(moment)

		if len(downs) < len(timeline):
			raise ValueError("the page did not see every key")

		scheduled_up = {}

		for when, down, key in events:
			if not down:
				scheduled_up.setdefault(key, []).append(when)

		gap_extra = [((b - a) / 1000) - (tb[0] - ta[0]) for a, b, ta, tb in zip(downs, downs[1:], timeline, timeline[1:])]
		used: dict[str, int] = {}
		hold_extra = []

		for index, (when, key) in enumerate(timeline):
			n = used.get(key, 0)
			used[key] = n + 1
			hold_extra.append(((ups[key][n] - downs[index]) / 1000) - (scheduled_up[key][n] - when))

		# The middle, not the mean: one slow key in a sample of forty should not move the whole run, and the
		# delay is a few milliseconds, so a ceiling far above the figures measured on the NAS only catches errors.
		clamp = lambda value: min(0.015, max(0.0, value))

		return clamp(statistics.median(gap_extra)), clamp(statistics.median(hold_extra))

	def _type_as_recorded(self, sequence: list, detail: dict, rng, thinking: float | None):
		"""Type `sequence` the way the recorded person does.

		The gap before each key is drawn from their rhythm (hand moves, common pairs, a wandering tempo,
		runs of quick keys, a day that is brisk or sluggish). If their key holds were recorded as well, the
		keys really go down and come up at separate times: held for as long as they hold a key, and, as often
		as they do, the next key going down before the last one is let go. Without holds each key is a
		single press as before.
		"""
		rhythm = self.rhythm(detail, rng)
		rhythm.start_search()
		latency = self.measured_latency(detail, rng) if "hold_mu" in detail else _driver_latency()

		if "start_mu" in detail:
			thinking = human_model.lognormal_ms(detail["start_mu"], detail.get("start_sigma", 0.4), rng, 200, 6000) / 1000
		elif thinking is None:
			thinking = rhythm_start_default(rng)

		hesitation_rate = detail.get("hesitation_rate", HESITATION_RATE)
		timeline: list[tuple[float, object]] = []
		now = thinking
		carry = 0.0
		previous = ""

		for key in sequence:
			if key is None:
				carry += detail["notice_pause_ms"] / 1000 * rng.uniform(0.8, 1.2) if "notice_pause_ms" in detail else 0.5
				continue

			char = key if isinstance(key, str) and len(key) == 1 else ""

			if not timeline:
				gap = 0.0
			elif key == Keys.BACKSPACE and "backspace_gap_ms" in detail:
				gap = detail["backspace_gap_ms"] / 1000 * rng.uniform(0.8, 1.25)
			elif key == Keys.ENTER and "enter_gap_ms" in detail:
				gap = detail["enter_gap_ms"] / 1000 * rng.uniform(0.8, 1.25)
			else:
				gap = max(0.02, rhythm.next_gap(previous, char) - latency[0])

			if timeline and char and rng.random() < hesitation_rate:
				gap += detail["hesitation_ms"] / 1000 * rng.uniform(0.7, 1.4) if "hesitation_ms" in detail else 0.5

			now += gap + carry
			carry = 0.0
			timeline.append((now, key))
			previous = char

		actions = ActionChains(self.driver, duration=0)

		if "hold_mu" not in detail:
			elapsed = 0.0

			for when, key in timeline:
				actions.pause(when - elapsed)
				actions.send_keys(key)
				elapsed = when

			actions.perform()

			return

		events = self.key_events(timeline, detail, rng, latency=latency)
		elapsed = 0.0

		for when, down, key in events:
			actions.pause(max(0.0, when - elapsed))
			actions.key_down(key) if down else actions.key_up(key)
			elapsed = when

		actions.perform()

	@staticmethod
	def key_events(timeline: list, detail: dict, rng, latency: tuple[float, float] | None = None) -> list[tuple[float, bool, object]]:
		"""(time, down?, key) for every press and release, in order, from when each key goes down.

		How long a key is held is drawn from the person's own holds; whether the next key goes down before
		this one comes up is decided by how often they do that. The same key twice in a row never
		overlaps itself (the browser would call that a held-down repeat)."""
		rollover = detail.get("rollover_rate", 0.0)
		learned = "hold_alpha" in detail
		events = []

		for index, (when, key) in enumerate(timeline):
			following = timeline[index + 1][0] - when if index + 1 < len(timeline) else None

			if learned:
				# The person's own hold given how soon the next key follows: overlaps come out of it.
				hold = human_model.hold_seconds(detail, following, rng)
			else:
				hold = human_model.lognormal_ms(detail["hold_mu"], detail.get("hold_sigma", 0.3), rng, 25, 320) / 1000

			if index + 1 < len(timeline):
				gap = following

				if timeline[index + 1][1] == key or gap <= 0:
					hold = min(hold, max(0.02, gap * 0.8))
				elif learned:
					pass
				elif gap > ROLLOVER_ONLY_BELOW:
					# After a pause nobody is still holding the last key.
					hold = min(hold, gap * 0.9)
				elif rng.random() < rollover:
					hold = min(MAX_HOLD, max(hold, gap * rng.uniform(1.05, 1.6)))
				else:
					hold = min(hold, gap * rng.uniform(0.5, 0.9))

			# What the driver adds to a hold is taken off, so the page sees the hold that was drawn.
			hold = max(0.015, hold - (latency if latency is not None else _driver_latency())[1])
			events.append((when, True, key))
			events.append((when + hold, False, key))

		# Releases can fall after later presses, so put everything in time order (a press before a release at a tie).
		return sorted(events, key=lambda e: (e[0], not e[1]))

	def _send_keys_human(self, keys: Iterable[str], intended: str | None = None, rng=None):
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

		detail = self.personal()
		plan = self._plan_correction(keys, list(intended), rng, detail) if intended else None
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

		if "start_latency_ms" in detail:
			# This person's own wait before the first key, a little shorter than measured (part
			# of it was reading the phrase) and never longer than a few seconds.
			thinking = min(3.0, detail["start_latency_ms"] / 1000 * rng.uniform(0.5, 1.0))

		# A person's own recorded rhythm, tempo and key holds, when they have been recorded (calibrate.py).
		if "log_gap_mu" in detail:
			return self._type_as_recorded(sequence, detail, rng, thinking if "start_latency_ms" in detail else None)

		actions.pause(thinking)

		fast = False
		persistence = detail.get("fast_persistence", 0.5)
		hesitation_rate = detail.get("hesitation_rate", HESITATION_RATE)

		for key in sequence:
			if key is None:
				noticing = detail["notice_pause_ms"] / 1000 * rng.uniform(0.8, 1.2) if "notice_pause_ms" in detail else mean * rng.uniform(2.0, 4.0)
				actions.pause(noticing)
				continue

			actions.send_keys(key)

			interval = rng.choices(buckets, weights=self.weights)[0]
			pause = rng.uniform(interval[0], interval[1])

			if key == Keys.BACKSPACE and "backspace_gap_ms" in detail:
				pause = detail["backspace_gap_ms"] / 1000 * rng.uniform(0.8, 1.25)

			# A run of fast keys within a word: speed is not independent per key.
			if fast:
				pause *= 0.6

			fast = (pause < mean * 0.8) if rng.random() < persistence else False

			if key == " ":
				pause += detail["space_extra_ms"] / 1000 * rng.uniform(0.7, 1.3) if "space_extra_ms" in detail else mean * rng.uniform(0.3, 0.9)
				fast = False
			elif rng.random() < hesitation_rate:
				pause += detail["hesitation_ms"] / 1000 * rng.uniform(0.7, 1.4) if "hesitation_ms" in detail else mean * rng.uniform(2.0, 4.0)
				fast = False

			actions.pause(pause)

		actions.perform()
