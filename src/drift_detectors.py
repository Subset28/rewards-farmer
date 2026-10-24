"""Detectors that look at how a sitting changes over time and how typing errors are placed.

Read-only analysis of recordings. Nothing here changes the generator.

    session_drift     does typing speed trend across a sitting (fatigue, warm-up, learning)?
    slip_placement    do slips land on quick keys and common pairs more than chance?
    cross_modal       do typing speed and pointer speed move together across sittings?

Each returns plain numbers so they can be compared between a real recording and a simulated one.
"""

import math
import statistics

import calibration


def _copied(records):
	return sorted((r for r in records if not r.compose and r.target), key=lambda r: r.shown_at)


def _slope(values: list[float]) -> tuple[float, float]:
	"""Least-squares slope of values against their index, and its t statistic."""
	n = len(values)

	if n < 4:
		return 0.0, 0.0

	xs = range(n)
	x_mean, y_mean = (n - 1) / 2, statistics.mean(values)
	sxx = sum((x - x_mean) ** 2 for x in xs)
	slope = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, values)) / sxx
	residuals = [y - (y_mean + slope * (x - x_mean)) for x, y in zip(xs, values)]
	se = math.sqrt(sum(r * r for r in residuals) / (n - 2) / sxx) if sxx else 0.0

	return slope, (slope / se if se else 0.0)


def session_drift(records) -> dict | None:
	"""Trend in per-phrase median log gap across one sitting. A positive slope means slowing down over time."""
	phrases = [calibration.analyze_phrase(r) for r in _copied(records)]
	per_phrase = []

	for p in phrases:
		gaps = [g for g, _, _ in p["gaps"] if 0 < g < calibration.TYPING_GAP_MAX]

		if len(gaps) >= calibration.MIN_PHRASES_FOR_RHYTHM:
			per_phrase.append(math.log(statistics.median(gaps)))

	if len(per_phrase) < 8:
		return None

	slope, t = _slope(per_phrase)

	return {"slope": slope, "t": t, "phrases": len(per_phrase), "first_half": statistics.mean(per_phrase[:len(per_phrase) // 2]), "second_half": statistics.mean(per_phrase[len(per_phrase) // 2:])}


def slip_placement(records) -> dict | None:
	"""Share of slips on common letter pairs and quick keys, against the share of all typed keys that are."""
	phrases = [calibration.analyze_phrase(r) for r in _copied(records)]
	spots = [s for p in phrases for s in p["spots"] if s["letter"]]
	slipped = [p["spots"][p["slip_spot"]] for p in phrases if p["slip_spot"] is not None and p["slip_spot"] < len(p["spots"])]
	slipped = [s for s in slipped if s["letter"]]

	if len(spots) < 40 or not slipped:
		return None

	pair_base = sum(s["pair"] for s in spots) / len(spots)
	pair_slip = sum(s["pair"] for s in slipped) / len(slipped)
	fast_base = sum(1 for s in spots if s["gap"] and 0 < s["gap"] < 0.1) / len(spots)
	fast_slip = sum(1 for s in slipped if s["gap"] and 0 < s["gap"] < 0.1) / len(slipped)

	return {
		"slips": len(slipped),
		"pair_share_slip": pair_slip, "pair_share_base": pair_base,
		"fast_share_slip": fast_slip, "fast_share_base": fast_base,
	}


def cross_modal(sittings: list) -> dict | None:
	"""Correlation of median typing gap with median pointer speed, across sittings (needs at least 5 sittings to mean anything)."""
	points = []

	for s in sittings:
		records = calibration.records_from_raw(s.get("typing", []))
		trials = calibration.trials_from_raw(s.get("mouse", []))
		gaps = [g for r in _copied(records) for g, _, _ in calibration.analyze_phrase(r)["gaps"] if 0 < g < calibration.TYPING_GAP_MAX]
		speeds = [t["speed"] for t in (calibration.analyze_trial(tr) for tr in trials) if t and t["speed"]]

		if gaps and speeds:
			points.append((statistics.median(gaps), statistics.median(speeds)))

	if len(points) < 3:
		return None

	xs, ys = [math.log(p[0]) for p in points], [math.log(p[1]) for p in points]
	mx, my = statistics.mean(xs), statistics.mean(ys)
	sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
	sxx = sum((x - mx) ** 2 for x in xs)
	syy = sum((y - my) ** 2 for y in ys)
	r = sxy / math.sqrt(sxx * syy) if sxx and syy else 0.0

	return {"r": r, "sittings": len(points), "min_sittings_for_power": 5}
