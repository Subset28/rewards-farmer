"""The calibration: what it measures from keys and clicks, the profile it makes, and the app that runs it."""

import json
import math
import os
import random
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import behavior
import calibrate
import calibration
import features
import mimic_typing
import mouse_trajectory


class Clock:
	def __init__(self):
		self.now = 100.0

	def __call__(self):
		return self.now

	def tick(self, seconds):
		self.now += seconds


def keysym_for(char):
	return "space" if char == " " else char


def record_phrase(target, steps, shown=100.0):
	"""A PhraseRecord from (delay, kind, char) steps, so a test says what a typist did."""
	record = calibration.PhraseRecord(target, shown)
	now = shown
	typed = ""

	for delay, kind, char in steps:
		now += delay
		record.events.append((now, kind, char))

		if kind == "char":
			typed += char
		elif kind == "back":
			typed = typed[:-1]

	record.final = typed
	record.submitted_at = now

	return record


def clean_steps(text, gap=0.1, start=1.0, enter=0.4):
	steps = [(start, "char", text[0])]
	steps += [(gap, "char", ch) for ch in text[1:]]
	steps.append((enter, "enter", ""))

	return steps


class TestPhraseAnalysis(unittest.TestCase):
	def test_a_clean_phrase_has_no_slip_and_its_gaps(self):
		result = calibration.analyze_phrase(record_phrase("abc def", clean_steps("abc def")))

		self.assertIsNone(result["slip"])
		self.assertEqual(len(result["gaps"]), 6)
		self.assertTrue(result["fixed"])
		self.assertAlmostEqual(result["start_latency"], 1.0)
		self.assertAlmostEqual(result["enter_gap"], 0.4)

	def test_a_neighbouring_key_is_a_neighbour_slip_noticed_after_two_keys_and_fixed(self):
		steps = [
			(1.0, "char", "h"), (0.1, "char", "r"),                       # wanted "e", got a neighbour
			(0.1, "char", "l"), (0.1, "char", "l"),                       # two more keys before noticing
			(0.7, "back", ""), (0.15, "back", ""), (0.15, "back", ""),
			(0.3, "char", "e"), (0.1, "char", "l"), (0.1, "char", "l"), (0.1, "char", "o"),
			(0.4, "enter", ""),
		]
		result = calibration.analyze_phrase(record_phrase("hello", steps))

		self.assertEqual(result["slip"], "neighbor")
		self.assertEqual(result["noticed_after"], 2)
		self.assertAlmostEqual(result["notice_pause"], 0.7)
		self.assertTrue(result["fixed"])

	def test_two_letters_the_wrong_way_round_is_a_swap(self):
		steps = [(1.0, "char", "h"), (0.1, "char", "l"), (0.1, "char", "e"), (0.1, "char", "l"), (0.1, "char", "o"), (0.4, "enter", "")]
		result = calibration.analyze_phrase(record_phrase("hello", steps))

		self.assertEqual(result["slip"], "swap")
		self.assertFalse(result["fixed"])

	def test_a_slip_left_in_is_not_fixed(self):
		steps = [(1.0, "char", "h"), (0.1, "char", "x"), (0.1, "char", "l"), (0.1, "char", "l"), (0.1, "char", "o"), (0.4, "enter", "")]
		result = calibration.analyze_phrase(record_phrase("hello", steps))

		self.assertEqual(result["slip"], "other")
		self.assertFalse(result["fixed"])
		self.assertIsNone(result["noticed_after"])

	def test_rhythm_gaps_do_not_include_the_wait_before_a_backspace_or_after_one(self):
		steps = [(1.0, "char", "h"), (0.1, "char", "x"), (2.0, "back", ""), (0.15, "back", ""), (3.0, "char", "e"), (0.1, "char", "l")]
		result = calibration.analyze_phrase(record_phrase("he", steps))

		self.assertEqual([round(g, 2) for g, _, _ in result["gaps"]], [0.1, 0.1])
		self.assertEqual([round(g, 2) for g in result["back_gaps"]], [0.15])

	def test_the_gap_after_a_space_is_marked(self):
		result = calibration.analyze_phrase(record_phrase("a b", clean_steps("a b")))

		self.assertEqual([previous == " " for _, previous, _ in result["gaps"]], [False, True])


