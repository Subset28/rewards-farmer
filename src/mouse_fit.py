"""Reading a person's pointer movements back into the numbers pointer_path.py draws from.

calibration.py records every mouse sample of every aimed move. Here each move is laid out along the line from
where it started to where it landed, so its progress along that line (how fast it travels, where the speed
peaks, whether it ran past the end) and its sideways distance from it (the bow, the tremor) can be read off.

    tracks            a move resampled at even steps, as progress and sideways distance
    fit               the numbers pointer_path.build draws from
    features          what a classifier looks at (see indistinguishable.py)
"""

import math
import statistics

STEP = 0.01             # seconds between resampled points
MIN_DISTANCE = 80.0     # a move shorter than this says little about its shape
MIN_SAMPLES = 8
GRID_TAUS = 24


class Track:
	def __init__(self, duration: float, distance: float, along: list[float], sideways: list[float], rect=None, press=None, start=None, lead: float = 0.0):
		self.duration = duration
		self.lead = lead                   # seconds from the first movement to 3 pixels away
		self.distance = distance
		self.along = along                 # progress in pixels along the straight line, at STEP
		self.sideways = sideways           # distance off the line, in pixels
		self.rect = rect
		self.press = press
		self.start = start


def track(trial) -> Track | None:
	"""A recorded move as even-step progress and sideways distance, or None if it is too short or sparse."""
	if not trial.done or not trial.presses:
		return None

	hit_time, hit_x, hit_y = trial.presses[-1]
	early = [(t, x, y) for t, x, y in getattr(trial, "early", []) if t <= hit_time]
	points = early + [(t, x, y) for t, x, y in trial.samples if t <= hit_time]
	lead = (trial.samples[0][0] - early[0][0]) if early and trial.samples else 0.0

	if len(points) < MIN_SAMPLES:
		return None

	points.append((hit_time, hit_x, hit_y))
	t0, x0, y0 = points[0]
	dx, dy = hit_x - x0, hit_y - y0
	distance = math.hypot(dx, dy)
	duration = hit_time - t0

	if distance < MIN_DISTANCE or duration <= 0.1:
		return None

	ux, uy = dx / distance, dy / distance
	along, sideways = [], []
	index = 0

	for step in range(int(duration / STEP) + 1):
		moment = t0 + step * STEP

		while index + 1 < len(points) - 1 and points[index + 1][0] < moment:
			index += 1

		(ta, xa, ya), (tb, xb, yb) = points[index], points[index + 1]
		share = 0.0 if tb == ta else min(1.0, max(0.0, (moment - ta) / (tb - ta)))
		x, y = xa + (xb - xa) * share, ya + (yb - ya) * share
		along.append((x - x0) * ux + (y - y0) * uy)
		sideways.append(-(x - x0) * uy + (y - y0) * ux)

	return Track(duration, distance, along, sideways, trial.rect, (hit_x, hit_y), (x0, y0), lead)


def _speeds(t: Track) -> list[float]:
	return [(b - a) / STEP for a, b in zip(t.along, t.along[1:])]


