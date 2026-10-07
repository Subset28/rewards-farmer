"""What is measured in a calibration, and how it becomes a behavior profile.

No window in here, so all of it can be tested. calibrate.py is the window; it feeds key and
mouse events into the recorders below and asks this module for the result.

Typing, recorded phrase by phrase the way a search box is used: type the phrase, fix what you
like, press Enter. A mistake left in is allowed, so how often one is left can be measured.

  gaps and rhythm   share of fast / medium / slow gaps, how often a fast gap follows a fast one
  slips             how often a phrase has a first wrong key, whether it was a neighbouring key
                    or two letters swapped, how many keys went by before it was noticed, how
                    long the pause was before Backspace, how fast the Backspaces came
  corrections       how many slips were fixed before Enter
  pauses            mid-phrase hesitations, the extra beat after a space, the wait before starting

Mouse, trial by trial: click the dot, then the rectangle.

  Fitts' law        MT = a + b * ID, as before
  reaction          from the rectangle appearing to the pointer starting to move
  hover             from the pointer reaching the rectangle to the click
  dwell             how long the button is held down
  misses, overshoot how often a click missed, how often the pointer left the target and came back
  straightness      how far the path wandered compared with a straight line

Everything optional is clamped to what a person could be before it is stored (behavior.py), so
a bad recording cannot produce an absurd profile.
"""

import math
import random
import statistics

import behavior
import search_behavior

# A phrase typed the way a search is: lower case, no punctuation.
PHRASES = (
	"best hiking trails near me this weekend",
	"how to cook salmon in an air fryer",
	"weather forecast for the next ten days",
	"cheap flights to lisbon in march 2026",
	"what time does the pharmacy close tonight",
	"easy weeknight dinner ideas for two people",
	"how to fix a leaky kitchen faucet",
	"who won the basketball game last night",
	"best budget laptops for college students",
	"how long to boil eggs for a soft yolk",
	"used car prices for a honda civic",
	"movies coming out next month worth watching",
	"how to remove a red wine stain from carpet",
	"closest grocery store open late",
	"top rated pizza places downtown",
	"how many calories in a banana",
)

PRACTICE = "type this to warm up"

# Keys that are not typing: modifiers, navigation and the like.
NOT_TYPING = {
	"Shift_L", "Shift_R", "Control_L", "Control_R", "Alt_L", "Alt_R", "Caps_Lock", "Num_Lock", "Scroll_Lock",
	"Win_L", "Win_R", "Meta_L", "Meta_R", "Super_L", "Super_R", "Menu", "ISO_Level3_Shift", "Mode_switch",
	"Tab", "Escape", "Left", "Right", "Up", "Down", "Home", "End", "Prior", "Next",
	"Insert", "Delete", "Pause", "Print", "App",
}
ENTER_KEYS = ("Return", "KP_Enter")

TYPING_GAP_MAX = 0.7        # a gap longer than this is a pause, not rhythm
HESITATION_MAX = 3.0        # longer than this is a distraction, not a hesitation
MIN_INTERVALS = 60
MIN_PHRASES = 8
MIN_TRIALS = 10
MIN_FITTS_SPREAD = 0.5      # the difficulty of the trials must vary at least this much (bits)


# ----------------------------------------------------------------------------- typing


class PhraseRecord:
	def __init__(self, target: str, shown_at: float):
		self.target = target
		self.shown_at = shown_at
		self.events: list[tuple[float, str, str]] = []   # (time, "char"|"back"|"enter", char)
		self.final = ""
		self.submitted_at: float | None = None