def synthetic_records(count=12, rng=None, slip_every=4):
	"""A believable session: steady typing, a hesitation, and every few phrases a slip noticed two keys later."""
	rng = rng or random.Random(5)
	records = []

	for n in range(count):
		text = calibration.PHRASES[n % len(calibration.PHRASES)]
		steps = [(1.2, "char", text[0])]
		slipped = n % slip_every == 1

		for i, ch in enumerate(text[1:], start=1):
			gap = rng.choice([0.07, 0.09, 0.14, 0.16, 0.3])

			if i == 8 and n % 3 == 0:
				gap = 1.1                                    # a hesitation

			if slipped and i == 3:
				steps.append((gap, "char", "#"))              # the wrong key
				steps.append((0.1, "char", text[i + 1]))
				steps.append((0.1, "char", text[i + 2]))
				steps.append((0.6, "back", ""))
				steps += [(0.14, "back", "")] * 2
				steps += [(0.25, "char", ch), (0.1, "char", text[i + 1]), (0.1, "char", text[i + 2])]
				continue

			if slipped and i in (4, 5):
				continue

			steps.append((gap, "char", ch))

		steps.append((0.4, "enter", ""))
		records.append(record_phrase(text, steps))

	return records


class TestTypingAnalysis(unittest.TestCase):
	def test_too_little_typing_is_refused(self):
		with self.assertRaises(behavior.ProfileError):
			calibration.analyze_typing(synthetic_records(3))

	def test_the_measurements_are_there_and_plausible(self):
		result = calibration.analyze_typing(synthetic_records(12))
		detail = result["detail"]

		self.assertGreater(result["fast_share"], 0.05)
		self.assertLess(result["fast_share"] + result["medium_share"], 1.0)
		self.assertAlmostEqual(detail["slip_rate"], 3 / 12)
		self.assertAlmostEqual(detail["correction_rate"], 1.0)
		self.assertAlmostEqual(detail["notice_pause_ms"], 600, delta=5)
		self.assertAlmostEqual(detail["backspace_gap_ms"], 140, delta=5)
		self.assertGreater(detail["hesitation_rate"], 0)
		self.assertAlmostEqual(detail["hesitation_ms"], 1100, delta=5)
		self.assertAlmostEqual(detail["start_latency_ms"], 1200, delta=5)
		self.assertEqual(len(detail["noticed_weights"]), 4)
		self.assertGreater(detail["noticed_weights"][1], 0)     # noticed two keys later

	def test_everything_measured_survives_the_profiles_own_range_check(self):
		detail = calibration.analyze_typing(synthetic_records(12))["detail"]
		cleaned = behavior.clean_detail(detail, behavior.TYPING_DETAIL_RANGES, "noticed_weights")

		for key in ("slip_rate", "correction_rate", "notice_pause_ms", "backspace_gap_ms", "hesitation_rate", "start_latency_ms", "noticed_weights"):
			self.assertIn(key, cleaned, key)

	def test_a_typist_with_no_slips_just_has_no_slip_measurements(self):
		records = [record_phrase(t, clean_steps(t, gap=0.09)) for t in calibration.PHRASES[:12]]
		detail = calibration.analyze_typing(records)["detail"]

		self.assertEqual(detail["slip_rate"], 0)
		self.assertNotIn("neighbor_share", detail)
		self.assertNotIn("correction_rate", detail)


