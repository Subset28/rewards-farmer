"""Can a classifier tell a person's real typing from the bot's, given only the person's own numbers?

"Looks human" is easy to claim and hard to check. This checks it the way a detector would: take the
person's real phrases (their recording) and phrases typed by the bot from the profile made out of that very
recording, describe each phrase by a few features, and train a simple classifier to tell the two apart.

    0.5   the classifier cannot do better than guessing: the bot's typing is statistically the person's
    1.0   it can always tell: the bot is obviously a bot

The features are the ones a page script can see from key events: how fast (the average log gap), how steady
(its spread), whether quick keys come in runs (serial correlation), how long the long gaps run (the tail),
how long a key is held and how often keys overlap. The classifier is a linear discriminant, which sees only
differences in the centre and in how the features move together, so a pass is evidence about those and not
proof against every detector that could exist. It uses numpy, and is meant for the PC the recording is made
on, not the NAS.
"""

import importlib.util
import math
import random
import statistics

import calibration
import human_model
import mouse_fit
import pointer_path

FEATURES = ("mean_log_gap", "sd_log_gap", "lag1", "tail", "hold_ms", "overlap")


def phrase_row(gaps: list[float], holds: list[float], overlaps: float | None) -> list[float] | None:
	"""The feature row of one phrase from its key-to-key gaps (seconds), key holds (ms) and share of keys that overlapped."""
	return _features(gaps, holds, overlaps)