class TypingRecorder:
	"""The phrases, what has been typed, and a time for every key."""

	def __init__(self, phrases=PHRASES, practice: str | None = PRACTICE, count: int | None = None, shuffle: bool = True, rng=random):
		order = list(phrases)

		if shuffle:
			rng.shuffle(order)

		if count is not None:
			order = order[:count]

		self.items = ([(practice, False)] if practice else []) + [(p, True) for p in order]
		self.position = 0
		self.typed = ""
		self.records: list[PhraseRecord] = []
		self._current: PhraseRecord | None = None
		self._down: set[str] = set()

	@property
	def finished(self) -> bool:
		return self.position >= len(self.items)

	@property
	def target(self) -> str:
		return self.items[self.position][0] if not self.finished else ""

	@property
	def counted(self) -> bool:
		return self.items[self.position][1] if not self.finished else False

	@property
	def real_total(self) -> int:
		return sum(1 for _, counted in self.items if counted)

	@property
	def real_done(self) -> int:
		return sum(1 for _, counted in self.items[:self.position] if counted)

	@property
	def matches(self) -> int:
		count = 0

		for typed, wanted in zip(self.typed, self.target):
			if typed != wanted:
				break

			count += 1

		return count

	def show(self, now: float) -> None:
		"""The current phrase has just appeared."""
		self._current = PhraseRecord(self.target, now)
		self.typed = ""
		self._down.clear()

	def release(self, keysym: str) -> None:
		self._down.discard(keysym)

	def press(self, keysym: str, char: str, now: float) -> str:
		"""One key going down. Returns "typed", "back", "done", "finished" or "ignored"."""
		if self.finished or self._current is None:
			return "ignored"

		if keysym in self._down:
			return "ignored"   # a held key repeating: the first press counted

		self._down.add(keysym)

		if keysym in NOT_TYPING:
			return "ignored"

		record = self._current

		if keysym in ENTER_KEYS:
			# Enter submits, but not an empty or barely started phrase.
			if len(self.typed) < max(3, len(self.target) // 2):
				return "ignored"

			record.events.append((now, "enter", ""))
			record.final = self.typed
			record.submitted_at = now

			if self.counted:
				self.records.append(record)

			self.position += 1
			self._current = None

			return "finished" if self.finished else "done"

		if keysym == "BackSpace":
			self.typed = self.typed[:-1]
			record.events.append((now, "back", ""))

			return "back"

		if len(char) == 1 and char.isprintable():
			self.typed += char
			record.events.append((now, "char", char))

			return "typed"

		return "ignored"


def _neighbor(wrong: str, expected: str) -> bool:
	return wrong.lower() in search_behavior.NEIGHBORS.get(expected.lower(), "")


def analyze_phrase(record: PhraseRecord) -> dict:
	"""Gaps and slips from one phrase's keys."""
	typed = ""
	gaps: list[tuple[float, bool]] = []     # (gap, previous key was a space)
	back_gaps: list[float] = []
	previous_time: float | None = None
	previous_char = ""
	previous_was_back = False
	slip = None
	pending = None                           # a first wrong key waiting to be noticed
	chars_since = 0

	first_key = next((e for e in record.events if e[1] in ("char", "back")), None)
	start_latency = (first_key[0] - record.shown_at) if first_key else None
	opportunities = 0

	for when, kind, char in record.events:
		if kind in ("char", "back") and previous_time is not None:
			gap = when - previous_time

			# Rhythm is key to key only. The wait before a Backspace is the slip being noticed
			# and the wait after one is starting again; both are measured on their own.
			if kind == "back" and previous_was_back:
				back_gaps.append(gap)
			elif kind == "char" and previous_char != "" and gap >= 0:
				gaps.append((gap, previous_char == " "))

		if kind == "char":
			position = len(typed)
			on_track = record.target.startswith(typed)

			if on_track and position < len(record.target):
				opportunities += 1
				expected = record.target[position]

				if char != expected and slip is None:
					following = record.target[position + 1] if position + 1 < len(record.target) else ""
					kind_of = "swap" if (char == following and following != expected) else ("neighbor" if _neighbor(char, expected) else "other")
					slip = kind_of
					pending = {"at": when, "after": 0}
					chars_since = 0
			elif pending is not None:
				chars_since += 1

			typed += char
			previous_char = char
			previous_was_back = False
		elif kind == "back":
			if pending is not None and "noticed_after" not in pending:
				pending["noticed_after"] = chars_since
				pending["pause"] = when - (previous_time if previous_time is not None else when)

			typed = typed[:-1]
			previous_was_back = True
			previous_char = ""
		elif kind == "enter":
			pass

		if kind in ("char", "back"):
			previous_time = when

	enter = next((e for e in record.events if e[1] == "enter"), None)
	last_key_time = previous_time

	return {
		"gaps": gaps,
		"back_gaps": back_gaps,
		"slip": slip,
		"noticed_after": (pending or {}).get("noticed_after"),
		"notice_pause": (pending or {}).get("pause"),
		"fixed": record.final == record.target,
		"start_latency": start_latency,
		"enter_gap": (enter[0] - last_key_time) if enter and last_key_time is not None else None,
		"opportunities": opportunities,
		"chars": len(record.target),
	}


def _median(values, default=None):
	values = [v for v in values if v is not None]

	return statistics.median(values) if values else default


def analyze_typing(records: list[PhraseRecord]) -> dict:
	"""Rhythm and habits from every counted phrase. Raises ProfileError if there is not enough."""
	phrases = [analyze_phrase(r) for r in records]
	pairs = [p for phrase in phrases for p in phrase["gaps"]]
	rhythm = [g for g, _ in pairs if 0 <= g < TYPING_GAP_MAX]

	if len(records) < MIN_PHRASES or len(rhythm) < MIN_INTERVALS:
		raise behavior.ProfileError(
			f"only {len(records)} phrases and {len(rhythm)} usable gaps recorded; "
			f"need {MIN_PHRASES} phrases and {MIN_INTERVALS} gaps"
		)

	fast = sum(g < 0.1 for g in rhythm) / len(rhythm)
	medium = sum(0.1 <= g < 0.2 for g in rhythm) / len(rhythm)
	median_gap = statistics.median(rhythm)

	# How often a quick key follows a quick key, within a phrase.
	persistence_hits = persistence_total = 0

	for phrase in phrases:
		inside = [g for g, _ in phrase["gaps"] if 0 <= g < TYPING_GAP_MAX]

		for earlier, later in zip(inside, inside[1:]):
			if earlier < median_gap:
				persistence_total += 1
				persistence_hits += later < median_gap

	slips = [p for p in phrases if p["slip"]]
	slip_count = len(slips)
	hesitations = [g for g, _ in pairs if TYPING_GAP_MAX <= g < HESITATION_MAX]
	after_space = [g for g, spaced in pairs if spaced and 0 <= g < TYPING_GAP_MAX]
	inside_word = [g for g, spaced in pairs if not spaced and 0 <= g < TYPING_GAP_MAX]
	noticed = [p["noticed_after"] for p in slips if p["noticed_after"] is not None]
	# How many keys went by before the slip was noticed: 1, 2, 3, or 4 and more. Noticing at once counts as 1.
	weights = [sum(1 for n in noticed if max(1, min(n, 4)) == k) for k in (1, 2, 3, 4)] if noticed else None

	minutes = sum(rhythm) / 60
	detail = {
		"slip_rate": slip_count / len(phrases),
		"neighbor_share": (sum(1 for p in slips if p["slip"] == "neighbor") / slip_count) if slip_count else None,
		"swap_share": (sum(1 for p in slips if p["slip"] == "swap") / slip_count) if slip_count else None,
		"correction_rate": (sum(1 for p in slips if p["fixed"]) / slip_count) if slip_count else None,
		"noticed_weights": weights,
		"notice_pause_ms": _ms(_median([p["notice_pause"] for p in slips])),
		"backspace_gap_ms": _ms(_median([g for p in phrases for g in p["back_gaps"]])),
		"hesitation_rate": len(hesitations) / len(rhythm),
		"hesitation_ms": _ms(_median(hesitations)),
		"space_extra_ms": _ms(max(0.0, (_median(after_space, 0.0) - _median(inside_word, 0.0)))) if after_space and inside_word else None,
		"start_latency_ms": _ms(_median([p["start_latency"] for p in phrases])),
		"enter_gap_ms": _ms(_median([p["enter_gap"] for p in phrases])),
		"fast_persistence": (persistence_hits / persistence_total) if persistence_total else None,
		"median_gap_ms": _ms(median_gap),
		"words_per_minute": (len(rhythm) / 5) / minutes if minutes > 0 else None,
		"phrases": len(phrases),
		"gaps": len(rhythm),
	}

	return {"fast_share": round(fast, 4), "medium_share": round(medium, 4), "detail": {k: v for k, v in detail.items() if v is not None}}


def _ms(seconds):
	return None if seconds is None else round(seconds * 1000, 1)


# ----------------------------------------------------------------------------- mouse


class Trial:
	def __init__(self, start: tuple[float, float], rect: tuple[float, float, float, float], shown_at: float):
		self.start = start
		self.rect = rect                       # x, y, width, height
		self.shown_at = shown_at
		self.samples: list[tuple[float, float, float]] = []   # (time, x, y) from the first move
		self.presses: list[tuple[float, float, float]] = []   # (time, x, y)
		self.release_at: float | None = None
		self.done = False

	def inside(self, x: float, y: float) -> bool:
		rx, ry, w, h = self.rect

		return rx <= x <= rx + w and ry <= y <= ry + h

	def move(self, now: float, x: float, y: float) -> None:
		if not self.samples:
			if max(abs(x - self.start[0]), abs(y - self.start[1])) <= 3:
				return

		self.samples.append((now, x, y))

	def press(self, now: float, x: float, y: float) -> bool:
		"""A click. Returns True if it hit the rectangle (the trial then waits for the release)."""
		if not self.samples:
			return False

		self.presses.append((now, x, y))

		return self.inside(x, y)

	def release(self, now: float) -> None:
		if self.presses and self.inside(self.presses[-1][1], self.presses[-1][2]) and self.release_at is None:
			self.release_at = now
			self.done = True


def make_targets(count: int, width: int, height: int, rng=random) -> list[tuple[float, float, float, float]]:
	"""Rectangles that cover easy and hard clicks: near and far, small and large."""
	start = (width / 2, height / 2)
	margin = 80
	targets = []
	longest = math.hypot(width / 2 - margin, height / 2 - margin)

	for n in range(count):
		# Walk the distance from short to long and the size from large to small in a shuffled
		# order, so the difficulty of the trials spreads out instead of clustering.
		distance = 120 + (longest - 120) * ((n * 7 % count) + rng.random()) / count
		w = rng.randint(30, 180)
		h = rng.randint(30, 180)
		angle = rng.uniform(0, 2 * math.pi)
		cx = start[0] + distance * math.cos(angle)
		cy = start[1] + distance * math.sin(angle)
		x = min(max(cx - w / 2, margin), max(margin, width - w - margin))
		y = min(max(cy - h / 2, margin), max(margin, height - h - margin))
		targets.append((x, y, w, h))

	rng.shuffle(targets)

	return targets


def _path_length(points) -> float:
	return sum(math.dist(a, b) for a, b in zip(points, points[1:]))


def analyze_trial(trial: Trial) -> dict | None:
	"""One trial's numbers, or None if it was not completed properly."""
	if not trial.done or not trial.samples or not trial.presses:
		return None

	hit_time, hit_x, hit_y = trial.presses[-1]
	first_time = trial.samples[0][0]
	rx, ry, w, h = trial.rect
	distance = math.hypot(rx + w / 2 - trial.start[0], ry + h / 2 - trial.start[1])
	width_term = (w + h) / 2
	movement_time = hit_time - first_time

	if movement_time <= 0.05 or distance < 50:
		return None

	path = [(x, y) for t, x, y in trial.samples if t <= hit_time]
	straight = math.dist(path[0], (hit_x, hit_y)) if path else 0
	entered_at = None
	entries = 0
	inside_before = False

	for t, x, y in trial.samples:
		if t > hit_time:
			break

		now_inside = trial.inside(x, y)

		if now_inside and not inside_before:
			entries += 1
			entered_at = t if entered_at is None else entered_at

		inside_before = now_inside

	return {
		"distance": distance,
		"width": width_term,
		"id": math.log2(max(2 * distance / max(width_term, 1), 1.0)),
		"movement_time": movement_time,
		"reaction": first_time - trial.shown_at,
		"hover": (hit_time - entered_at) if entered_at is not None else 0.0,
		"dwell": (trial.release_at - hit_time) if trial.release_at is not None else None,
		"misses": len(trial.presses) - 1,
		"overshoot": entries > 1,
		"straightness": (_path_length(path) / straight) if straight > 20 else None,
		"speed": (_path_length(path) / movement_time) if movement_time > 0 else None,
	}


def fit_fitts(trials: list[dict]) -> tuple[float, float, float]:
	xs = [t["id"] for t in trials]
	ys = [t["movement_time"] for t in trials]

	if len(xs) < MIN_TRIALS or max(xs) - min(xs) < MIN_FITTS_SPREAD:
		raise behavior.ProfileError(
			f"{len(xs)} usable clicks that were too alike in difficulty; need {MIN_TRIALS} with a spread of at least {MIN_FITTS_SPREAD} bits"
		)

	x_mean, y_mean = sum(xs) / len(xs), sum(ys) / len(ys)
	b = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, ys)) / sum((x - x_mean) ** 2 for x in xs)
	a = y_mean - b * x_mean

	if b <= 0.005 or not behavior.MIN_FITTS_A <= a <= behavior.MAX_FITTS_A or b > behavior.MAX_FITTS_B:
		raise behavior.ProfileError(
			"the time each click took did not follow how far and how small the target was, so the mouse speed "
			"could not be measured. Try the mouse part again and move the way you normally do."
		)

	tss = sum((y - y_mean) ** 2 for y in ys)
	rss = sum((y - (a + b * x)) ** 2 for x, y in zip(xs, ys))

	return a, b, (1.0 if tss == 0 else 1.0 - rss / tss)


