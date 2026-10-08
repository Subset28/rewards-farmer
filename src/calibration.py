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
import human_model
import mouse_fit
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

# Searches typed from the person's own head rather than copied: how long they think before the first
# key and how they pause while making one up is not something copying shows.
# Questions, not instructions, and shown differently from the phrases to copy (calibrate.py), because an instruction in the
# same big type as a phrase gets copied word for word.
COMPOSE_PROMPTS = (
	"What is something you might search for about food or cooking?",
	"What is something you might search for about a trip or a place?",
	"What is something you might search for about sports or a game?",
	"What is something you might want to buy, and would search for?",
	"What is something you might look up about the news or the weather?",
)

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
MIN_PHRASES_FOR_RHYTHM = 6        # gaps in a phrase before it says anything about tempo
PRIOR_DAY_SD = 0.06              # the day-to-day spread assumed until two sittings on different days exist
MIN_FITTS_SPREAD = 0.5      # the difficulty of the trials must vary at least this much (bits)


# ----------------------------------------------------------------------------- typing


class PhraseRecord:
	def __init__(self, target: str, shown_at: float, compose: bool = False):
		self.target = target
		self.compose = compose
		self.shown_at = shown_at
		self.events: list[tuple[float, str, str]] = []   # (time, "char"|"back"|"enter", char)
		self.holds: list[tuple[str, float, float]] = []  # (key, went down, came up): how long each key is held
		self.final = ""
		self.submitted_at: float | None = None