class TestKeyHolds(unittest.TestCase):
	def record(self, holds):
		record = calibration.PhraseRecord("abc", 0.0)
		record.holds = holds

		return record

	def test_hold_time_and_rollover_are_measured(self):
		# Each key held 90 ms, the next one going down 60 ms after the last, i.e. before it comes up.
		holds = [(chr(97 + n % 20), n * 0.06, n * 0.06 + 0.09) for n in range(80)]
		measured = calibration.key_holds([self.record(holds)])

		self.assertAlmostEqual(measured["hold_ms"], 90, delta=1)
		self.assertAlmostEqual(measured["rollover_rate"], 1.0, delta=0.01)

	def test_keys_let_go_before_the_next_are_not_rollover(self):
		holds = [(chr(97 + n % 20), n * 0.2, n * 0.2 + 0.08) for n in range(80)]

		self.assertEqual(calibration.key_holds([self.record(holds)])["rollover_rate"], 0.0)

	def test_a_key_held_for_ages_is_not_a_hold(self):
		holds = [("a", n * 0.2, n * 0.2 + 2.0) for n in range(80)]

		self.assertNotIn("hold_ms", calibration.key_holds([self.record(holds)]))

	def test_too_little_to_say_says_nothing(self):
		self.assertEqual(calibration.key_holds([self.record([("a", 0.0, 0.09)])]), {})

	def test_the_recorder_notes_how_long_each_key_was_down(self):
		recorder = calibration.TypingRecorder(phrases=("hello there",), practice=None, shuffle=False, compose=())
		recorder.show(0.0)
		recorder.press("h", "h", 1.00)
		recorder.release("h", 1.08)

		self.assertEqual(recorder._current.holds, [("h", 1.00, 1.08)])


class TestReanalyze(unittest.TestCase):
	def test_a_profile_can_be_rebuilt_from_what_was_recorded(self):
		directory = tempfile.TemporaryDirectory()
		self.addCleanup(directory.cleanup)

		with mock.patch.object(behavior, "PROFILE_DIR", directory.name):
			clock = Clock()
			app = calibrate.App("kin", None, clock=clock, rng=random.Random(2), phrases=10, trials=14)
			helper = TestTheApp()
			helper.clock, helper.app, helper.directory = clock, app, directory
			helper.run_typing()
			helper.run_mouse()
			os.unlink(os.path.join(directory.name, "kin.json"))

			self.assertEqual(calibrate.reanalyze("kin"), 0)
			self.assertEqual(behavior.load("kin").source, "recorded")
			self.assertEqual(calibrate.reanalyze("nobody"), 1)


class TestRecorder(unittest.TestCase):
	def setUp(self):
		self.recorder = calibration.TypingRecorder(phrases=("hello there",), practice=None, shuffle=False, compose=())
		self.recorder.show(10.0)

	def type_text(self, text, start=11.0, gap=0.1):
		now = start

		for ch in text:
			self.recorder.press(keysym_for(ch), ch, now)
			self.recorder.release(keysym_for(ch))
			now += gap

		return now

	def test_enter_is_ignored_until_enough_is_typed(self):
		self.type_text("he")

		self.assertEqual(self.recorder.press("Return", "\r", 12.0), "ignored")
		self.assertFalse(self.recorder.finished)

	def test_enter_submits_whatever_was_typed_even_with_a_mistake(self):
		now = self.type_text("hellx there")

		self.assertEqual(self.recorder.press("Return", "\r", now), "finished")
		self.assertEqual(self.recorder.records[0].final, "hellx there")

	def test_a_held_key_counts_once(self):
		self.recorder.press("h", "h", 11.0)

		self.assertEqual(self.recorder.press("h", "h", 11.03), "ignored")
		self.assertEqual(self.recorder.typed, "h")

	def test_modifier_keys_are_not_typing(self):
		self.assertEqual(self.recorder.press("Shift_L", "", 11.0), "ignored")

	def test_the_practice_phrase_is_not_counted(self):
		recorder = calibration.TypingRecorder(phrases=("hello there",), practice="warm up now", shuffle=False, compose=())
		recorder.show(0.0)

		for i, ch in enumerate("warm up now"):
			recorder.press(keysym_for(ch), ch, 1 + i * 0.1)
			recorder.release(keysym_for(ch))

		recorder.press("Return", "\r", 5.0)

		self.assertEqual(recorder.records, [])
		self.assertEqual(recorder.target, "hello there")