def analyze_mouse(trials: list[Trial]) -> dict:
	"""Fitts' law and the rest from the completed trials. Raises ProfileError if there is not enough."""
	done = [t for t in (analyze_trial(trial) for trial in trials) if t]
	a, b, r_squared = fit_fitts(done)
	detail = {
		"reaction_ms": _ms(_median([t["reaction"] for t in done])),
		"hover_ms": _ms(_median([t["hover"] for t in done])),
		"dwell_ms": _ms(_median([t["dwell"] for t in done])),
		"dwell_low_ms": _ms(_percentile([t["dwell"] for t in done if t["dwell"] is not None], 0.1)),
		"dwell_high_ms": _ms(_percentile([t["dwell"] for t in done if t["dwell"] is not None], 0.9)),
		"miss_rate": sum(1 for t in done if t["misses"]) / len(done),
		"overshoot_rate": sum(1 for t in done if t["overshoot"]) / len(done),
		"straightness": _median([t["straightness"] for t in done]),
		"speed_px_s": _median([t["speed"] for t in done]),
		"r_squared": round(r_squared, 3),
		"trials": len(done),
	}

	return {"fitts_a": round(a, 4), "fitts_b": round(b, 4), "detail": {k: v for k, v in detail.items() if v is not None}}


def _percentile(values, share):
	values = sorted(v for v in values if v is not None)

	if not values:
		return None

	return values[min(len(values) - 1, int(share * len(values)))]


