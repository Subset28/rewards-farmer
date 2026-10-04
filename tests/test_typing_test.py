"""Tests for the typing calibration test's logic (no window is opened).

	python -m unittest discover -s tests
"""

import os
import random
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import behavior
import make_behavior_profile
import typing_test as tt


def type_text(session, text, start=0.0, gap=0.12):
	"""Type `text` a character at a time, `gap` seconds apart; returns the time after the last one."""
	now = start
	outcome = None

	for char in text:
		outcome = session.press(char, char, now)
		session.release(char)
		now += gap

	return now, outcome


def quick(phrases=("abc def", "ghi jkl"), practice=None, **kwargs):
	return tt.TypingSession(phrases=phrases, practice=practice, shuffle=False, **kwargs)


class TestPhrases(unittest.TestCase):
	def test_there_are_twelve_search_style_phrases(self):
		self.assertEqual(len(tt.PHRASES), 12)
		self.assertEqual(len(set(tt.PHRASES)), 12)

	def test_every_phrase_is_plain_lower_case_a_person_could_type(self):
		for phrase in tt.PHRASES + (tt.PRACTICE,):
			self.assertEqual(phrase, phrase.lower())
			self.assertTrue(phrase.isprintable())
			self.assertEqual(phrase, " ".join(phrase.split()))
			self.assertTrue(20 <= len(phrase) <= 60, phrase)

	def test_there_are_plenty_of_keystrokes_to_build_a_profile_from(self):
		self.assertGreaterEqual(sum(len(p) for p in tt.PHRASES), 300)


class TestSessionBasics(unittest.TestCase):
	def test_typing_a_phrase_moves_on_to_the_next(self):
		session = quick()
		_, outcome = type_text(session, "abc def")

		self.assertEqual(outcome, "done")
		self.assertEqual(session.target, "ghi jkl")
		self.assertEqual(session.typed, "")

	def test_the_last_phrase_finishes_the_session(self):
		session = quick()
		type_text(session, "abc def")
		_, outcome = type_text(session, "ghi jkl", start=10)

		self.assertEqual(outcome, "finished")
		self.assertTrue(session.finished)
		self.assertEqual(session.target, "")

	def test_keys_after_the_end_are_ignored(self):
		session = quick(phrases=("abc",))
		type_text(session, "abc")

		self.assertEqual(session.press("x", "x", 99.0), "ignored")
		self.assertEqual(session.keystrokes, 3)

	def test_a_phrase_only_finishes_on_an_exact_match(self):
		session = quick(phrases=("abc",))
		type_text(session, "abd")

		self.assertFalse(session.finished)
		self.assertEqual(session.typed, "abd")
		self.assertEqual(session.matches, 2)

	def test_the_order_is_shuffled_but_every_phrase_appears_once_after_the_practice(self):
		session = tt.TypingSession(rng=random.Random(5))

		self.assertEqual(session.items[0], (tt.PRACTICE, False))
		self.assertEqual(sorted(p for p, counted in session.items if counted), sorted(tt.PHRASES))
		self.assertNotEqual([p for p, _ in session.items[1:]], list(tt.PHRASES))

	def test_without_shuffling_the_order_is_kept(self):
		self.assertEqual([p for p, _ in quick().items], ["abc def", "ghi jkl"])

	def test_progress_counts_only_the_real_phrases(self):
		session = tt.TypingSession(phrases=("abc", "def"), practice="xyz", shuffle=False)

		self.assertEqual((session.real_done, session.real_total, session.counted), (0, 2, False))

		type_text(session, "xyz")

		self.assertEqual((session.real_done, session.counted), (0, True))

		type_text(session, "abc", start=10)

		self.assertEqual(session.real_done, 1)


