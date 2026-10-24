"""Can a classifier tell a person's real pointer moves from simulated ones, given only shape features?

Same method as indistinguishable.py, applied to pointer paths: real moves are described by mouse_fit.features,
simulated moves over the same start/end geometry are described the same way, and a cross-validated linear
discriminant decides how separable the two sets are. 0.5 means no better than chance.

Read-only analysis. The simulated moves are a test set; nothing here feeds back into pointer_path.
"""

import math
import random
import statistics

import indistinguishable
import mouse_fit
import pointer_path

SAMPLE_STEP = mouse_fit.STEP


def _simulated_track(start, end, duration, params, rng, lead):
	"""A pointer_path move sampled at mouse_fit's step, in the same along/sideways form as a recorded one."""
	at = pointer_path.build(start, end, duration, params, rng, lead)
	sx, sy = start
	ex, ey = end
	distance = math.hypot(ex - sx, ey - sy)
	ux, uy = (ex - sx) / distance, (ey - sy) / distance
	along, sideways = [], []
	steps = int(at.total / SAMPLE_STEP) + 1

	for k in range(steps):
		x, y = at(k * SAMPLE_STEP)
		along.append((x - sx) * ux + (y - sy) * uy)
		sideways.append(-(x - sx) * uy + (y - sy) * ux)

	return mouse_fit.Track(at.total, distance, along, sideways, None, (ex, ey), (sx, sy), lead)


def real_features(sittings: list) -> list[list[float]]:
	rows = []

	for session in sittings:
		for trial in session:
			t = mouse_fit.track(trial)

			if t is None:
				continue

			try:
				rows.append(mouse_fit.features(t))
			except (ValueError, ZeroDivisionError, statistics.StatisticsError):
				continue

	return rows


def simulated_features(sittings: list, params: dict, reps: int, rng) -> list[list[float]]:
	"""Features of simulated moves, one per recorded move's geometry, repeated `reps` times."""
	rows = []

	for session in sittings:
		for trial in session:
			t = mouse_fit.track(trial)

			if t is None:
				continue

			for _ in range(reps):
				try:
					sim = _simulated_track(t.start, t.press, t.duration, params, rng, t.lead)
					rows.append(mouse_fit.features(sim))
				except (ValueError, ZeroDivisionError, statistics.StatisticsError):
					continue

	return rows


def tell_apart(sittings: list, params: dict, reps: int = 4, rng=None) -> dict | None:
	"""AUC of telling real moves from simulated ones over the same geometry, or None if there is not enough data."""
	rng = rng or random.Random(0)
	real = real_features(sittings)

	if len(real) < 12:
		return None

	simulated = simulated_features(sittings, params, reps, rng)

	if len(simulated) < len(real):
		return None

	return {
		"auc": indistinguishable.auc(real, simulated),
		"real_moves": len(real),
		"simulated_moves": len(simulated),
		"features": list(mouse_fit.FEATURE_NAMES),
	}