# ----------------------------------------------------------------------------- the raw data


def raw(records: list[PhraseRecord], trials: list[Trial]) -> dict:
	"""Everything that was recorded, plain enough to write as JSON and analyse again later."""
	return {
		"typing": [
			{
				"target": r.target, "final": r.final, "shown_at": r.shown_at,
				"events": [{"t": t, "kind": kind, "char": char} for t, kind, char in r.events],
			}
			for r in records
		],
		"mouse": [
			{
				"start": list(t.start), "rect": list(t.rect), "shown_at": t.shown_at,
				"samples": [list(sample) for sample in t.samples],
				"presses": [list(press) for press in t.presses], "release_at": t.release_at,
			}
			for t in trials
		],
	}


# ----------------------------------------------------------------------------- the profile


def build(typing: dict, mouse: dict) -> dict:
	"""The arguments behavior.save wants, from the two analyses."""
	return {
		"fast_share": typing["fast_share"],
		"medium_share": typing["medium_share"],
		"fitts_a": mouse["fitts_a"],
		"fitts_b": mouse["fitts_b"],
		"typing_detail": typing["detail"],
		"mouse_detail": mouse["detail"],
	}


def describe(typing: dict, mouse: dict) -> list[str]:
	t, m = typing["detail"], mouse["detail"]
	lines = [
		f"Typing: about {t.get('words_per_minute', 0):.0f} words per minute, median gap {t.get('median_gap_ms', 0):.0f} ms",
		f"  {typing['fast_share']:.0%} of gaps under 0.1 s, {typing['medium_share']:.0%} from 0.1 to 0.2 s",
		f"  a slip in {t.get('slip_rate', 0):.0%} of phrases; {t.get('correction_rate', 1):.0%} of those fixed before Enter",
	]

	if "notice_pause_ms" in t:
		lines.append(f"  a slip is noticed after about {t['notice_pause_ms']:.0f} ms")

	if "hesitation_rate" in t:
		lines.append(f"  a mid-phrase pause every {1 / t['hesitation_rate']:.0f} keys" if t["hesitation_rate"] else "  no mid-phrase pauses")

	lines += [
		f"Mouse: MT = {mouse['fitts_a']:.3f} + {mouse['fitts_b']:.3f} * ID (fit {m.get('r_squared', 0):.2f}, {m.get('trials', 0)} clicks)",
		f"  starts moving {m.get('reaction_ms', 0):.0f} ms after the target appears, hovers {m.get('hover_ms', 0):.0f} ms, holds the button {m.get('dwell_ms', 0):.0f} ms",
		f"  misses {m.get('miss_rate', 0):.0%} of clicks, overshoots {m.get('overshoot_rate', 0):.0%}, path {m.get('straightness', 1):.2f}x a straight line",
	]

	return lines