def simulate_trial(app, clock, a=0.2, b=0.13, reaction=0.3, hover=0.06, dwell=0.09, miss_first=False):
	"""Click the dot, then the rectangle, as a person with Fitts constants a and b would."""
	cx, cy = app.centre
	app.on_button_press(cx + 2, cy - 1)
	clock.tick(0.01)
	app.on_button_release()

	trial = app.trial
	x, y, w, h = trial.rect
	tx, ty = x + w / 2, y + h / 2
	distance = math.hypot(tx - (cx + 2), ty - (cy - 1))
	movement_time = a + b * math.log2(max(2 * distance / ((w + h) / 2), 1.0))
	steps = 12
	clock.tick(reaction)
	app.on_motion(cx + 2 + (tx - cx) * 0.02, cy - 1 + (ty - cy) * 0.02 + 6)

	for k in range(1, steps + 1):
		clock.tick(movement_time / steps)
		fraction = k / steps
		app.on_motion(cx + (tx - cx) * fraction, cy + (ty - cy) * fraction + 4 * math.sin(math.pi * fraction))

	if miss_first:
		app.on_button_press(5, 5)
		clock.tick(0.2)

	app.on_button_press(tx, ty)
	clock.tick(dwell)
	app.on_button_release()
	clock.tick(0.4)

	return movement_time


class TestMouseAnalysis(unittest.TestCase):
	def make_app(self, trials=14):
		clock = Clock()
		app = calibrate.App("mom", None, clock=clock, rng=random.Random(3), trials=trials, phrases=2, save=False)

		return app, clock

	def test_targets_cover_a_range_of_difficulty(self):
		targets = calibration.make_targets(18, 1920, 1080, random.Random(1))
		starts = (960, 540)
		ids = []

		for x, y, w, h in targets:
			distance = math.hypot(x + w / 2 - starts[0], y + h / 2 - starts[1])
			ids.append(math.log2(max(2 * distance / ((w + h) / 2), 1.0)))

		self.assertEqual(len(targets), 18)
		self.assertGreater(max(ids) - min(ids), 1.5)

	def test_fitts_constants_are_recovered_from_simulated_clicks(self):
		app, clock = self.make_app(16)
		app.start_mouse()

		for _ in range(16):
			simulate_trial(app, clock, a=0.2, b=0.13)

		result = calibration.analyze_mouse(app.trials)

		self.assertAlmostEqual(result["fitts_a"], 0.2, delta=0.03)
		self.assertAlmostEqual(result["fitts_b"], 0.13, delta=0.03)
		self.assertAlmostEqual(result["detail"]["reaction_ms"], 300, delta=40)
		self.assertAlmostEqual(result["detail"]["dwell_ms"], 90, delta=10)

	def test_a_missed_click_is_counted(self):
		app, clock = self.make_app(12)
		app.start_mouse()

		for n in range(12):
			simulate_trial(app, clock, miss_first=(n % 3 == 0))

		detail = calibration.analyze_mouse(app.trials)["detail"]

		self.assertAlmostEqual(detail["miss_rate"], 4 / 12, delta=0.01)

	def test_a_straight_path_is_straighter_than_a_curved_one(self):
		app, clock = self.make_app(12)
		app.start_mouse()

		for _ in range(12):
			simulate_trial(app, clock)

		self.assertGreaterEqual(calibration.analyze_mouse(app.trials)["detail"]["straightness"], 1.0)

	def test_too_few_clicks_is_refused(self):
		app, clock = self.make_app(3)
		app.start_mouse()

		for _ in range(3):
			simulate_trial(app, clock)

		with self.assertRaises(behavior.ProfileError):
			calibration.analyze_mouse(app.trials)

	def test_clicks_that_take_the_same_time_whatever_the_difficulty_say_so_in_plain_words(self):
		trials = [{"id": 1.0 + n * 0.5, "movement_time": 0.5} for n in range(12)]

		with self.assertRaisesRegex(behavior.ProfileError, "mouse speed could not be measured"):
			calibration.fit_fitts(trials)

	def test_clicks_that_are_all_alike_cannot_make_a_fit(self):
		trials = [{"id": 3.0, "movement_time": 0.5}] * 12

		with self.assertRaises(behavior.ProfileError):
			calibration.fit_fitts(trials)

	def test_a_click_outside_the_rectangle_does_not_finish_the_trial(self):
		app, clock = self.make_app(4)
		app.start_mouse()
		cx, cy = app.centre
		app.on_button_press(cx, cy)
		app.on_button_release()
		clock.tick(0.3)
		app.on_motion(cx + 40, cy + 40)
		clock.tick(0.2)
		app.on_button_press(3, 3)
		app.on_button_release()

		self.assertEqual(app.trials, [])


