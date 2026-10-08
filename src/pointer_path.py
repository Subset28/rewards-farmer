"""A pointer path shaped like a particular person's.

The original path (mouse_trajectory.get_final_path_from_real_time) has two shapes a page script can measure
that no person has: its speed is highest at the very first instant and then dies away, where a hand starts
from rest, peaks around the middle of the move and slows towards the target; and it bows by a fixed 20 to 40
pixels whatever the distance, so a short move is a loop and a long one is a ruler. This builds a path from a
person's own recorded movements instead:

  speed       progress along the move follows a velocity curve  tau^(a-1) * (1-tau)^(b-1); a and b are fitted so
              the peak falls where this person's does (path_a, path_b), varied a little move to move
  curve       the sideways bow is a fraction of the distance, with this person's own spread and lean
              (path_lat_sd, path_lat_bias), plus a smaller second bend
  tremor      a few tenths of a pixel of correlated hand noise that fades out as the pointer lands
  overshoot   as often as they do (path_over_rate), the pointer runs a little past the target and comes back

Only the standard library. The caller gives a duration (from Fitts' law and the person's tempo) and gets back
a function from time to position that is exactly the target once the time is up.
"""

import math
import random

TABLE_SIZE = 240
TREMOR_STEP = 0.005
TREMOR_MEMORY = 0.04        # seconds a tremor wobble lasts
LANDING_FADE = 0.06         # tremor dies away over the last bit of the move
CORRECTION_SHARE = 0.2      # of the time, when there is an overshoot to come back from

DEFAULTS = {
	"path_a": 2.7, "path_b": 3.6, "path_lat_sd": 0.03, "path_lat_bias": 0.0, "path_tremor": 0.35, "path_over_rate": 0.1,
}


def velocity_table(a: float, b: float, size: int = TABLE_SIZE) -> list[float]:
	"""Progress (0 to 1) at evenly spaced fractions of the move, for a speed of tau^(a-1) (1-tau)^(b-1)."""
	cumulative, total = [0.0], 0.0

	for step in range(1, size + 1):
		tau = (step - 0.5) / size
		total += (tau ** (a - 1)) * ((1 - tau) ** (b - 1))
		cumulative.append(total)

	return [value / total for value in cumulative]


def progress(table: list[float], tau: float) -> float:
	if tau <= 0:
		return 0.0

	if tau >= 1:
		return 1.0

	position = tau * (len(table) - 1)
	low = int(position)
	share = position - low

	return table[low] * (1 - share) + table[low + 1] * share


def _smooth(x: float) -> float:
	x = min(1.0, max(0.0, x))

	return x * x * (3 - 2 * x)


def _tremor(duration: float, size: float, rng) -> list[tuple[float, float]]:
	"""Correlated noise sampled every few milliseconds: (x, y) offsets in pixels."""
	steps = int(duration / TREMOR_STEP) + 2
	memory = math.exp(-TREMOR_STEP / TREMOR_MEMORY)
	spread = math.sqrt(1 - memory ** 2) * size
	x = y = 0.0
	points = []

	for _ in range(steps):
		x = memory * x + spread * rng.gauss(0, 1)
		y = memory * y + spread * rng.gauss(0, 1)
		points.append((x, y))

	return points


NEAREST_MOVES = 6
LIBRARY_MIN_DISTANCE = 60.0


def _from_library(library: list[dict], distance: float, rng) -> dict:
	"""A recorded move of about this length: one of the nearest in length, chosen at random."""
	ranked = sorted(library, key=lambda entry: abs(math.log(max(1.0, entry["d"]) / distance)))

	return rng.choice(ranked[:NEAREST_MOVES])


def _replay(entry: dict, distance: float, rng):
	"""A function from the share of the move's time to (progress, sideways) as shares of its length, for a recorded
	move, varied a little: its timing warped by a few percent and its bow by about a tenth, so a recorded move is
	never repeated exactly."""
	u, l = entry["u"], entry["l"]
	end = u[-1] or 1.0
	warp = rng.gauss(0, 0.025)
	scale = math.exp(rng.gauss(0, 0.1))
	size = len(u) - 1

	def at(tau: float):
		tau = min(1.0, max(0.0, tau + warp * math.sin(math.pi * tau)))
		position = tau * size
		low = min(size - 1, int(position))
		mix = position - low

		return (
			(u[low] * (1 - mix) + u[low + 1] * mix) / end,
			(l[low] * (1 - mix) + l[low + 1] * mix) * scale,
		)

	return at