def features(t: Track) -> list[float]:
	"""Efficiency, bow, where the speed peaks, how fast the start and the landing are, submovements, roughness."""
	path = sum(math.hypot(b - a, d - c) for a, b, c, d in zip(t.along, t.along[1:], t.sideways, t.sideways[1:]))
	speeds = _smooth(_speeds(t), 5)
	peak = max(range(len(speeds)), key=speeds.__getitem__)
	top = max(speeds) or 1.0
	third = max(1, len(speeds) // 3)
	submovements = sum(
		1 for i in range(2, len(speeds) - 2)
		if speeds[i] > speeds[i - 1] and speeds[i] >= speeds[i + 1] and speeds[i] > 0.15 * top
		and min(speeds[max(0, i - 8):i + 1]) < 0.85 * speeds[i]
	)
	rough = statistics.pstdev([b - 2 * a + c for a, b, c in zip(t.sideways, t.sideways[1:], t.sideways[2:])]) if len(t.sideways) > 4 else 0.0

	return [
		min(1.0, t.distance / path) if path else 1.0,
		max(abs(s) for s in t.sideways) / t.distance,
		peak / len(speeds),
		statistics.mean(speeds[:third]) / top,
		statistics.mean(speeds[-third:]) / top,
		float(submovements),
		rough,
	]


FEATURE_NAMES = ("efficiency", "bow", "peak_position", "start_speed", "landing_speed", "submovements", "roughness")


def _smooth(values: list[float], window: int) -> list[float]:
	half = window // 2

	return [statistics.mean(values[max(0, i - half):i + half + 1]) for i in range(len(values))]


def _progress_curve(t: Track, points: int = GRID_TAUS) -> list[float]:
	"""Progress as a share of the distance at evenly spaced fractions of the move's time."""
	last = len(t.along) - 1

	return [min(1.0, max(0.0, t.along[round(k / points * last)] / t.distance)) for k in range(1, points)]


LIBRARY_POINTS = 48
MAX_LIBRARY = 400


def library(tracks: list[Track]) -> list[dict]:
	"""The person's own moves as shapes: progress along the line and distance off it, as shares of the move's
	length, at evenly spaced fractions of its time. pointer_path replays them, rescaled, for new moves."""
	entries = []

	for index, t in enumerate(tracks):
		if not t or len(t.along) < 8:
			continue

		last = len(t.along) - 1

		def sample(values, k):
			position = k / (LIBRARY_POINTS - 1) * last
			low = min(last - 1, int(position))
			mix = position - low

			return values[low] * (1 - mix) + values[low + 1] * mix

		entries.append({
			"id": index,
			"d": round(t.distance, 1),
			"T": round(t.duration, 3),
			"u": [round(sample(t.along, k) / t.distance, 4) for k in range(LIBRARY_POINTS)],
			"l": [round(sample(t.sideways, k) / t.distance, 4) for k in range(LIBRARY_POINTS)],
		})

	return entries[:MAX_LIBRARY]


_TABLES: dict = {}
SHAPE_A = [1.2 + 0.25 * n for n in range(36)]          # 1.2 to 9.95
SHAPE_B = [1.2 + 0.4 * n for n in range(36)]           # 1.2 to 15.2


def _shape_tables(points: int = GRID_TAUS):
	"""Progress curves for every (a, b) on the grid, built once."""
	if points not in _TABLES:
		import pointer_path

		taus = [(k + 1) / points for k in range(points - 1)]
		_TABLES[points] = [
			((a, b), [pointer_path.progress(pointer_path.velocity_table(a, b), tau) for tau in taus])
			for a in SHAPE_A for b in SHAPE_B
		]

	return _TABLES[points]


def best_shape(curve: list[float]) -> tuple[float, float]:
	"""The (a, b) whose progress curve is closest to one recorded move's."""
	best, best_error = (2.7, 3.6), None

	for shape, candidate in _shape_tables(len(curve) + 1):
		error = sum((x - y) ** 2 for x, y in zip(candidate, curve))

		if best_error is None or error < best_error:
			best, best_error = shape, error

	return best


def fit(tracks: list[Track]) -> dict:
	"""The numbers pointer_path.build draws from, from a person's moves. Empty if there are too few moves."""
	original = list(tracks)          # kept with its gaps, so a move's place in the recording is its id
	tracks = [t for t in tracks if t]

	if len(tracks) < 8:
		return {}

	# 1. The speed curve: the tau^(a-1) * (1-tau)^(b-1) whose progress best matches the average recorded progress.
	import pointer_path

	mean_curve = [statistics.mean(c) for c in zip(*(_progress_curve(t) for t in tracks))]
	best, best_error = (2.7, 3.6), None

	for a in [1.3 + 0.15 * n for n in range(0, 40)]:
		for b in [1.3 + 0.15 * n for n in range(0, 48)]:
			table = pointer_path.velocity_table(a, b)
			error = sum((pointer_path.progress(table, (k + 1) / GRID_TAUS) - value) ** 2 for k, value in enumerate(mean_curve))

			if best_error is None or error < best_error:
				best, best_error = (a, b), error

	# 2. The bow: how far off the straight line the move strays at its widest, as a fraction of its length, signed.
	bows = []

	for t in tracks:
		widest = max(t.sideways, key=abs)
		bows.append(widest / t.distance)

	# 3. Tremor: what is left of the sideways wobble once the smooth bow is taken out.
	residual = []

	for t in tracks:
		smooth = _smooth(t.sideways, 15)
		residual += [a - b for a, b in zip(t.sideways[7:-7], smooth[7:-7])]

	# 4. Overshoot: the pointer ran past the end and came back.
	over = sum(1 for t in tracks if max(t.along) > t.distance * 1.02 and t.along[-1] < max(t.along) - 2) / len(tracks)

	# 5. Where the click lands inside the target.
	across, along_offsets = [], []

	for t in tracks:
		if not t.rect or not t.press or not t.start:
			continue

		x, y, w, h = t.rect
		cx, cy = x + w / 2, y + h / 2
		dx, dy = cx - t.start[0], cy - t.start[1]
		length = math.hypot(dx, dy) or 1.0
		ux, uy = dx / length, dy / length
		off_x, off_y = t.press[0] - cx, t.press[1] - cy
		extent_along = abs(ux) * w + abs(uy) * h
		extent_across = abs(uy) * w + abs(ux) * h
		along_offsets.append((off_x * ux + off_y * uy) / extent_along)
		across.append((-off_x * uy + off_y * ux) / extent_across)

	# The shape of each move on its own, to draw from.
	shapes = [best_shape(_progress_curve(t)) for t in tracks]

	result = {
		"path_library": library(original),
		"path_shapes": [[round(a, 2), round(b, 2)] for a, b in shapes][:300],
		"path_a": min(9.95, best[0]),
		"path_b": min(15.2, best[1]),
		"path_lat_bias": max(-0.15, min(0.15, statistics.mean(bows))),
		"path_lat_sd": max(0.0, min(0.25, statistics.pstdev(bows))),
		"path_tremor": max(0.0, min(4.0, statistics.pstdev(residual))) if len(residual) > 20 else 0.35,
		"path_over_rate": over,
		"path_trials": float(len(tracks)),
		"path_lead_ms": max(0.0, min(400.0, 1000 * statistics.median(t.lead for t in tracks))),
	}

	if len(across) >= 8:
		result["end_sd_across"] = max(0.02, min(0.6, statistics.pstdev(across)))
		result["end_bias_along"] = max(-0.4, min(0.6, -statistics.mean(along_offsets)))

	return result
