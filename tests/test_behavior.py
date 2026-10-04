"""Tests for per-account typing and mouse behavior profiles.

	python -m unittest discover -s tests
"""

import json
import os
import random
import sys
import tempfile
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import behavior
import make_behavior_profile
import mimic_typing
import mouse_trajectory


class ProfileDirTestCase(unittest.TestCase):
	def setUp(self):
		directory = tempfile.TemporaryDirectory()
		self.addCleanup(directory.cleanup)
		self.dir = os.path.join(directory.name, "behavior")
		patcher = mock.patch.object(behavior, "PROFILE_DIR", self.dir)
		patcher.start()
		self.addCleanup(patcher.stop)

	def write(self, name, raw):
		os.makedirs(self.dir, exist_ok=True)

		with open(os.path.join(self.dir, f"{name}.json"), "w", encoding="utf-8") as handle:
			if isinstance(raw, str):
				handle.write(raw)
			else:
				json.dump(raw, handle)


class TestBaseline(unittest.TestCase):
	def test_the_original_account_keeps_exactly_the_original_constants(self):
		b = behavior.baseline()

		self.assertEqual(b.typing_weights[0], mimic_typing.FIRST_INTERVAL_PROBABILITY)
		self.assertEqual(b.typing_weights[1], mimic_typing.SECOND_INTERVAL_PROBABILITY)
		self.assertAlmostEqual(b.typing_weights[2], mimic_typing.THIRD_INTERVAL_PROBABILITY)
		self.assertEqual(b.fitts_a, mouse_trajectory.FITTS_LAW_A)
		self.assertEqual(b.fitts_b, mouse_trajectory.FITTS_LAW_B)


class TestProvisional(unittest.TestCase):
	def test_it_is_stable_for_the_same_name(self):
		def numbers(b):
			return (b.fast_share, b.medium_share, b.fitts_a, b.fitts_b)

		self.assertEqual(behavior.provisional("second"), behavior.provisional("second"))
		self.assertEqual(numbers(behavior.provisional("Second")), numbers(behavior.provisional("second")))

	def test_different_accounts_get_different_hands(self):
		names = ["second", "third", "alice", "bob", "spare", "work", "x1", "x2"]
		profiles = {behavior.provisional(n).fitts_a for n in names}

		self.assertEqual(len(profiles), len(names))

	def test_it_is_never_the_baseline_and_always_believable(self):
		base = behavior.baseline()

		for i in range(200):
			p = behavior.provisional(f"account-{i}")

			self.assertNotEqual((p.fast_share, p.medium_share, p.fitts_a, p.fitts_b), (base.fast_share, base.medium_share, base.fitts_a, base.fitts_b))
			self.assertEqual(p.source, "provisional")
			behavior.validate(p.fast_share, p.medium_share, p.fitts_a, p.fitts_b)
			self.assertGreater(p.slow_share, 0)
			self.assertAlmostEqual(sum(p.typing_weights), 1.0)


class TestValidate(unittest.TestCase):
	def test_a_plausible_profile_passes(self):
		behavior.validate(0.5, 0.4, 0.4, 0.15)

	def test_a_slightly_negative_intercept_is_a_normal_fitts_result(self):
		# Measured on a real person: MT = -0.0358 + 0.2719 * ID, R^2 = 0.33.
		behavior.validate(0.5, 0.4, -0.0358, 0.2719)
		behavior.validate(0.5, 0.4, 0.0, 0.15)
		behavior.validate(0.5, 0.4, behavior.MIN_FITTS_A, 0.15)

	def test_implausible_ones_are_refused(self):
		for args in (
			(0.01, 0.4, 0.4, 0.15),     # nobody types that slowly
			(0.7, 0.4, 0.4, 0.15),      # shares add past 1
			(0.5, 0.5, 0.4, 0.15),      # no slow tail at all
			(0.5, -0.1, 0.4, 0.15),
			(0.5, 0.4, -0.6, 0.15),
			(0.5, 0.4, 0.4, 0),
			(0.5, 0.4, 5.0, 0.15),
			(0.5, 0.4, 0.4, 3.0),
			(float("nan"), 0.4, 0.4, 0.15),
			("a", 0.4, 0.4, 0.15),
			(True, 0.4, 0.4, 0.15),
		):
			with self.subTest(args=args):
				with self.assertRaises(behavior.ProfileError):
					behavior.validate(*args)