class TestGaps(unittest.TestCase):
	def test_the_gaps_inside_a_phrase_are_recorded_and_the_first_key_has_none(self):
		session = quick(phrases=("abcd",))
		type_text(session, "abcd", gap=0.2)

		self.assertEqual(len(session.intervals), 3)
		for gap in session.intervals:
			self.assertAlmostEqual(gap, 0.2)

	def test_the_pause_between_phrases_is_not_a_gap(self):
		session = quick()
		after, _ = type_text(session, "abc def", gap=0.1)
		type_text(session, "ghi jkl", start=after + 30, gap=0.1)

		self.assertEqual(len(session.intervals), 6 + 6)
		self.assertLess(max(session.intervals), 0.5)

	def test_the_practice_phrase_is_not_counted_at_all(self):
		session = tt.TypingSession(phrases=("abc",), practice="xyz", shuffle=False)
		type_text(session, "xyz")

		self.assertEqual(session.intervals, [])
		self.assertEqual(session.keystrokes, 0)

		type_text(session, "abc", start=10)

		self.assertEqual(session.keystrokes, 3)
		self.assertEqual(len(session.intervals), 2)

	def test_uneven_typing_is_recorded_as_it_happened(self):
		session = quick(phrases=("abcd",))
		now = 0.0

		for char, gap in zip("abcd", (0.0, 0.05, 0.4, 0.15)):
			now += gap
			session.press(char, char, now)
			session.release(char)

		for recorded, expected in zip(session.intervals, (0.05, 0.4, 0.15)):
			self.assertAlmostEqual(recorded, expected)


class TestWhatIsNotTyping(unittest.TestCase):
	def test_shift_and_other_modifiers_add_nothing(self):
		session = quick(phrases=("aBc",))
		session.press("a", "a", 0.0)
		session.release("a")
		session.press("Shift_L", "", 0.05)
		session.press("B", "B", 0.10)
		session.release("B")
		session.release("Shift_L")
		session.press("c", "c", 0.20)

		self.assertEqual(session.keystrokes, 3)
		self.assertEqual(len(session.intervals), 2)
		self.assertAlmostEqual(session.intervals[0], 0.10)

	def test_every_modifier_and_navigation_key_is_ignored(self):
		session = quick(phrases=("abc",))

		for index, keysym in enumerate(sorted(tt.NOT_TYPING)):
			self.assertEqual(session.press(keysym, "", index * 0.01), "ignored", keysym)

		self.assertEqual(session.keystrokes, 0)
		self.assertEqual(session.typed, "")

	def test_a_held_key_repeating_counts_once(self):
		session = quick(phrases=("aaa",))

		self.assertEqual(session.press("a", "a", 0.0), "typed")
		self.assertEqual(session.press("a", "a", 0.03), "ignored")
		self.assertEqual(session.press("a", "a", 0.06), "ignored")
		self.assertEqual(session.keystrokes, 1)
		self.assertEqual(session.typed, "a")

		session.release("a")

		self.assertEqual(session.press("a", "a", 0.20), "typed")
		self.assertEqual(len(session.intervals), 1)

	def test_paste_and_other_control_characters_are_ignored(self):
		session = quick(phrases=("abc",))

		for keysym, char in (("v", "\x16"), ("c", "\x03"), ("x", "\x18"), ("F5", ""), ("Unknown", "")):
			self.assertEqual(session.press(keysym, char, 0.0), "ignored", keysym)
			session.release(keysym)

		self.assertEqual(session.typed, "")
		self.assertEqual(session.keystrokes, 0)

	def test_a_multi_character_string_is_not_one_keystroke(self):
		session = quick(phrases=("abc",))

		self.assertEqual(session.press("x", "abc", 0.0), "ignored")
		self.assertEqual(session.typed, "")


class TestMistakes(unittest.TestCase):
	def test_a_wrong_letter_is_an_error_and_backspace_fixes_it(self):
		session = quick(phrases=("abc",))
		type_text(session, "ax", gap=0.1)

		self.assertEqual(session.errors, 1)
		self.assertEqual(session.matches, 1)

		session.press("BackSpace", "\x08", 0.5)
		session.release("BackSpace")
		_, outcome = type_text(session, "bc", start=0.7)

		self.assertEqual(outcome, "finished")

	def test_backspace_is_a_keystroke_with_a_gap_like_any_other(self):
		session = quick(phrases=("abc",))
		type_text(session, "ax", gap=0.1)
		before = session.keystrokes
		session.press("BackSpace", "\x08", 0.25)

		self.assertEqual(session.keystrokes, before + 1)
		self.assertAlmostEqual(session.intervals[-1], 0.25 - 0.1)

	def test_backspace_on_an_empty_line_does_nothing_harmful(self):
		session = quick(phrases=("abc",))

		self.assertEqual(session.press("BackSpace", "\x08", 0.0), "back")
		self.assertEqual(session.typed, "")

	def test_mistakes_in_the_practice_phrase_are_not_counted(self):
		session = tt.TypingSession(phrases=("abc",), practice="xyz", shuffle=False)
		type_text(session, "q")

		self.assertEqual(session.errors, 0)

	def test_a_letter_typed_after_a_mistake_is_not_a_second_error_unless_wrong_again(self):
		session = quick(phrases=("abc",))
		type_text(session, "ax")

		self.assertEqual(session.errors, 1)

		type_text(session, "y", start=1.0)

		self.assertEqual(session.errors, 2)