def _features(gaps: list[float], holds: list[float], overlaps: float | None) -> list[float] | None:
	logs = [math.log(g) for g in gaps if 0 < g < calibration.TYPING_GAP_MAX]

	if len(logs) < 8 or len(holds) < 4 or overlaps is None:
		return None

	centre = statistics.mean(logs)
	bottom = sum((x - centre) ** 2 for x in logs)
	lag = sum((a - centre) * (b - centre) for a, b in zip(logs, logs[1:])) / bottom if bottom > 0 else 0.0
	# The tail of the steady typing; the rare thinking pause is measured on its own and differs between copying and composing.
	ordered = sorted(g for g in gaps if 0 < g < calibration.TYPING_GAP_MAX)
	tail = math.log(ordered[int(0.9 * (len(ordered) - 1))] / ordered[len(ordered) // 2])

	return [centre, statistics.pstdev(logs), lag, tail, statistics.median(holds), overlaps]


def real_features(records: list) -> list[list[float]]:
	"""One feature row per recorded phrase that has key holds."""
	rows = []

	for record in records:
		if record.compose or not record.target:
			continue

		gaps = [g for g, _, _ in calibration.analyze_phrase(record)["gaps"]]
		ordered = sorted(record.holds, key=lambda h: h[1])
		holds = [(up - down) * 1000 for _, down, up in ordered if 0 < up - down < 0.5]
		pairs = [(a, b) for a, b in zip(ordered, ordered[1:]) if b[1] - a[1] < calibration.TYPING_GAP_MAX]
		overlaps = (sum(1 for a, b in pairs if b[1] < a[2]) / len(pairs)) if pairs else None
		row = _features(gaps, holds, overlaps)

		if row:
			rows.append(row)

	return rows


def simulated_features(texts: list[str], detail: dict, sittings: int, rng) -> list[list[float]]:
	"""The same features from the bot typing `texts` from a person's numbers, `sittings` times over."""
	import mimic_typing

	rhythm = human_model.TypingRhythm(detail, rng, 0.0)
	rows = []

	for _ in range(sittings):
		for text in texts:
			rhythm.start_search()
			now, timeline = 0.0, [(0.0, text[0])]

			for previous, char in zip(text, text[1:]):
				now += rhythm.next_gap(previous, char)
				timeline.append((now, char))

			events = mimic_typing.KeyboardUtils.key_events(timeline, detail, rng, latency=(0.0, 0.0)) if "hold_mu" in detail else []
			held = {}
			holds, overlaps, pairs, open_keys = [], 0, 0, set()

			for when, down, key in events:
				if down:
					if open_keys:
						overlaps += 1

					pairs += 1
					held[key] = when
					open_keys.add(key)
				else:
					holds.append((when - held[key]) * 1000)
					open_keys.discard(key)

			row = _features([b[0] - a[0] for a, b in zip(timeline, timeline[1:])], holds, (overlaps / max(1, pairs - 1)) if events else 0.0)

			if row:
				rows.append(row)

	return rows


def _direction(a, b):
	import numpy as np

	both = np.vstack([a, b])
	centre, scale = both.mean(axis=0), both.std(axis=0)
	scale[scale == 0] = 1.0
	a, b = (a - centre) / scale, (b - centre) / scale
	# Pooled covariance, nudged so it can always be inverted with few phrases.
	pooled = ((len(a) - 1) * np.cov(a.T) + (len(b) - 1) * np.cov(b.T)) / (len(a) + len(b) - 2) + 0.05 * np.eye(a.shape[1])

	return centre, scale, np.linalg.solve(pooled, a.mean(axis=0) - b.mean(axis=0))


def auc(real: list[list[float]], simulated: list[list[float]], folds: int = 5) -> float:
	"""How well a linear discriminant separates the two sets: 0.5 cannot, 1.0 always can. Needs numpy.

	Scored on phrases the discriminant was not fitted on (cross-validated). Fitted and scored on the same few
	phrases it looks for differences that are only chance, and even identical typists score well above 0.5."""
	import numpy as np

	a, b = np.array(real, dtype=float), np.array(simulated, dtype=float)
	rng = np.random.default_rng(0)
	order_a, order_b = rng.permutation(len(a)), rng.permutation(len(b))
	scores_a, scores_b = np.zeros(len(a)), np.zeros(len(b))

	for fold in range(folds):
		test_a, test_b = order_a[fold::folds], order_b[fold::folds]
		train_a, train_b = np.setdiff1d(order_a, test_a), np.setdiff1d(order_b, test_b)
		centre, scale, direction = _direction(a[train_a], b[train_b])
		scores_a[test_a] = ((a[test_a] - centre) / scale) @ direction
		scores_b[test_b] = ((b[test_b] - centre) / scale) @ direction

	wins = sum((x > scores_b).sum() + 0.5 * (x == scores_b).sum() for x in scores_a)
	score = float(wins / (len(scores_a) * len(scores_b)))

	# Which of the two sets is "real" is not part of the question, only how separable they are.
	return max(score, 1.0 - score)


def tell_apart(records: list, detail: dict, rng=None, sittings: int = 12) -> dict | None:
	"""AUC of telling the person's real phrases from the bot's, or None without enough data or numpy.

	The bot's phrases are typed over the same texts as the recording, so differences in the text itself
	cannot give anything away. The score is for the person's own recording, which is a stand-in for every
	other thing they might type; the more sittings recorded, the less that stand-in is overfitted."""
	if importlib.util.find_spec("numpy") is None:
		return None

	rng = rng or random.Random(0)
	copied = [r for r in records if not r.compose and r.target]
	real = real_features(copied)

	if len(real) < 12 or "log_gap_mu" not in detail:
		return None

	simulated = simulated_features([r.target for r in copied], detail, sittings, rng)

	if len(simulated) < len(real):
		return None

	return {"auc": auc(real, simulated), "real_phrases": len(real), "simulated_phrases": len(simulated)}


def tune_slip_weights(texts: list[str], detail: dict, rounds: int = 6, draws: int = 200) -> dict:
	"""`detail` with slip_pair_weight and slip_word_weight set so the slips the bot makes fall in everyday words and on
	common letter pairs as often, against chance, as the person's did.

	The typo inserter only slips where a person plausibly would (not a word's first letter, not a very short word),
	which squeezes the weights further than they say, so the weights are nudged until what is produced matches."""
	import human_model
	import search_behavior

	result = dict(detail)
	per_char = detail.get("slip_per_char")
	rng = random.Random(11)

	for key, weight_key in (("slip_common_word_ratio", "slip_word_weight"), ("slip_common_pair_ratio", "slip_pair_weight")):
		target = detail.get(key)

		if target is None or per_char is None or not texts:
			continue

		weight = target

		for _ in range(rounds):
			result[weight_key] = weight
			seen = chance = 0.0

			for _ in range(draws):
				for text in texts:
					typed = search_behavior.with_typo(text, 1 - (1 - per_char) ** len(text), rng, neighbor_share=detail.get("neighbor_share", 0.5), weights=human_model.slip_weights(text, result))
					changed = [i for i, (a, b) in enumerate(zip(text, typed)) if a != b]

					if not changed:
						continue

					def hit(i: int) -> bool:
						if key.endswith("word_ratio"):
							return human_model.common_word(human_model.word_at(text, i))

						return i > 0 and human_model.common_pair(text[i - 1], text[i])

					letters = [i for i, c in enumerate(text) if c.isalpha()]
					chance += sum(hit(i) for i in letters) / len(letters)
					seen += hit(changed[0])

			if chance <= 0 or seen <= 0:
				break

			weight = min(8.0, max(0.1, weight * (target / (seen / chance)) ** 0.9))

		result[weight_key] = weight

	return result


def verdict(score: float) -> str:
	if score < 0.62:
		return "cannot be told apart"
	if score < 0.75:
		return "hard to tell apart"
	if score < 0.88:
		return "detectable"

	return "obviously a bot"


# ----------------------------------------------------------------------------- the pointer

FRAME = 1 / 60          # the browser reports pointer movement about once a frame


def mouse_real_features(trials: list) -> list[list[float]]:
	tracks = [mouse_fit.track(trial) for trial in trials]

	return [mouse_fit.features(t) for t in tracks if t]


def trial_like(trial, path, rng=None):
	"""A copy of a recorded move whose samples are a made path instead: same start, landing, timing and target."""
	import calibration as c

	t0, x0, y0 = trial.samples[0]
	hit_time, hit_x, hit_y = trial.presses[-1]
	copy = c.Trial(trial.start, trial.rect, trial.shown_at)
	total = getattr(path, "total", hit_time - t0)
	begin = hit_time - total
	moment = 0.0

	# Fed through the same 3-pixel rule as a recording, so the two are measured the same way.
	while moment < total:
		x, y = path(moment)
		copy.move(begin + moment, x, y)
		moment += FRAME

	copy.presses = [(hit_time, hit_x, hit_y)]
	copy.release_at = trial.release_at
	copy.done = True

	return copy


def mouse_simulated_features(trials: list, detail: dict | None, rng, repeats: int = 12, original: bool = False) -> list[list[float]]:
	"""The same features from paths made for the same moves: from a person's numbers, or by the original generator."""
	import mouse_trajectory

	rows = []

	for position, trial in enumerate(trials):
		if not trial.done or not trial.presses or len(trial.samples) < mouse_fit.MIN_SAMPLES:
			continue

		# A recorded move must not stand in for itself: leave it out of the library it is judged against.
		own = detail
		if detail and detail.get("path_library"):
			own = {**detail, "path_library": [e for e in detail["path_library"] if e.get("id") != position]}

		early = trial.early[0] if trial.early else trial.samples[0]
		x0, y0 = early[1], early[2]
		hit_time, hit_x, hit_y = trial.presses[-1]
		duration = hit_time - trial.samples[0][0]
		lead = trial.samples[0][0] - early[0]

		for _ in range(repeats):
			if original:
				path = mouse_trajectory.get_final_path_from_real_time(duration + lead, (x0, y0), (hit_x, hit_y))
				path.total = duration + lead
			else:
				path = pointer_path.build((x0, y0), (hit_x, hit_y), duration, own, rng, lead)

			made = mouse_fit.track(trial_like(trial, path))

			if made:
				rows.append(mouse_fit.features(made))

	return rows


def mouse_tell_apart(trials: list, detail: dict, rng=None, repeats: int = 12) -> dict | None:
	"""AUC of telling a person's recorded pointer moves from paths built from their numbers (None if too few)."""
	if importlib.util.find_spec("numpy") is None:
		return None

	real = mouse_real_features(trials)

	if len(real) < 12 or "path_a" not in detail:
		return None

	simulated = mouse_simulated_features(trials, detail, rng or random.Random(0), repeats)

	return {"auc": auc(real, simulated), "real_moves": len(real), "simulated_moves": len(simulated)} if len(simulated) >= len(real) else None


# ----------------------------------------------------------------------------- tuning the pointer to match

REFINED = (
	("path_a", 1.3, 10.0, "scale"), ("path_b", 1.3, 16.0, "scale"), ("path_lat_sd", 0.0, 0.25, "scale"),
	("path_lat_bias", -0.15, 0.15, "shift"), ("path_tremor", 0.0, 4.0, "scale"), ("path_over_rate", 0.0, 0.7, "shift"),
)


def _mismatch(real, simulated) -> float:
	"""How far the simulated moves' features are from the recorded ones: centres and spreads, in the recorded spread."""
	import numpy as np

	r, s = np.array(real, dtype=float), np.array(simulated, dtype=float)
	sd = r.std(axis=0)
	sd[sd == 0] = 1.0
	r, s = r / sd, s / sd
	# The centres, measured against how the recorded features vary together (this is what a linear classifier
	# uses), the spreads, and how the features move together.
	covariance = np.cov(r.T) + 0.05 * np.eye(r.shape[1])
	gap = s.mean(axis=0) - r.mean(axis=0)
	centre = float(gap @ np.linalg.solve(covariance, gap))
	spread = float((np.log((s.std(axis=0) + 1e-9) / (r.std(axis=0) + 1e-9)) ** 2).sum())
	together = float(((np.corrcoef(s.T) - np.corrcoef(r.T)) ** 2).sum())

	return centre + 0.5 * spread + 0.5 * together


def refine_mouse(trials: list, detail: dict, rounds: int = 5, repeats: int = 4) -> dict:
	"""The pointer settings, nudged one at a time until paths made from them match the person's recorded ones.

	The estimates read straight off the recording are close but carry small errors (the tremor picks up the
	bow's remains, the overshoot rate misses small ones), and a classifier is sensitive to the sum of them.
	This simulates the person's own moves with the settings, compares their shape features with the recording's
	and keeps any change that brings them closer. The same random numbers are used every time, so a change
	is judged on the setting and not on luck. Returns the settings unchanged if numpy is missing."""
	if importlib.util.find_spec("numpy") is None or "path_a" not in detail:
		return detail

	real = mouse_real_features(trials)

	if len(real) < 12:
		return detail

	def loss(candidate: dict) -> float:
		simulated = mouse_simulated_features(trials, candidate, random.Random(7), repeats)

		return _mismatch(real, simulated) if len(simulated) >= len(real) else float("inf")

	best, best_loss = dict(detail), loss(detail)

	for _ in range(rounds):
		improved = False

		for key, low, high, kind in REFINED:
			if key in ("path_a", "path_b") and best.get("path_shapes"):
				# The person's own shapes are in use: scale those, not the single averages.
				key, low, high = ("path_a_scale", 0.6, 1.7) if key == "path_a" else ("path_b_scale", 0.6, 1.7)

			current = best.get(key, 1.0 if key.endswith("_scale") else pointer_path.DEFAULTS[key])

			for factor in (1.08, 0.93) if kind == "scale" else (0.025, -0.025) if key == "path_over_rate" else (0.006, -0.006):
				value = current * factor if kind == "scale" else current + factor
				value = min(high, max(low, value))

				if value == current:
					continue

				trial_detail = {**best, key: value}
				trial_loss = loss(trial_detail)

				if trial_loss < best_loss - 1e-4:
					best, best_loss, improved = trial_detail, trial_loss, True

					break

		if not improved:
			break

	return best