class TestProfileFormat(unittest.TestCase):
	def setUp(self):
		self.directory = tempfile.TemporaryDirectory()
		self.addCleanup(self.directory.cleanup)
		patcher = mock.patch.object(behavior, "PROFILE_DIR", self.directory.name)
		patcher.start()
		self.addCleanup(patcher.stop)

	def test_out_of_range_values_are_dropped_not_kept(self):
		cleaned = behavior.clean_detail({"slip_rate": 0.99, "notice_pause_ms": 900, "hesitation_ms": "slow", "nonsense": 5}, behavior.TYPING_DETAIL_RANGES)

		self.assertEqual(cleaned, {"notice_pause_ms": 900.0})

	def test_bad_weights_are_dropped(self):
		for weights in ([1, 2], [0, 0, 0, 0], [1, -1, 1, 1], "x"):
			self.assertNotIn("noticed_weights", behavior.clean_detail({"noticed_weights": weights}, behavior.TYPING_DETAIL_RANGES, "noticed_weights"))

	def test_a_profile_with_detail_round_trips(self):
		behavior.save("mom", 0.4, 0.4, 0.2, 0.13, typing_detail={"slip_rate": 0.12, "noticed_weights": [1, 2, 1, 0]}, mouse_detail={"dwell_ms": 95})
		loaded = behavior.load("mom")

		self.assertEqual(loaded.source, "recorded")
		self.assertEqual(loaded.typing_detail["slip_rate"], 0.12)
		self.assertEqual(loaded.typing_detail["noticed_weights"], [1.0, 2.0, 1.0, 0.0])
		self.assertEqual(loaded.mouse_detail["dwell_ms"], 95.0)

	def test_an_older_profile_without_detail_still_loads(self):
		path = Path(behavior.profile_path("old"))
		path.write_text(json.dumps({"typing": {"fast_share": 0.4, "medium_share": 0.4}, "mouse": {"fitts_a": 0.2, "fitts_b": 0.13}}))
		loaded = behavior.load("old")

		self.assertEqual(loaded.source, "recorded")
		self.assertEqual(loaded.typing_detail, {})
		self.assertEqual(loaded.mouse_detail, {})

	def test_provisional_and_baseline_profiles_have_no_detail(self):
		self.assertEqual(behavior.provisional("x").typing_detail, {})
		self.assertEqual(behavior.baseline().mouse_detail, {})