class TestSummary(unittest.TestCase):
	def test_an_empty_session_summarises_to_zeros(self):
		stats = quick().summary()

		self.assertEqual(stats["keystrokes"], 0)
		self.assertEqual(stats["words_per_minute"], 0.0)
		self.assertEqual(stats["fast_share"], 0.0)

	def test_the_figures_follow_the_typing(self):
		session = quick(phrases=("abcdefghij",))
		type_text(session, "abcdefghij", gap=0.15)
		stats = session.summary()

		self.assertEqual(stats["keystrokes"], 10)
		self.assertAlmostEqual(stats["median_gap"], 0.15)
		self.assertEqual(stats["medium_share"], 1.0)
		self.assertEqual(stats["fast_share"], 0.0)
		self.assertGreater(stats["words_per_minute"], 50)

	def test_the_shares_match_what_the_profile_builder_would_compute(self):
		session = tt.TypingSession(rng=random.Random(3))
		now = 0.0
		pattern = [0.05, 0.05, 0.15, 0.3]

		while not session.finished:
			target = session.target
			for index, char in enumerate(target):
				now += pattern[index % len(pattern)]
				session.press(char, char, now)
				session.release(char)

			now += 5

		fast, medium = behavior.typing_shares(session.intervals)
		stats = session.summary()

		self.assertAlmostEqual(stats["fast_share"], fast, places=3)
		self.assertAlmostEqual(stats["medium_share"], medium, places=3)

	def test_a_long_pause_does_not_spoil_the_speed(self):
		session = quick(phrases=("abcdefghij",))
		now = 0.0

		for index, char in enumerate("abcdefghij"):
			now += 30 if index == 5 else 0.15
			session.press(char, char, now)
			session.release(char)

		self.assertGreater(session.summary()["words_per_minute"], 50)


class TestOutput(unittest.TestCase):
	def test_the_gaps_are_written_one_per_line_and_read_back_by_the_profile_builder(self):
		with tempfile.TemporaryDirectory() as directory:
			path = os.path.join(directory, "keypress_times.txt")
			session = tt.TypingSession(rng=random.Random(1))
			now = 0.0

			while not session.finished:
				for char in session.target:
					now += 0.11
					session.press(char, char, now)
					session.release(char)

				now += 3

			session.write_intervals(path)
			read = make_behavior_profile.read_intervals(path)

			self.assertEqual(len(read), len(session.intervals))
			self.assertGreaterEqual(len(read), tt.MIN_INTERVALS)
			self.assertEqual(behavior.typing_shares(read), behavior.typing_shares(session.intervals))


class TestMain(unittest.TestCase):
	def run_main(self, window, directory):
		out = os.path.join(directory, "keypress_times.txt")

		with mock.patch.object(tt, "run_window", window), mock.patch("builtins.print") as shown:
			code = tt.main(["typing_test.py", "--out", out])

		return code, out, "\n".join(str(call.args[0]) for call in shown.call_args_list)

	def test_a_finished_test_writes_the_file_and_prints_a_summary(self):
		def window(session):
			now = 0.0

			while not session.finished:
				for char in session.target:
					now += 0.12
					session.press(char, char, now)
					session.release(char)

				now += 2

			return True

		with tempfile.TemporaryDirectory() as directory:
			code, out, output = self.run_main(window, directory)

			self.assertEqual(code, 0)
			self.assertTrue(os.path.exists(out))
			self.assertIn("words per minute", output)
			self.assertIn("make_behavior_profile.py", output)

	def test_quitting_early_writes_nothing(self):
		with tempfile.TemporaryDirectory() as directory:
			code, out, output = self.run_main(lambda session: False, directory)

			self.assertEqual(code, 1)
			self.assertFalse(os.path.exists(out))
			self.assertIn("Nothing was written", output)

	def test_too_few_gaps_writes_nothing_and_says_so(self):
		with tempfile.TemporaryDirectory() as directory:
			code, out, output = self.run_main(lambda session: True, directory)

			self.assertEqual(code, 1)
			self.assertFalse(os.path.exists(out))
			self.assertIn(f"need at least {tt.MIN_INTERVALS}", output)


if __name__ == "__main__":
	unittest.main()