class TestLoad(ProfileDirTestCase):
	def test_a_recorded_profile_is_used(self):
		self.write("second", {"typing": {"fast_share": 0.42, "medium_share": 0.45}, "mouse": {"fitts_a": 0.5, "fitts_b": 0.2}})
		b = behavior.load("second")

		self.assertEqual((b.source, b.fast_share, b.medium_share, b.fitts_a, b.fitts_b), ("recorded", 0.42, 0.45, 0.5, 0.2))

	def test_the_original_account_with_no_file_is_the_baseline(self):
		self.assertEqual(behavior.load("default").source, "baseline")

	def test_another_account_with_no_file_is_provisional_and_says_so(self):
		with self.assertLogs(behavior.logger, level="WARNING") as logs:
			b = behavior.load("second")

		self.assertEqual(b.source, "provisional")
		self.assertIn("no recorded behavior profile", logs.output[0])

	def test_two_accounts_with_no_files_never_share_hands(self):
		with self.assertLogs(behavior.logger, level="WARNING"):
			one, two = behavior.load("one"), behavior.load("two")

		self.assertNotEqual((one.fast_share, one.fitts_a), (two.fast_share, two.fitts_a))

	def test_a_broken_file_falls_back_to_provisional_not_the_baseline(self):
		for label, content in (
			("not json", "{nope"),
			("missing fields", {"typing": {}}),
			("out of range", {"typing": {"fast_share": 0.01, "medium_share": 0.4}, "mouse": {"fitts_a": 0.4, "fitts_b": 0.15}}),
		):
			with self.subTest(case=label):
				self.write("second", content)

				with self.assertLogs(behavior.logger, level="WARNING"):
					b = behavior.load("second")

				self.assertEqual(b.source, "provisional")
				self.assertNotEqual(b.fitts_a, behavior.BASELINE_FITTS[0])

	def test_the_original_account_can_have_a_recorded_profile_too(self):
		self.write("default", {"typing": {"fast_share": 0.3, "medium_share": 0.5}, "mouse": {"fitts_a": 0.6, "fitts_b": 0.25}})

		self.assertEqual(behavior.load("default").source, "recorded")

	def test_save_validates_before_writing_anything(self):
		with self.assertRaises(behavior.ProfileError):
			behavior.save("second", 0.01, 0.4, 0.4, 0.15)

		self.assertFalse(os.path.exists(behavior.profile_path("second")))

	def test_save_and_load_round_trip(self):
		behavior.save("second", 0.44, 0.46, 0.52, 0.19)
		b = behavior.load("second")

		self.assertEqual((b.source, b.fast_share, b.medium_share, b.fitts_a, b.fitts_b), ("recorded", 0.44, 0.46, 0.52, 0.19))


class TestTypingShares(unittest.TestCase):
	def test_the_shares_come_from_the_intervals(self):
		intervals = [0.05] * 50 + [0.15] * 30 + [0.4] * 20

		self.assertEqual(behavior.typing_shares(intervals), (0.5, 0.3))

	def test_pauses_to_think_are_left_out(self):
		intervals = [0.05] * 50 + [0.15] * 30 + [0.4] * 20 + [5.0] * 100 + [30.0] * 10

		self.assertEqual(behavior.typing_shares(intervals), (0.5, 0.3))

	def test_too_little_data_is_refused(self):
		with self.assertRaises(behavior.ProfileError):
			behavior.typing_shares([0.1] * 10)

	def test_negative_intervals_from_a_clock_glitch_are_ignored(self):
		self.assertEqual(behavior.typing_shares([-1.0] + [0.05] * 50 + [0.15] * 30 + [0.4] * 20), (0.5, 0.3))


