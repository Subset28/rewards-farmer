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

FEATURES = ("mean_log_gap", "sd_log_gap", "lag1", "tail", "hold_ms", "overlap")


def _features(gaps: list[float], holds: list[float], overlaps: float | None) -> list[float] | None:
	logs = [math.log(g) for g in gaps if 0 < g < calibration.TYPING_GAP_MAX]

	if len(logs) < 8 or len(holds) < 4 or overlaps is None:
		return None

	centre = statistics.mean(logs)
	bottom = sum((x - centre) ** 2 for x in logs)
	lag = sum((a - centre) * (b - centre) for a, b in zip(logs, logs[1:])) / bottom if bottom > 0 else 0.0
	ordered = sorted(gaps)
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

			events = mimic_typing.KeyboardUtils.key_events(timeline, detail, rng) if "hold_mu" in detail else []
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


def verdict(score: float) -> str:
	if score < 0.62:
		return "cannot be told apart"
	if score < 0.75:
		return "hard to tell apart"
	if score < 0.88:
		return "detectable"

	return "obviously a bot"