def build(start, end, duration: float, params: dict | None = None, rng=random, lead: float = 0.0):
	"""A function t (seconds) -> (x, y) for a move from `start` to `end`.

	`duration` is the time of the aimed movement as it is measured (from the pointer being 3 pixels from where
	it started); `lead` is the slow beginning before that. The function runs for duration + lead and says how
	long in its `total` attribute: the caller should drive it for that long."""
	params = {**DEFAULTS, **(params or {})}
	duration = duration + max(0.0, lead)
	sx, sy = start
	ex, ey = end
	dx, dy = ex - sx, ey - sy
	distance = math.hypot(dx, dy)

	if distance < 1 or duration <= 0:
		def still(t):
			return (ex, ey) if t >= duration else (sx, sy)

		still.total = duration

		return still

	ux, uy = dx / distance, dy / distance
	px, py = -uy, ux
	shapes = params.get("path_shapes")

	if shapes:
		# One of this person's own moves' shapes, as measured, a touch varied: the sharpness of their peak and how
		# much one move differs from the next come out as they are instead of as an average of them.
		a, b = rng.choice(shapes)
		a, b = a * params.get("path_a_scale", 1.0), b * params.get("path_b_scale", 1.0)
		table = velocity_table(max(1.15, a * math.exp(rng.gauss(0, 0.04))), max(1.15, b * math.exp(rng.gauss(0, 0.04))))
	else:
		table = velocity_table(params["path_a"] * math.exp(rng.gauss(0, 0.08)), params["path_b"] * math.exp(rng.gauss(0, 0.08)))
	bow = distance * (params["path_lat_bias"] + params["path_lat_sd"] * rng.gauss(0, 1))
	second_bend = bow * rng.gauss(0, 0.25)
	over = distance * rng.uniform(0.015, 0.05) + 2 if distance > 60 and rng.random() < params["path_over_rate"] else 0.0
	reach = distance + over
	main_time = duration * (1 - CORRECTION_SHARE) if over else duration
	library = params.get("path_library") if distance >= LIBRARY_MIN_DISTANCE else None
	replay = _replay(_from_library(library, distance, rng), distance, rng) if library else None
	tremor = _tremor(duration, min(params["path_tremor"], 0.5) if replay else params["path_tremor"], rng)

	def at(t: float):
		if t >= duration:
			return (ex, ey)

		if t <= 0:
			return (sx, sy)

		if replay:
			progress_share, sideways_share = replay(t / duration)
			along, sideways = progress_share * distance, sideways_share * distance
		else:
			if t <= main_time:
				along = progress(table, t / main_time) * reach
			else:
				along = reach + (distance - reach) * _smooth((t - main_time) / (duration - main_time))

			share = min(1.0, along / distance)
			sideways = bow * math.sin(math.pi * share) + second_bend * math.sin(2 * math.pi * share)
		index = t / TREMOR_STEP
		low = int(index)
		mix = index - low
		jitter_x = tremor[low][0] * (1 - mix) + tremor[low + 1][0] * mix
		jitter_y = tremor[low][1] * (1 - mix) + tremor[low + 1][1] * mix
		fade = min(1.0, (duration - t) / LANDING_FADE)

		return (sx + ux * along + px * sideways + jitter_x * fade, sy + uy * along + py * sideways + jitter_y * fade)

	at.total = duration

	return at


def endpoint(centre, size, direction, params: dict, rng=random):
	"""Where inside a target the click lands: clustered around the middle, a touch short of it when the person
	tends to undershoot, and never outside it.

	`centre` is (x, y), `size` is (width, height), `direction` is the unit vector the pointer was travelling."""
	width, height = size
	sd_x = max(0.02, params.get("end_sd_across", 0.18)) * width
	sd_y = max(0.02, params.get("end_sd_across", 0.18)) * height
	along = params.get("end_bias_along", 0.0) * (abs(direction[0]) * width + abs(direction[1]) * height)

	for _ in range(20):
		x = centre[0] + rng.gauss(0, sd_x) - direction[0] * along
		y = centre[1] + rng.gauss(0, sd_y) - direction[1] * along

		if abs(x - centre[0]) <= width / 2 * 0.95 and abs(y - centre[1]) <= height / 2 * 0.95:
			return (x, y)

	return centre