class TestWiring(unittest.TestCase):
	def test_the_keyboard_uses_the_accounts_weights(self):
		driver = mock.Mock()
		profile = behavior.Behavior("x", "recorded", 0.2, 0.3, 0.4, 0.15)
		keyboard = mimic_typing.KeyboardUtils(driver, profile)

		self.assertEqual(keyboard.weights, [0.2, 0.3, 0.5])

		with mock.patch.object(mimic_typing.random, "choices", wraps=random.choices) as choose, \
			mock.patch.object(mimic_typing, "ActionChains"):
			keyboard.send_keys("ab")

		for call in choose.call_args_list:
			self.assertEqual(call.kwargs["weights"], [0.2, 0.3, 0.5])

	def test_the_keyboard_without_a_profile_keeps_the_original_constants(self):
		keyboard = mimic_typing.KeyboardUtils(mock.Mock())

		self.assertEqual(keyboard.weights[0], mimic_typing.FIRST_INTERVAL_PROBABILITY)

	def test_the_fitts_time_uses_the_accounts_constants(self):
		slow = mouse_trajectory.get_movement_time_from_fitts_law(400, 40, 0.8, 0.3)
		fast = mouse_trajectory.get_movement_time_from_fitts_law(400, 40, 0.3, 0.1)
		default = mouse_trajectory.get_movement_time_from_fitts_law(400, 40)

		self.assertGreater(slow, default)
		self.assertLess(fast, default)
		self.assertAlmostEqual(
			default,
			mouse_trajectory.get_movement_time_from_fitts_law(400, 40, mouse_trajectory.FITTS_LAW_A, mouse_trajectory.FITTS_LAW_B),
		)

	def test_the_mouse_takes_the_accounts_constants(self):
		profile = behavior.Behavior("x", "recorded", 0.5, 0.4, 0.77, 0.33)

		with mock.patch.object(mouse_trajectory.MouseUtils, "reinitialize"):
			mouse = mouse_trajectory.MouseUtils(mock.Mock(), profile)
			plain = mouse_trajectory.MouseUtils(mock.Mock())

		self.assertEqual((mouse.fitts_a, mouse.fitts_b), (0.77, 0.33))
		self.assertEqual((plain.fitts_a, plain.fitts_b), (None, None))


class TestMakeProfileTool(ProfileDirTestCase):
	def setUp(self):
		super().setUp()
		directory = tempfile.TemporaryDirectory()
		self.addCleanup(directory.cleanup)
		self.keys = os.path.join(directory.name, "keypress_times.txt")

		with open(self.keys, "w") as handle:
			handle.write("\n".join(str(i) for i in [0.05] * 60 + [0.15] * 25 + [0.4] * 15))

	def test_it_writes_a_profile_from_recordings(self):
		with mock.patch("builtins.print"):
			code = make_behavior_profile.main(["t", "second", "--keys", self.keys, "--fitts", "0.45", "0.17"])

		self.assertEqual(code, 0)
		b = behavior.load("second")
		self.assertEqual((b.source, b.fast_share, b.medium_share, b.fitts_a, b.fitts_b), ("recorded", 0.6, 0.25, 0.45, 0.17))

	def test_too_little_typing_is_a_clear_failure_and_writes_nothing(self):
		with open(self.keys, "w") as handle:
			handle.write("0.1\n0.2\n")

		with mock.patch("builtins.print") as shown:
			code = make_behavior_profile.main(["t", "second", "--keys", self.keys, "--fitts", "0.45", "0.17"])

		self.assertEqual(code, 1)
		self.assertIn("at least 30", shown.call_args.args[0])
		self.assertFalse(os.path.exists(behavior.profile_path("second")))

	def test_a_missing_file_is_a_clear_failure(self):
		with mock.patch("builtins.print"):
			code = make_behavior_profile.main(["t", "second", "--keys", self.keys + ".nope", "--fitts", "0.45", "0.17"])

		self.assertEqual(code, 1)

	def test_both_recordings_are_required(self):
		with self.assertRaises(SystemExit), mock.patch("sys.stderr"):
			make_behavior_profile.main(["t", "second", "--keys", self.keys])

	def test_show_reports_which_kind_of_profile_is_in_use(self):
		with mock.patch("builtins.print") as shown, self.assertLogs(behavior.logger, level="WARNING"):
			make_behavior_profile.main(["t", "--show", "second"])

		output = "\n".join(str(call.args[0]) for call in shown.call_args_list)

		self.assertIn("provisional profile", output)
		self.assertIn("not recorded", output)


if __name__ == "__main__":
	unittest.main()