class TestWiring(unittest.TestCase):
	def setUp(self):
		self.directory = tempfile.TemporaryDirectory()
		self.addCleanup(self.directory.cleanup)

		for patcher in (
			mock.patch.dict(os.environ, {features.ENV: ""}),
			mock.patch.object(features, "FEATURES_FILE", str(Path(self.directory.name) / "features.json")),
		):
			patcher.start()
			self.addCleanup(patcher.stop)

		self.profile = behavior.Behavior(
			"mom", "recorded", 0.4, 0.4, 0.2, 0.13,
			typing_detail={"slip_rate": 0.2, "neighbor_share": 0.8, "correction_rate": 0.95, "noticed_weights": [0, 0, 0, 1], "notice_pause_ms": 900.0},
			mouse_detail={"dwell_ms": 120.0, "dwell_low_ms": 100.0, "dwell_high_ms": 150.0, "hover_ms": 200.0},
		)

	def turn_on(self, feature):
		features.switch(feature, "mom", True)

	def keyboard(self):
		return mimic_typing.KeyboardUtils(mock.Mock(), self.profile, account="mom")

	def test_with_the_feature_off_nothing_personal_is_used(self):
		keyboard = self.keyboard()

		self.assertEqual(keyboard.personal(), {})
		self.assertEqual(keyboard.slip_settings(), (mimic_typing.search_behavior.TYPO_RATE, 0.5))

	def test_with_the_feature_on_the_recorded_slip_habits_are_used(self):
		self.turn_on("typing")
		keyboard = self.keyboard()

		self.assertEqual(keyboard.slip_settings(), (0.2, 0.8))

	def test_a_recorded_correction_rate_and_noticing_delay_decide_the_correction(self):
		self.turn_on("typing")
		keyboard = self.keyboard()
		rng = random.Random(0)
		plan = keyboard._plan_correction(list("abxdefg"), list("abcdefg"), rng, keyboard.personal())

		# Weights say "always four keys later"; the slip at index 2 leaves room for four.
		self.assertEqual(plan, (2, 4))

	def test_a_recording_without_detail_behaves_as_before(self):
		self.turn_on("typing")
		keyboard = mimic_typing.KeyboardUtils(mock.Mock(), behavior.provisional("mom"), account="mom")

		self.assertEqual(keyboard.personal(), {})

	def mouse(self):
		with mock.patch.object(mouse_trajectory.MouseUtils, "reinitialize"):
			return mouse_trajectory.MouseUtils(mock.Mock(), self.profile)

	def click_duration(self, mouse):
		with mock.patch.object(mouse_trajectory, "ActionChains") as chains, mock.patch.object(mouse_trajectory.time, "sleep") as sleep:
			mouse.human_like_click()

		return chains.call_args.kwargs["duration"], sleep

	def test_the_click_is_the_original_one_until_the_mouse_feature_is_on(self):
		duration, sleep = self.click_duration(self.mouse())

		self.assertTrue(200 <= duration <= 300)
		sleep.assert_not_called()

	def test_with_the_mouse_feature_on_the_click_is_held_as_long_as_the_person_holds_it(self):
		self.turn_on("mouse")

		for _ in range(20):
			duration, sleep = self.click_duration(self.mouse())

			self.assertTrue(100 <= duration <= 151)

		sleep.assert_called()
		self.assertLessEqual(sleep.call_args.args[0], 0.4)

	def test_a_caller_that_names_its_own_hold_time_gets_it(self):
		self.turn_on("mouse")
		mouse = self.mouse()

		with mock.patch.object(mouse_trajectory, "ActionChains") as chains, mock.patch.object(mouse_trajectory.time, "sleep"):
			mouse.human_like_click((50, 51))

		self.assertIn(chains.call_args.kwargs["duration"], (50, 51))