class TypingRecorder:
	"""The phrases, what has been typed, and a time for every key."""

	def __init__(
		self, phrases=PHRASES, practice: str | None = PRACTICE, count: int | None = None, shuffle: bool = True, rng=random,
		compose: tuple = COMPOSE_PROMPTS, compose_count: int | None = None,
	):
		order = list(phrases)

		if shuffle:
			rng.shuffle(order)

		if count is not None:
			order = order[:count]

		items = [(p, True, False) for p in order]
		prompts = list(compose)[:compose_count] if compose_count is not None else list(compose)

		# The made-up ones are spread through the test, not bunched at the end.
		for n, prompt in enumerate(prompts):
			spot = min(len(items), (n + 1) * len(order) // (len(prompts) + 1) + n)
			items.insert(spot, (prompt, True, True))

		self.items = ([(practice, False, False)] if practice else []) + items
		self.position = 0
		self.typed = ""
		self.records: list[PhraseRecord] = []
		self._current: PhraseRecord | None = None
		self._down: set[str] = set()
		self._pressed: dict[str, tuple[str, float]] = {}

	@property
	def finished(self) -> bool:
		return self.position >= len(self.items)

	@property
	def prompt(self) -> str:
		"""What is shown: the phrase to copy, or the request to make one up."""
		return self.items[self.position][0] if not self.finished else ""

	@property
	def compose(self) -> bool:
		return self.items[self.position][2] if not self.finished else False

	@property
	def target(self) -> str:
		"""What must be typed: nothing in particular for a made-up search."""
		return "" if self.compose else self.prompt

	@property
	def counted(self) -> bool:
		return self.items[self.position][1] if not self.finished else False

	@property
	def real_total(self) -> int:
		return sum(1 for item in self.items if item[1])

	@property
	def real_done(self) -> int:
		return sum(1 for item in self.items[:self.position] if item[1])

	@property
	def matches(self) -> int:
		if self.compose:
			return len(self.typed)

		count = 0

		for typed, wanted in zip(self.typed, self.target):
			if typed != wanted:
				break

			count += 1

		return count

	def show(self, now: float) -> None:
		"""The current phrase has just appeared."""
		self._current = PhraseRecord(self.target, now, self.compose)
		self.typed = ""
		self._down.clear()

	def release(self, keysym: str, now: float | None = None) -> None:
		"""A key coming up. Given the time, how long it was held is recorded (a person holds a key for
		tens of milliseconds, and a fast typist presses the next one before letting go of this one)."""
		self._down.discard(keysym)
		pressed = self._pressed.pop(keysym, None)

		if pressed is not None and now is not None and self._current is not None and now >= pressed[1]:
			self._current.holds.append((pressed[0], pressed[1], now))

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
			if len(self.typed) < (3 if self.compose else max(3, len(self.target) // 2)):
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
			self._pressed[keysym] = (char, now)

			return "typed"

		return "ignored"


def _neighbor(wrong: str, expected: str) -> bool:
	return wrong.lower() in search_behavior.NEIGHBORS.get(expected.lower(), "")


def analyze_phrase(record: PhraseRecord) -> dict:
	"""Gaps and slips from one phrase's keys."""
	typed = ""
	gaps: list[tuple[float, str, str]] = []     # (gap, the key before, the key)
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
	spots: list[dict] = []                   # every key typed while still on track: where a slip could have been
	slip_spot = None

	for when, kind, char in record.events:
		if kind in ("char", "back") and previous_time is not None:
			gap = when - previous_time

			# Rhythm is key to key only. The wait before a Backspace is the slip being noticed
			# and the wait after one is starting again; both are measured on their own.
			if kind == "back" and previous_was_back:
				back_gaps.append(gap)
			elif kind == "char" and previous_char != "" and gap >= 0:
				gaps.append((gap, previous_char, char))

		if kind == "char":
			position = len(typed)
			on_track = record.target.startswith(typed)

			if on_track and position < len(record.target):
				opportunities += 1
				expected = record.target[position]
				before = record.target[position - 1] if position > 0 else ""
				spots.append({
					"gap": (when - previous_time) if previous_time is not None and previous_char != "" else None,
					"pair": bool(before) and human_model.common_pair(before, expected),
					"word": human_model.common_word(human_model.word_at(record.target, position)),
					"letter": expected.isalpha() and position > 0,
				})

				if char != expected and slip is None:
					following = record.target[position + 1] if position + 1 < len(record.target) else ""
					kind_of = "swap" if (char == following and following != expected) else ("neighbor" if _neighbor(char, expected) else "other")
					slip = kind_of
					slip_spot = len(spots) - 1
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
		"spots": spots,
		"slip_spot": slip_spot,
		"chars": len(record.target),
	}


def slip_habits(phrases: list[dict]) -> dict:
	"""Where this person's slips fall: on quick keys, on common letter pairs, in everyday words.

	A handful of slips says little, so each figure is pulled towards "no preference" by the number of
	slips seen, and a person with none gets nothing here."""
	slips = [(p, p["spots"][p["slip_spot"]]) for p in phrases if p["slip_spot"] is not None and p["slip_spot"] < len(p["spots"])]

	if not slips:
		return {}

	everything = [spot for p in phrases for spot in p["spots"] if spot["letter"]]
	slip_spots = [spot for _, spot in slips if spot["letter"]]

	if len(everything) < 40 or not slip_spots:
		return {}

	result = {}
	count = len(slip_spots)

	for key, field in (("slip_common_pair_ratio", "pair"), ("slip_common_word_ratio", "word")):
		share = sum(1 for s in everything if s[field]) / len(everything)
		expected = count * share
		result[key] = min(3.0, max(0.4, (sum(1 for s in slip_spots if s[field]) + 1) / (expected + 1)))

	# How much quicker than the rest of the phrase the key before each slip was, in units of the person's own spread.
	logs = [math.log(s["gap"]) for p in phrases for s in p["spots"] if s["gap"] and 0 < s["gap"] < TYPING_GAP_MAX]

	if len(logs) >= 40:
		spread = max(0.1, statistics.pstdev(logs))
		centres = {id(p): _mean(math.log(s["gap"]) for s in p["spots"] if s["gap"] and 0 < s["gap"] < TYPING_GAP_MAX) for p in phrases if p["spots"]}
		seen = [(centres[id(p)] - math.log(spot["gap"])) / spread for p, spot in slips if spot["gap"] and 0 < spot["gap"] < TYPING_GAP_MAX and id(p) in centres]

		if seen:
			result["slip_fast_slope"] = min(1.5, max(-1.0, _mean(seen) * len(seen) / (len(seen) + 5)))

	return result


def _median(values, default=None):
	values = [v for v in values if v is not None]

	return statistics.median(values) if values else default


def key_holds(records: list[PhraseRecord]) -> dict:
	"""How long keys are held down, and how often the next key goes down before the last one is let go."""
	held, overlaps, pairs = [], 0, 0

	for record in records:
		ordered = sorted(record.holds, key=lambda h: h[1])
		held += [(up - down) * 1000 for _, down, up in ordered if 0 < up - down < 0.5]

		for first, second in zip(ordered, ordered[1:]):
			if second[1] - first[1] < TYPING_GAP_MAX:
				pairs += 1
				overlaps += second[1] < first[2]

	result = {}
	spread = _log_spread(held)

	if spread and len(held) >= 40:
		result["hold_mu"], result["hold_sigma"] = spread
		result["hold_ms"] = round(statistics.median(held), 1)

	if pairs >= 40:
		result["rollover_rate"] = overlaps / pairs

	# How a hold depends on how soon the next key goes down.
	points = []

	for record in records:
		ordered = sorted(record.holds, key=lambda h: h[1])

		for first, second in zip(ordered, ordered[1:]):
			gap, hold = second[1] - first[1], (first[2] - first[1]) * 1000

			if 0.01 < gap < TYPING_GAP_MAX and 5 < hold < 500:
				points.append((math.log(gap), math.log(hold)))

	if len(points) >= 60:
		# The person's own (gap, hold) pairs, in ms, thinned evenly to a few hundred: what the bot draws from.
		stride = max(1, len(points) // 400)
		result["hold_pairs"] = [[round(math.exp(x) * 1000, 1), round(math.exp(y), 1)] for x, y in points[::stride]][:400]
		xs, ys = [p[0] for p in points], [p[1] for p in points]
		reference, centre = _mean(xs), _mean(ys)
		spread_x = sum((x - reference) ** 2 for x in xs)

		if spread_x > 0:
			beta = sum((x - reference) * (y - centre) for x, y in points) / spread_x
			beta = min(1.5, max(-0.5, beta))
			residual = [y - (centre + beta * (x - reference)) for x, y in points]
			result.update({
				"hold_alpha": centre, "hold_beta": beta, "hold_gap_ref": reference,
				"hold_resid": min(1.0, max(0.02, statistics.pstdev(residual))),
			})

	return result


def _mean(values):
	values = list(values)

	return sum(values) / len(values) if values else 0.0


def _lag1(series: list[float]) -> float:
	"""How much each value resembles the one before it (0 to 1 here; negative is not modelled)."""
	if len(series) < 3:
		return 0.0

	centre = _mean(series)
	top = sum((a - centre) * (b - centre) for a, b in zip(series, series[1:]))
	bottom = sum((a - centre) ** 2 for a in series)

	return top / bottom if bottom > 0 else 0.0


def fit_rhythm(phrases: list[list[tuple[float, str, str]]]):
	"""A person's key-to-key timing as the numbers human_model.TypingRhythm draws from.

	Returns (the numbers, a function that takes a log gap's move out of it), or None if there is too little.

	The gap is modelled in log seconds as a typical level, plus how much slower or faster the kind of
	move was (hand change, same hand, same finger, other), plus a tempo that differs from search to
	search, plus a wobble within a search that carries over from key to key. The sizes of the last
	two are what make the bot inconsistent by the same amount, and in the same way, as they are.
	"""
	usable = []

	for gaps in phrases:
		logs = [(math.log(g), human_model.transition(p, c), human_model.common_pair(p, c)) for g, p, c in gaps if 0 < g < TYPING_GAP_MAX]

		if len(logs) >= MIN_PHRASES_FOR_RHYTHM:
			usable.append(logs)

	if len(usable) < 4:
		return None

	# 1. How much slower or faster each kind of move is, against the search it came from.
	by_kind: dict[str, list[float]] = {name: [] for name in human_model.TRANSITIONS}

	for logs in usable:
		centre = _mean(l for l, _, _ in logs)

		for value, kind, _ in logs:
			by_kind[kind].append(value - centre)

	shrunk = {kind: _mean(v) * len(v) / (len(v) + 8) for kind, v in by_kind.items()}
	total = sum(len(v) for v in by_kind.values())
	level = sum(shrunk[k] * len(by_kind[k]) for k in by_kind) / total
	offsets = {kind: shrunk[kind] - level for kind in shrunk}

	# 1b. Letter pairs that are common in English (th, er, in...) are typed in a run by most people.
	# How much quicker they are for this person, learned rather than assumed.
	common_hits, other_hits = [], []

	for logs in usable:
		centre = _mean(l for l, _, _ in logs)

		for value, kind, common in logs:
			if kind != "other":
				(common_hits if common else other_hits).append(value - centre - offsets[kind])

	common_offset = 0.0

	if len(common_hits) >= 8 and len(other_hits) >= 8:
		common_offset = (_mean(common_hits) - _mean(other_hits)) * len(common_hits) / (len(common_hits) + 8)
		common_offset = max(-0.6, min(0.3, common_offset))

	def adjust(value: float, kind: str, common: bool) -> float:
		return value - offsets[kind] - (common_offset if common and kind != "other" else 0.0)

	# 2. What is left once the kind of move is taken out: the tempo of each search and the wobble inside it.
	adjusted = [[adjust(value, kind, common) for value, kind, common in logs] for logs in usable]
	tempos = [_mean(a) for a in adjusted]
	residuals = [[x - m for x in a] for a, m in zip(adjusted, tempos)]
	degrees = sum(len(r) for r in residuals) - len(residuals)
	sigma = math.sqrt(sum(x * x for r in residuals for x in r) / max(1, degrees))
	per_search = [math.sqrt(sum(x * x for x in r) / max(1, len(r) - 1)) for r in residuals]
	sampling = _mean(sigma ** 2 / len(r) for r in residuals)
	tempo_var = max(0.0, statistics.pvariance(tempos) - sampling)
	top = sum(a * b for r in residuals for a, b in zip(r, r[1:]))
	bottom = sum(x * x for r in residuals for x in r)

	# People are rarely symmetric: slow keys are usually bunched closer or stretched further than quick ones.
	# How far each side reaches, as a share of the overall spread, shrunk towards 1 when there are few keys.
	flat = [x for r in residuals for x in r]
	above = [x for x in flat if x > 0]
	below = [x for x in flat if x < 0]
	weight = len(flat) / (len(flat) + 60)

	def reach(side: list[float]) -> float:
		if len(side) < 10 or sigma <= 0:
			return 1.0

		return max(0.6, min(1.4, 1 + weight * (math.sqrt(sum(x * x for x in side) / len(side)) / sigma - 1)))

	return {
		"upper_reach": reach(above),
		"lower_reach": reach(below),
		"log_gap_mu": _mean(tempos),
		"within_sigma": sigma,
		"sigma_sd": min(0.25, max(0.02, statistics.pstdev(per_search))),
		"tempo_sd": max(0.02, math.sqrt(tempo_var)),
		# Few searches say little about how long a tempo lasts, so it is pulled towards a modest middle.
		"tempo_phi": min(0.7, max(0.0, 0.5 * _lag1(tempos) + 0.5 * 0.3)) if len(tempos) >= 6 else 0.3,
		"gap_phi": min(0.6, max(0.0, top / bottom)) if bottom > 0 else 0.0,
		**{f"offset_{kind}": offsets[kind] for kind in human_model.TRANSITIONS},
		"offset_common_pair": common_offset,
	}, adjust


def _session_levels(sessions: list[list[dict]], adjust) -> list[float]:
	"""Each sitting's average log gap, to see how far one day differs from another."""
	levels = []

	for phrases in sessions:
		values = [
			adjust(math.log(g), human_model.transition(p, c), human_model.common_pair(p, c))
			for gaps in phrases for g, p, c in gaps if 0 < g < TYPING_GAP_MAX
		]

		if len(values) >= 30:
			levels.append(_mean(values))

	return levels


def copied_the_prompt(record) -> bool:
	"""Whether an own-search item holds the on-screen question (or the older instruction) typed out, not a search of the person's own."""
	typed = " ".join(str(record.final or "").lower().split())

	if any(typed.startswith(old) for old in ("type a search", "think of a search")):
		return True

	return len(typed) >= 12 and " ".join(str(record.target or "").lower().split()).startswith(typed[:20])


def analyze_typing(sessions: list) -> dict:
	"""Rhythm, slips, pauses and how much each of those varies. Raises ProfileError if there is not enough.

	`sessions` is one list of PhraseRecords per sitting (a single list of records is one sitting).
	Sittings on different days are what show how much a person's tempo differs from day to day; with one
	it is assumed to be modest and the profile says so."""
	if sessions and isinstance(sessions[0], PhraseRecord):
		sessions = [sessions]

	everything = [r for session in sessions for r in session]
	copied = [r for r in everything if not r.compose]
	# A made-up search that is the instruction typed out, not a search, says nothing about composing.
	made_up = [r for r in everything if r.compose and not copied_the_prompt(r)]
	phrases = [analyze_phrase(r) for r in copied]
	pairs = [p for phrase in phrases for p in phrase["gaps"]]
	rhythm = [g for g, _, _ in pairs if 0 <= g < TYPING_GAP_MAX]

	if len(copied) < MIN_PHRASES or len(rhythm) < MIN_INTERVALS:
		raise behavior.ProfileError(
			f"only {len(copied)} phrases and {len(rhythm)} usable gaps recorded; "
			f"need {MIN_PHRASES} phrases and {MIN_INTERVALS} gaps"
		)

	fast = sum(g < 0.1 for g in rhythm) / len(rhythm)
	medium = sum(0.1 <= g < 0.2 for g in rhythm) / len(rhythm)
	median_gap = statistics.median(rhythm)

	# How often a quick key follows a quick key, within a phrase (kept for the original model).
	persistence_hits = persistence_total = 0

	for phrase in phrases:
		inside = [g for g, _, _ in phrase["gaps"] if 0 <= g < TYPING_GAP_MAX]

		for earlier, later in zip(inside, inside[1:]):
			if earlier < median_gap:
				persistence_total += 1
				persistence_hits += later < median_gap

	slips = [p for p in phrases if p["slip"]]
	slip_count = len(slips)
	made = [analyze_phrase(r) for r in made_up]
	# Pauses are taken from the made-up searches when there are enough: that is where thinking shows.
	source = made if len(made) >= 3 else phrases
	source_pairs = [p for phrase in source for p in phrase["gaps"]]
	source_rhythm = [g for g, _, _ in source_pairs if 0 <= g < TYPING_GAP_MAX]
	hesitations = [g for g, _, _ in source_pairs if TYPING_GAP_MAX <= g < HESITATION_MAX]
	noticed = [p["noticed_after"] for p in slips if p["noticed_after"] is not None]
	# How many keys went by before the slip was noticed: 1, 2, 3, or 4 and more. Noticing at once counts as 1.
	weights = [sum(1 for n in noticed if max(1, min(n, 4)) == k) for k in (1, 2, 3, 4)] if noticed else None
	starts = [p["start_latency"] for p in (made if len(made) >= 3 else phrases) if p["start_latency"] and p["start_latency"] > 0]

	fitted = fit_rhythm([p["gaps"] for p in phrases])
	rhythm_detail = {}
	day_sd, day_measured = PRIOR_DAY_SD, 0.0

	if fitted:
		rhythm_detail, adjust = fitted
		per_sitting = [[analyze_phrase(r)["gaps"] for r in session if not r.compose] for session in sessions]
		levels = _session_levels(per_sitting, adjust)

		if len(levels) >= 2:
			day_sd, day_measured = min(0.3, max(0.02, statistics.stdev(levels))), 1.0

	after_space = [g for g, p, _ in pairs if p == " " and 0 <= g < TYPING_GAP_MAX]
	inside_word = [g for g, p, _ in pairs if p != " " and 0 <= g < TYPING_GAP_MAX]
	minutes = sum(rhythm) / 60
	mean_length = _mean(len(r.target) for r in copied) or 30.0
	slip_rate = slip_count / len(phrases)
	detail = {
		"slip_rate": slip_rate,
		"slip_per_char": 1 - (1 - min(0.97, slip_rate)) ** (1 / mean_length),
		"neighbor_share": (sum(1 for p in slips if p["slip"] == "neighbor") / slip_count) if slip_count else None,
		"swap_share": (sum(1 for p in slips if p["slip"] == "swap") / slip_count) if slip_count else None,
		"correction_rate": (sum(1 for p in slips if p["fixed"]) / slip_count) if slip_count else None,
		"noticed_weights": weights,
		"notice_pause_ms": _ms(_median([p["notice_pause"] for p in slips])),
		"backspace_gap_ms": _ms(_median([g for p in phrases for g in p["back_gaps"]])),
		"hesitation_rate": (len(hesitations) / len(source_rhythm)) if source_rhythm else None,
		"hesitation_ms": _ms(_median(hesitations)),
		"space_extra_ms": _ms(max(0.0, (_median(after_space, 0.0) - _median(inside_word, 0.0)))) if after_space and inside_word else None,
		"start_latency_ms": _ms(_median(starts)),
		"start_mu": _mean(math.log(v) for v in starts) if len(starts) >= 3 else None,
		"start_sigma": min(1.5, max(0.05, statistics.pstdev([math.log(v) for v in starts]))) if len(starts) >= 3 else None,
		"enter_gap_ms": _ms(_median([p["enter_gap"] for p in phrases])),
		"fast_persistence": (persistence_hits / persistence_total) if persistence_total else None,
		"median_gap_ms": _ms(median_gap),
		"words_per_minute": (len(rhythm) / 5) / minutes if minutes > 0 else None,
		"day_sd": day_sd,
		"day_sd_measured": day_measured,
		**key_holds(everything),
		**slip_habits(phrases),
		"phrases": len(phrases),
		"made_up": len(made),
		"sittings": len(sessions),
		"gaps": len(rhythm),
		**rhythm_detail,
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
		self.early: list[tuple[float, float, float]] = []     # the slower, smaller movements before that
		self.presses: list[tuple[float, float, float]] = []   # (time, x, y)
		self.release_at: float | None = None
		self.done = False

	def inside(self, x: float, y: float) -> bool:
		rx, ry, w, h = self.rect

		return rx <= x <= rx + w and ry <= y <= ry + h

	def move(self, now: float, x: float, y: float) -> None:
		if not self.samples:
			if max(abs(x - self.start[0]), abs(y - self.start[1])) <= 3:
				# Not yet 3 pixels away: the first moments of the hand leaving the spot. Kept, because
				# the shape of the start of a move is part of what a person's moves look like.
				if len(self.early) < 60:
					self.early.append((now, x, y))

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


def _log_spread(values, offset: float = 0.0):
	"""(mean, spread) of the log of positive durations in ms, or None if there are too few."""
	logs = [math.log(v + offset) for v in values if v is not None and v + offset > 0]

	if len(logs) < 5:
		return None

	return _mean(logs), min(1.2, max(0.03, statistics.pstdev(logs)))


def analyze_mouse(sessions: list) -> dict:
	"""Fitts' law, and how much a person's moves scatter around it. Raises ProfileError if there is not enough.

	`sessions` is one list of Trials per sitting (a single list of Trials is one sitting). The scatter is
	what keeps the bot from taking exactly the same time to make the same move every time."""
	if sessions and isinstance(sessions[0], Trial):
		sessions = [sessions]

	analysed = [[t for t in (analyze_trial(trial) for trial in session) if t] for session in sessions]
	done = [t for session in analysed for t in session]
	a, b, r_squared = fit_fitts(done)

	# What is left around the line, in the order it happened: how big, and how much it carries over from one move to the next.
	residuals = [[math.log(t["movement_time"] / (a + b * t["id"])) for t in session if a + b * t["id"] > 0] for session in analysed]
	flat = [r for session in residuals for r in session]
	top = sum(x * y for session in residuals for x, y in zip(session, session[1:]))
	bottom = sum(x * x for session in residuals for x in session)
	move_phi = min(0.8, max(0.0, 0.5 * (top / bottom if bottom > 0 else 0.0) + 0.5 * 0.15))
	levels = [_mean(session) for session in residuals if len(session) >= 8]
	day_sd, day_measured = PRIOR_DAY_SD, 0.0

	if len(levels) >= 2:
		day_sd, day_measured = min(0.3, max(0.02, statistics.stdev(levels))), 1.0

	dwell = _log_spread([t["dwell"] * 1000 for t in done if t["dwell"] is not None])
	hover = _log_spread([t["hover"] * 1000 for t in done], 20)
	reaction = _log_spread([t["reaction"] * 1000 for t in done])
	straight = [t["straightness"] for t in done if t["straightness"] is not None]
	shape = mouse_fit.fit([mouse_fit.track(trial) for session in sessions for trial in session])
	detail = {
		**shape,
		"move_rel_sd": min(0.8, max(0.02, statistics.pstdev(flat))) if len(flat) >= 5 else None,
		"move_phi": move_phi,
		"day_sd": day_sd,
		"day_sd_measured": day_measured,
		"reaction_ms": _ms(_median([t["reaction"] for t in done])),
		"reaction_mu": reaction[0] if reaction else None,
		"reaction_sigma": reaction[1] if reaction else None,
		"hover_ms": _ms(_median([t["hover"] for t in done])),
		"hover_mu": hover[0] if hover else None,
		"hover_sigma": hover[1] if hover else None,
		"dwell_ms": _ms(_median([t["dwell"] for t in done])),
		"dwell_mu": dwell[0] if dwell else None,
		"dwell_sigma": dwell[1] if dwell else None,
		"dwell_low_ms": _ms(_percentile([t["dwell"] for t in done if t["dwell"] is not None], 0.1)),
		"dwell_high_ms": _ms(_percentile([t["dwell"] for t in done if t["dwell"] is not None], 0.9)),
		"miss_rate": sum(1 for t in done if t["misses"]) / len(done),
		"overshoot_rate": sum(1 for t in done if t["overshoot"]) / len(done),
		"straightness": _median(straight),
		"straightness_sd": statistics.pstdev(straight) if len(straight) >= 5 else None,
		"speed_px_s": _median([t["speed"] for t in done]),
		"r_squared": round(r_squared, 3),
		"trials": len(done),
		"sittings": len(sessions),
	}

	return {"fitts_a": round(a, 4), "fitts_b": round(b, 4), "detail": {k: v for k, v in detail.items() if v is not None}}


def _percentile(values, share):
	values = sorted(v for v in values if v is not None)

	if not values:
		return None

	return values[min(len(values) - 1, int(share * len(values)))]


def rhythm_check(sessions: list, detail: dict, rng=None) -> dict | None:
	"""Does a typist made from these numbers type like the person did? None if scipy is not installed.

	Simulates the same phrases several times with the fitted model and compares the spread of its gaps
	and of its tempo from phrase to phrase with the recording (a two-sample Kolmogorov-Smirnov test for the
	gaps: the statistic is the largest gap between the two distributions, so smaller is closer)."""
	try:
		from scipy import stats
	except ImportError:
		return None

	if sessions and isinstance(sessions[0], PhraseRecord):
		sessions = [sessions]

	copied = [r for session in sessions for r in session if not r.compose and r.target]
	observed, observed_tempo = [], []

	for record in copied:
		logs = [math.log(g) for g, _, _ in analyze_phrase(record)["gaps"] if 0 < g < TYPING_GAP_MAX]
		observed += logs

		if len(logs) >= MIN_PHRASES_FOR_RHYTHM:
			observed_tempo.append(_mean(logs))

	if len(observed) < 50 or "log_gap_mu" not in detail:
		return None

	rhythm = human_model.TypingRhythm(detail, rng or random.Random(0), 0.0)
	simulated, simulated_tempo = [], []

	for _ in range(5):
		for record in copied:
			rhythm.start_search()
			logs = [math.log(rhythm.next_gap(a, b)) for a, b in zip(record.target, record.target[1:])]
			simulated += logs

			if len(logs) >= MIN_PHRASES_FOR_RHYTHM:
				simulated_tempo.append(_mean(logs))

	test = stats.ks_2samp(observed, simulated)

	return {
		"ks": float(test.statistic),
		"tempo_sd_recorded": statistics.pstdev(observed_tempo) if len(observed_tempo) > 2 else None,
		"tempo_sd_simulated": statistics.pstdev(simulated_tempo) if len(simulated_tempo) > 2 else None,
	}


# ----------------------------------------------------------------------------- the raw data


def raw(records: list[PhraseRecord], trials: list[Trial], when: str = "") -> dict:
	"""One sitting's recording, plain enough to write as JSON and analyse again later."""
	return {
		"when": when,
		"typing": [
			{
				"target": r.target, "final": r.final, "shown_at": r.shown_at, "compose": r.compose,
				"events": [{"t": t, "kind": kind, "char": char} for t, kind, char in r.events],
				"holds": [[key, down, up] for key, down, up in r.holds],
			}
			for r in records
		],
		"mouse": [
			{
				"start": list(t.start), "rect": list(t.rect), "shown_at": t.shown_at,
				"samples": [list(sample) for sample in t.samples],
				"early": [list(sample) for sample in t.early],
				"presses": [list(press) for press in t.presses], "release_at": t.release_at,
			}
			for t in trials
		],
	}


def records_from_raw(rows: list[dict]) -> list[PhraseRecord]:
	records = []

	for row in rows:
		record = PhraseRecord(row.get("target", ""), row.get("shown_at", 0.0), bool(row.get("compose")))
		record.events = [(e["t"], e["kind"], e.get("char", "")) for e in row.get("events", [])]
		record.holds = [(h[0], h[1], h[2]) for h in row.get("holds", []) if len(h) == 3]
		record.final = row.get("final", "")
		records.append(record)

	return records


def trials_from_raw(rows: list[dict]) -> list[Trial]:
	trials = []

	for row in rows:
		trial = Trial(tuple(row["start"]), tuple(row["rect"]), row["shown_at"])
		trial.samples = [tuple(sample) for sample in row.get("samples", [])]
		trial.early = [tuple(sample) for sample in row.get("early", [])]
		trial.presses = [tuple(press) for press in row.get("presses", [])]
		trial.release_at = row.get("release_at")
		trial.done = trial.release_at is not None
		trials.append(trial)

	return trials


def sittings_from_file(data: dict) -> list[dict]:
	"""The sittings in a raw file, whether it is the old single-sitting shape or the list of sittings."""
	if isinstance(data.get("sessions"), list):
		return [s for s in data["sessions"] if isinstance(s, dict)]

	return [data] if data.get("typing") or data.get("mouse") else []


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


def describe(typing: dict, mouse: dict, check: dict | None = None) -> list[str]:
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

	if "tempo_sd" in t:
		lines += [
			f"  how much you vary: key to key {t['within_sigma']:.0%}, from search to search {t['tempo_sd']:.0%}, "
			f"from day to day {t.get('day_sd', 0):.0%}" + ("" if t.get("day_sd_measured") else " (assumed: record again on another day)"),
			f"  quick keys come in runs ({t.get('gap_phi', 0):.2f}); common letter pairs are {abs(t.get('offset_common_pair', 0)):.0%} "
			+ ("quicker" if t.get("offset_common_pair", 0) < 0 else "slower"),
		]

	if "hold_ms" in t:
		lines.append(
			f"  holds each key down about {t['hold_ms']:.0f} ms" + (f"; presses the next key before letting go in {t['rollover_rate']:.0%} of pairs" if "rollover_rate" in t else "")
		)

	if "slip_common_word_ratio" in t:
		lines.append(
			f"  slips fall in everyday words {t['slip_common_word_ratio']:.1f}x as often as chance, on common letter pairs "
			f"{t.get('slip_common_pair_ratio', 1):.1f}x" + (", and on your quicker keys" if t.get("slip_fast_slope", 0) > 0.15 else "")
		)

	if check and "ks" in check:
		lines.append(
			f"  check: a typist built from these numbers differs from your recording by {check['ks']:.2f} "
			f"({'very close' if check['ks'] < 0.08 else 'close' if check['ks'] < 0.15 else 'rough'}; 0 is identical)"
		)

	lines += [
		f"Mouse: MT = {mouse['fitts_a']:.3f} + {mouse['fitts_b']:.3f} * ID (fit {m.get('r_squared', 0):.2f}, {m.get('trials', 0)} clicks)",
		f"  starts moving {m.get('reaction_ms', 0):.0f} ms after the target appears, hovers {m.get('hover_ms', 0):.0f} ms, holds the button {m.get('dwell_ms', 0):.0f} ms",
		f"  misses {m.get('miss_rate', 0):.0%} of clicks, overshoots {m.get('overshoot_rate', 0):.0%}, path {m.get('straightness', 1):.2f}x a straight line",
	]

	if "move_rel_sd" in m:
		lines.append(
			f"  how much you vary: move to move {m['move_rel_sd']:.0%}, from day to day {m.get('day_sd', 0):.0%}"
			+ ("" if m.get("day_sd_measured") else " (assumed: record again on another day)")
		)

	return lines
