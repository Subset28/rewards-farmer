"""The pointer path shaped like a person's: its shape, that measuring recovers it, and that a classifier cannot tell it apart."""

import importlib.util
import math
import os
import random
import statistics
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import behavior
import calibration
import indistinguishable
import mouse_fit
import mouse_trajectory
import pointer_path

HAVE_NUMPY = importlib.util.find_spec("numpy") is not None

PERSON = {
	"path_a": 3.0, "path_b": 4.2, "path_lat_sd": 0.035, "path_lat_bias": 0.012, "path_tremor": 0.6, "path_over_rate": 0.2,
	"end_sd_across": 0.20, "end_bias_along": 0.06,
}


def sample(path, duration, step=1 / 60):
	times = [n * step for n in range(int(duration / step) + 1)]

	return times, [path(t) for t in times]


def speeds(points, step=1 / 60):
	return [math.dist(a, b) / step for a, b in zip(points, points[1:])]


class TestTheShape(unittest.TestCase):
	def test_it_starts_and_ends_exactly_where_asked(self):
		path = pointer_path.build((100, 100), (700, 400), 0.8, PERSON, random.Random(1))

		self.assertEqual(path(0), (100, 100))
		self.assertEqual(path(0.8), (700, 400))
		self.assertEqual(path(5.0), (700, 400))

	def test_it_starts_from_rest_rather_than_at_full_speed(self):
		first_third, peak_at = [], []

		for seed in range(60):
			_, points = sample(pointer_path.build((0, 0), (800, 0), 0.9, PERSON, random.Random(seed)), 0.9)
			v = speeds(points)
			first_third.append(statistics.mean(v[:len(v) // 3]) / max(v))
			peak_at.append(v.index(max(v)) / len(v))

		# The original path peaks in the first few percent and is already at full speed.
		self.assertTrue(0.25 < statistics.mean(peak_at) < 0.6)
		self.assertLess(statistics.mean(first_third), 0.85)

	def test_the_speed_dies_towards_the_landing_but_not_instantly(self):
		_, points = sample(pointer_path.build((0, 0), (800, 0), 0.9, {**PERSON, "path_over_rate": 0.0, "path_tremor": 0.0}, random.Random(3)), 0.9)
		v = speeds(points)

		self.assertLess(v[-1], 0.3 * max(v))
		self.assertGreater(statistics.mean(v[-len(v) // 5:-1]), 0.0)

	def test_the_bow_grows_with_the_distance_instead_of_being_a_fixed_number_of_pixels(self):
		def bow(distance):
			values = []

			for seed in range(80):
				_, points = sample(pointer_path.build((0, 0), (distance, 0), 0.9, {**PERSON, "path_tremor": 0.0, "path_over_rate": 0.0, "path_lat_bias": 0.0}, random.Random(seed)), 0.9)
				values.append(max(abs(y) for _, y in points))

			return statistics.mean(values)

		short, long = bow(150), bow(1200)

		self.assertGreater(long, 5 * short)
		self.assertAlmostEqual(long / 1200, short / 150, delta=0.02)

	def test_the_original_path_does_not_scale_with_distance(self):
		def bow(distance):
			values = []

			for seed in range(60):
				random.seed(seed)
				path = mouse_trajectory.get_final_path_from_real_time(0.9, (0, 0), (distance, 0))
				values.append(max(abs(path(n / 60)[1]) for n in range(55)))

			return statistics.mean(values)

		self.assertGreater(bow(150) / 150, 3 * (bow(1200) / 1200))

	def test_tremor_dies_away_as_the_pointer_lands(self):
		path = pointer_path.build((0, 0), (500, 0), 0.7, {**PERSON, "path_tremor": 3.0, "path_over_rate": 0.0, "path_lat_sd": 0.0, "path_lat_bias": 0.0}, random.Random(2))
		early = statistics.pstdev([path(t / 100)[1] for t in range(10, 30)])
		late = abs(path(0.699)[1])

		self.assertLess(late, 1.0)
		self.assertGreater(early, 0.05)

	def test_sometimes_it_runs_past_the_target_and_comes_back(self):
		over = 0

		for seed in range(200):
			_, points = sample(pointer_path.build((0, 0), (600, 0), 0.9, {**PERSON, "path_over_rate": 0.3}, random.Random(seed)), 0.9)
			over += max(x for x, _ in points) > 603

		self.assertAlmostEqual(over / 200, 0.3, delta=0.1)

	def test_never_when_the_person_never_does(self):
		for seed in range(60):
			_, points = sample(pointer_path.build((0, 0), (600, 0), 0.9, {**PERSON, "path_over_rate": 0.0}, random.Random(seed)), 0.9)

			self.assertLess(max(x for x, _ in points), 606)

	def test_a_zero_length_move_is_handled(self):
		path = pointer_path.build((50, 50), (50, 50), 0.4, PERSON, random.Random(1))

		self.assertEqual(path(0.2), (50, 50))

	def test_two_moves_between_the_same_points_are_not_identical(self):
		a = pointer_path.build((0, 0), (500, 300), 0.8, PERSON, random.Random(1))
		b = pointer_path.build((0, 0), (500, 300), 0.8, PERSON, random.Random(2))

		self.assertNotEqual(a(0.4), b(0.4))


class TestWhereTheClickLands(unittest.TestCase):
	def test_clicks_cluster_around_the_middle_and_stay_inside(self):
		rng = random.Random(1)
		points = [pointer_path.endpoint((500, 300), (100, 60), (1, 0), {"end_sd_across": 0.2, "end_bias_along": 0.0}, rng) for _ in range(3000)]

		self.assertTrue(all(abs(x - 500) <= 50 and abs(y - 300) <= 30 for x, y in points))
		self.assertAlmostEqual(statistics.mean(x for x, _ in points), 500, delta=2)
		self.assertGreater(sum(1 for x, _ in points if abs(x - 500) < 15) / len(points), 0.4)

	def test_someone_who_undershoots_lands_short_of_the_middle(self):
		rng = random.Random(1)
		short = [pointer_path.endpoint((500, 300), (100, 60), (1, 0), {"end_sd_across": 0.15, "end_bias_along": 0.2}, rng)[0] for _ in range(2000)]

		self.assertLess(statistics.mean(short), 495)


def recorded_moves(params, count=70, seed=5, original=False):
	"""Trials as the calibration records them, made by a path generator, with the click landing where the person's land."""
	rng = random.Random(seed)
	targets = calibration.make_targets(count, 1920, 1080, rng)
	trials = []

	for n, (x, y, w, h) in enumerate(targets):
		start = (960 + rng.uniform(-8, 8), 540 + rng.uniform(-8, 8))
		centre = (x + w / 2, y + h / 2)
		distance = math.dist(start, centre)
		direction = ((centre[0] - start[0]) / distance, (centre[1] - start[1]) / distance)
		landing = pointer_path.endpoint(centre, (w, h), direction, params, rng)
		duration = 0.25 + 0.14 * math.log2(max(2 * distance / ((w + h) / 2), 1.0)) * math.exp(rng.gauss(0, 0.1))

		if original:
			random.seed(n)
			path = mouse_trajectory.get_final_path_from_real_time(duration, start, landing)
		else:
			path = pointer_path.build(start, landing, duration, params, rng)

		shown = 1000.0 + n * 10
		trial = calibration.Trial(start, (x, y, w, h), shown)
		moment = 0.0

		while moment < duration:
			px, py = path(moment)
			trial.move(shown + 0.3 + moment, px, py)
			moment += 1 / 60

		trial.press(shown + 0.3 + duration, landing[0], landing[1])
		trial.release(shown + 0.3 + duration + 0.09)
		trials.append(trial)

	return trials


class TestMeasuringRecoversTheShape(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		cls.trials = recorded_moves(PERSON, count=90)
		cls.fitted = mouse_fit.fit([mouse_fit.track(t) for t in cls.trials])

	def test_the_speed_curve_peaks_where_the_persons_does(self):
		fitted_peak = (self.fitted["path_a"] - 1) / (self.fitted["path_a"] + self.fitted["path_b"] - 2)
		true_peak = (PERSON["path_a"] - 1) / (PERSON["path_a"] + PERSON["path_b"] - 2)

		self.assertAlmostEqual(fitted_peak, true_peak, delta=0.06)

	def test_the_bow(self):
		self.assertAlmostEqual(self.fitted["path_lat_sd"], PERSON["path_lat_sd"], delta=0.02)
		self.assertAlmostEqual(self.fitted["path_lat_bias"], PERSON["path_lat_bias"], delta=0.02)

	def test_how_often_it_overshoots(self):
		self.assertAlmostEqual(self.fitted["path_over_rate"], PERSON["path_over_rate"], delta=0.12)

	def test_where_clicks_land(self):
		self.assertAlmostEqual(self.fitted["end_sd_across"], PERSON["end_sd_across"], delta=0.07)
		self.assertAlmostEqual(self.fitted["end_bias_along"], PERSON["end_bias_along"], delta=0.07)

	def test_too_few_moves_gives_nothing(self):
		self.assertEqual(mouse_fit.fit([mouse_fit.track(t) for t in self.trials[:4]]), {})

	def test_it_all_fits_the_profile_ranges(self):
		cleaned = behavior.clean_detail(self.fitted, behavior.MOUSE_DETAIL_RANGES)

		for key in ("path_a", "path_b", "path_lat_sd", "path_lat_bias", "path_tremor", "path_over_rate", "end_sd_across", "end_bias_along"):
			self.assertIn(key, cleaned)


@unittest.skipUnless(HAVE_NUMPY, "numpy is not installed")
class TestACassifierCannotTellItApart(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		cls.trials = recorded_moves(PERSON, count=60)
		fitted = mouse_fit.fit([mouse_fit.track(t) for t in cls.trials])
		# The formula alone, without the replay of recorded moves, which would make refining pointless
		# (the replay is already as good as the person): this is the fallback for a new recording and the
		# thing refining improves.
		cls.fitted = {k: v for k, v in fitted.items() if k not in ("path_library", "path_shapes")}
		cls.refined = indistinguishable.refine_mouse(cls.trials, cls.fitted)

	def test_the_original_generator_is_easy_to_tell_from_a_person(self):
		original = indistinguishable.mouse_simulated_features(self.trials, None, random.Random(1), original=True)
		real = indistinguishable.mouse_real_features(self.trials)

		self.assertGreater(indistinguishable.auc(real, original), 0.9)

	def test_a_path_built_from_the_persons_measured_and_refined_numbers_is_not(self):
		outcome = indistinguishable.mouse_tell_apart(self.trials, self.refined, random.Random(1))

		self.assertIsNotNone(outcome)
		self.assertLess(outcome["auc"], 0.68, indistinguishable.verdict(outcome["auc"]))

	def test_refining_the_settings_against_the_recording_brings_them_closer(self):
		before = indistinguishable.mouse_tell_apart(self.trials, self.fitted, random.Random(1))["auc"]
		after = indistinguishable.mouse_tell_apart(self.trials, self.refined, random.Random(1))["auc"]

		self.assertLess(after, before - 0.05)

	def test_refining_without_numpy_or_a_shape_changes_nothing(self):
		self.assertEqual(indistinguishable.refine_mouse(self.trials, {"dwell_ms": 90.0}), {"dwell_ms": 90.0})

	def test_leaving_the_measured_shape_out_is_worse(self):
		flat = {**self.refined, "path_lat_sd": 0.0, "path_lat_bias": 0.0, "path_tremor": 0.0, "path_over_rate": 0.0}
		measured = indistinguishable.mouse_tell_apart(self.trials, self.refined, random.Random(1))["auc"]
		without = indistinguishable.mouse_tell_apart(self.trials, flat, random.Random(1))["auc"]

		self.assertGreater(without, measured + 0.1)


class TestReplayingTheAccountsOwnMoves(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		cls.trials = recorded_moves(PERSON, count=40, seed=11)
		cls.fitted = mouse_fit.fit([mouse_fit.track(t) for t in cls.trials])

	def test_every_usable_recorded_move_is_in_the_library(self):
		self.assertGreaterEqual(len(self.fitted["path_library"]), 35)

	def test_a_replayed_move_starts_and_ends_where_asked(self):
		path = pointer_path.build((100, 100), (800, 500), 0.7, self.fitted, random.Random(1))

		self.assertEqual(path(0), (100, 100))
		self.assertEqual(path(path.total), (800, 500))

	def test_a_replayed_move_is_never_the_same_twice(self):
		paths = [pointer_path.build((0, 0), (700, 200), 0.7, self.fitted, random.Random(seed)) for seed in range(6)]
		middles = {round(p(0.35)[0], 1) for p in paths}

		self.assertGreater(len(middles), 3)

	def test_a_replayed_move_stays_near_the_line_and_the_target(self):
		for seed in range(40):
			path = pointer_path.build((0, 0), (700, 0), 0.7, self.fitted, random.Random(seed))
			points = [path(n * 0.7 / 40) for n in range(41)]

			self.assertTrue(all(-60 <= x <= 900 and abs(y) <= 400 for x, y in points))

	def test_a_short_move_below_the_library_minimum_falls_back_to_the_formula(self):
		path = pointer_path.build((0, 0), (30, 10), 0.3, self.fitted, random.Random(1))

		self.assertEqual(path(0.3), (30, 10))

	def test_the_discriminator_does_not_let_a_move_stand_in_for_itself(self):
		own = {e["id"] for e in self.fitted["path_library"]}
		rows = indistinguishable.mouse_simulated_features(self.trials[:3], self.fitted, random.Random(1), repeats=2)

		self.assertTrue(rows)
		self.assertEqual(own & {0, 1, 2}, {0, 1, 2})

	@unittest.skipUnless(HAVE_NUMPY, "numpy is not installed")
	def test_replaying_the_persons_own_moves_cannot_be_told_from_them(self):
		outcome = indistinguishable.mouse_tell_apart(self.trials, self.fitted, random.Random(1))

		self.assertIsNotNone(outcome)
		self.assertLess(outcome["auc"], 0.68, indistinguishable.verdict(outcome["auc"]))

	def test_the_library_survives_the_profile_check(self):
		cleaned = behavior.clean_detail(self.fitted, behavior.MOUSE_DETAIL_RANGES)

		self.assertGreaterEqual(len(cleaned["path_library"]), 35)

	def test_a_damaged_library_is_dropped_not_trusted(self):
		bad = {"path_library": [{"d": 500, "T": 0.5, "u": [0.0, 1.0], "l": [0.0]} for _ in range(20)]}

		self.assertNotIn("path_library", behavior.clean_detail(bad, behavior.MOUSE_DETAIL_RANGES))


class TestTheMouseUsesIt(unittest.TestCase):
	def mouse(self, detail):
		profile = behavior.Behavior("mom", "recorded", 0.4, 0.4, 0.4, 0.15, mouse_detail=detail)

		with mock.patch.object(mouse_trajectory.MouseUtils, "reinitialize"):
			return mouse_trajectory.MouseUtils(mock.Mock(), profile)

	def setUp(self):
		patcher = mock.patch.dict(os.environ, {"REWARDS_FEATURES": "mouse"})
		patcher.start()
		self.addCleanup(patcher.stop)

	def test_with_a_recording_and_the_switch_the_new_path_is_built(self):
		with mock.patch.object(mouse_trajectory.pointer_path, "build", wraps=pointer_path.build) as build:
			path = self.mouse(PERSON).path_for((0, 0), (500, 200), 0.8)

		build.assert_called_once()
		self.assertEqual(path(0.8), (500, 200))

	def test_without_the_switch_it_is_the_original(self):
		with mock.patch.dict(os.environ, {"REWARDS_FEATURES": ""}), mock.patch.object(mouse_trajectory.pointer_path, "build") as build:
			self.mouse(PERSON).path_for((0, 0), (500, 200), 0.8)

		build.assert_not_called()

	def test_without_a_recorded_shape_it_is_the_original(self):
		with mock.patch.object(mouse_trajectory.pointer_path, "build") as build:
			self.mouse({"dwell_ms": 100.0}).path_for((0, 0), (500, 200), 0.8)

		build.assert_not_called()

	def test_the_aim_point_follows_the_persons_landing_when_recorded(self):
		mouse = self.mouse(PERSON)
		rect = {"x": 400, "y": 300, "width": 120, "height": 60}
		points = [mouse.choose_target((0, 0), rect) for _ in range(300)]

		self.assertTrue(all(400 <= x <= 520 and 300 <= y <= 360 for x, y in points))

	def test_the_aim_point_is_the_original_choice_without_it(self):
		mouse = self.mouse({"dwell_ms": 100.0})
		rect = {"x": 400, "y": 300, "width": 120, "height": 60}
		points = [mouse.choose_target((0, 0), rect) for _ in range(300)]

		self.assertTrue(all(430 <= x <= 490 and 315 <= y <= 345 for x, y in points))


if __name__ == "__main__":
	unittest.main()