class TestTheApp(unittest.TestCase):
	"""The whole sitting, driven without a window: a simulated typist and a simulated mouse."""

	def setUp(self):
		self.directory = tempfile.TemporaryDirectory()
		self.addCleanup(self.directory.cleanup)
		patcher = mock.patch.object(behavior, "PROFILE_DIR", self.directory.name)
		patcher.start()
		self.addCleanup(patcher.stop)
		self.clock = Clock()
		self.app = calibrate.App("mom", None, clock=self.clock, rng=random.Random(2), phrases=10, trials=14)

	def press(self, keysym, char=""):
		self.app.on_key_press(keysym, char)
		self.clock.tick(0.085)
		self.app.on_key_release(keysym)

	def type_current(self, slip=False):
		composing = self.app.typing.compose
		target = "best pizza near me tonight" if composing else self.app.typing.target
		slip = slip and not composing
		rng = random.Random(len(target))
		self.clock.tick(1.1)

		for i, ch in enumerate(target):
			self.clock.tick(rng.choice([0.0, 0.02, 0.06, 0.1, 0.13]))

			if slip and i == 4:
				self.press("x", "x")
				continue

			self.press(keysym_for(ch), ch)

		if slip:
			# Noticed at the end: Backspace back to the wrong letter and type the rest again.
			self.clock.tick(0.6)

			for _ in range(len(target) - 4):
				self.press("BackSpace")
				self.clock.tick(0.15)

			for ch in target[4:]:
				self.press(keysym_for(ch), ch)
				self.clock.tick(0.12)

		self.clock.tick(0.4)
		self.press("Return", "\r")

	def run_typing(self):
		self.press("Return", "\r")
		self.assertEqual(self.app.page, "typing")
		count = 0

		while self.app.page == "typing":
			self.type_current(slip=(count % 4 == 2))
			count += 1

			if count > 40:
				self.fail("the typing part did not end")

	def run_mouse(self):
		self.press("Return", "\r")
		self.assertEqual(self.app.page, "mouse")

		for _ in range(self.app.trial_count):
			simulate_trial(self.app, self.clock)

	def test_the_pages_follow_one_another_and_the_profile_is_written(self):
		self.assertEqual(self.app.page, "intro")
		self.run_typing()
		self.assertEqual(self.app.page, "mouse_intro")
		self.run_mouse()
		self.assertEqual(self.app.page, "results")
		self.assertIsNone(self.app.outcome["error"], self.app.outcome)

		loaded = behavior.load("mom")

		self.assertEqual(loaded.source, "recorded")
		self.assertIn("slip_rate", loaded.typing_detail)
		self.assertIn("dwell_ms", loaded.mouse_detail)
		self.assertTrue(Path(self.directory.name, "mom.raw.json").exists())

		raw = json.loads(Path(self.directory.name, "mom.raw.json").read_text())

		self.assertEqual(len(raw["sessions"]), 1)
		self.assertEqual(len(raw["sessions"][0]["typing"]), 15)      # ten copied and five made up
		self.assertEqual(len(raw["sessions"][0]["mouse"]), 14)
		self.assertTrue(any(row["holds"] for row in raw["sessions"][0]["typing"]))

	def test_the_description_mentions_both_halves(self):
		self.run_typing()
		self.run_mouse()
		text = "\n".join(self.app.outcome["lines"])

		self.assertIn("Typing:", text)
		self.assertIn("Mouse:", text)

	def test_too_little_typing_shows_an_error_and_r_starts_the_typing_again(self):
		app = calibrate.App("mom", None, clock=self.clock, rng=random.Random(2), phrases=2, trials=14)
		app.on_key_press("Return", "\r")

		while app.page == "typing":
			target = "best pizza near me tonight" if app.typing.compose else app.typing.target
			self.clock.tick(1.0)

			for ch in target:
				self.clock.tick(0.1)
				app.on_key_press(keysym_for(ch), ch)
				app.on_key_release(keysym_for(ch))

			app.on_key_press("Return", "\r")
			app.on_key_release("Return")

		app.on_key_press("Return", "\r")

		for _ in range(14):
			simulate_trial(app, self.clock)

		self.assertEqual(app.page, "results")
		self.assertIsNotNone(app.outcome["error"])
		self.assertFalse(Path(behavior.profile_path("mom")).exists())

		app.on_key_press("r", "r")

		self.assertEqual(app.page, "typing")

	def test_nothing_is_written_if_the_profile_is_not_to_be_saved(self):
		app = calibrate.App("mom", None, clock=self.clock, rng=random.Random(2), phrases=10, trials=14, save=False)
		self.app = app
		self.run_typing()
		self.run_mouse()

		self.assertIsNone(app.outcome["path"])
		self.assertFalse(Path(behavior.profile_path("mom")).exists())


if __name__ == "__main__":
	unittest.main()
