"""Second round of fixes, from an independent review of the feature switch and the behaviours behind it."""

import contextlib
import io
import json
import os
import random
import sys
import threading
import time
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import features
import mimic_typing
import query_sources
import search_scheduler as ss


class FeatureFileTestCase(unittest.TestCase):
	def setUp(self):
		patcher = mock.patch.dict(os.environ, {features.ENV: ""})
		patcher.start()
		self.addCleanup(patcher.stop)
		self.file = Path(features.FEATURES_FILE)

	def write(self, data):
		self.file.write_text(json.dumps(data))


class TestCaseInsensitiveNames(FeatureFileTestCase):
	def test_an_account_is_matched_whatever_the_case(self):
		self.write({"chains": ["Second"]})

		for name in ("second", "SECOND", "Second", " second "):
			self.assertTrue(features.enabled("chains", name), name)

		self.assertFalse(features.enabled("chains", "default"))

	def test_a_scheduler_owner_is_matched_whatever_the_case(self):
		self.write({"query_sessions": ["second"]})

		self.assertTrue(features.enabled("query_sessions", "Default,SECOND"))

	def test_switching_off_with_a_different_case_really_turns_it_off(self):
		features.switch("chains", "Second", True)
		features.switch("chains", "SECOND", False)

		self.assertFalse(features.enabled("chains", "second"))
		self.assertEqual(json.loads(self.file.read_text())["chains"], [])

	def test_switching_on_twice_in_different_cases_lists_it_once(self):
		features.switch("chains", "second", True)
		features.switch("chains", "Second", True)

		self.assertEqual(len(json.loads(self.file.read_text())["chains"]), 1)


class TestOneAccountPerEntry(FeatureFileTestCase):
	def test_a_comma_is_refused_rather_than_stored_as_a_name_that_never_matches(self):
		with self.assertRaisesRegex(ValueError, "one account"):
			features.switch("chains", "default,second", True)

		self.assertFalse(self.file.exists())

	def test_an_empty_name_is_refused(self):
		for name in ("", "   "):
			with self.assertRaises(ValueError):
				features.switch("chains", name, True)

	def test_surrounding_spaces_are_dropped(self):
		features.switch("chains", "  second  ", True)

		self.assertEqual(json.loads(self.file.read_text())["chains"], ["second"])


class TestSwitchingIsSafeToRace(FeatureFileTestCase):
	def test_many_switches_at_once_all_land(self):
		names = [f"acct{i}" for i in range(12)]
		errors = []

		def go(name):
			try:
				features.switch("chains", name, True)
			except Exception as exc:  # pragma: no cover - reported below
				errors.append(exc)

		threads = [threading.Thread(target=go, args=(n,)) for n in names]

		for t in threads:
			t.start()

		for t in threads:
			t.join()

		self.assertEqual(errors, [])
		self.assertEqual(sorted(json.loads(self.file.read_text())["chains"]), sorted(names))

	def test_a_race_cannot_lose_an_off(self):
		for n in range(6):
			features.switch("chains", f"a{n}", True)

		threads = [threading.Thread(target=features.switch, args=("chains", f"a{n}", False)) for n in range(6)]
		threads += [threading.Thread(target=features.switch, args=("query_sessions", "x", True))]

		for t in threads:
			t.start()

		for t in threads:
			t.join()

		data = json.loads(self.file.read_text())

		self.assertEqual(data["chains"], [])
		self.assertEqual(data["query_sessions"], ["x"])

	def test_the_lock_is_released_afterwards(self):
		features.switch("chains", "second", True)

		self.assertFalse(os.path.exists(f"{features.FEATURES_FILE}.lock"))

	def test_a_lock_that_is_held_makes_it_fail_clearly_not_hang(self):
		Path(f"{features.FEATURES_FILE}.lock").write_text("")

		with mock.patch.object(features, "LOCK_WAIT_SECONDS", 0.2):
			started = time.monotonic()

			with self.assertRaisesRegex(OSError, "held by another switch"):
				features.switch("chains", "second", True)

		self.assertLess(time.monotonic() - started, 2)
		os.unlink(f"{features.FEATURES_FILE}.lock")

	def test_a_lock_left_by_a_dead_process_is_taken_over(self):
		lock = f"{features.FEATURES_FILE}.lock"
		Path(lock).write_text("")
		old = time.time() - 600
		os.utime(lock, (old, old))

		features.switch("chains", "second", True)

		self.assertTrue(features.enabled("chains", "second"))
		self.assertFalse(os.path.exists(lock))

	def test_a_failed_write_leaves_no_temporary_file_and_says_so(self):
		with mock.patch.object(features.os, "replace", side_effect=PermissionError("in use")), self.assertRaises(OSError):
			features.switch("chains", "second", True)

		leftovers = [p.name for p in self.file.parent.iterdir() if p.name.startswith("features.json.")]

		self.assertEqual(leftovers, [])


class TestTheCommandLine(FeatureFileTestCase):
	def run_cli(self, *args):
		out = io.StringIO()

		with contextlib.redirect_stdout(out):
			code = features.main(list(args))

		return code, out.getvalue()

	def test_on_and_off_work_and_show_the_result(self):
		code, text = self.run_cli("on", "chains", "second")

		self.assertEqual(code, 0)
		self.assertIn("second", text)
		self.assertTrue(features.enabled("chains", "second"))

		code, _ = self.run_cli("off", "chains", "second")

		self.assertEqual(code, 0)
		self.assertFalse(features.enabled("chains", "second"))

	def test_a_bad_request_exits_non_zero_and_changes_nothing(self):
		for args in (("on", "made_up", "second"), ("on", "chains", "a,b"), ("on", "chains"), ("sideways", "chains", "x")):
			code, _ = self.run_cli(*args)

			self.assertEqual(code, 2, args)

		self.assertFalse(self.file.exists())

	def test_a_name_no_account_answers_to_is_called_out(self):
		with mock.patch("status.known_names", return_value=["default", "second"]):
			_, text = self.run_cli("on", "chains", "Secnod")

		self.assertIn("note:", text)
		self.assertIn("not an account", text)

	def test_a_known_account_in_any_case_gets_no_note(self):
		with mock.patch("status.known_names", return_value=["default", "second"]):
			_, text = self.run_cli("on", "chains", "SECOND")

		self.assertNotIn("note:", text)


class TestTypingCorrectionOnlyForSameLengthSlips(unittest.TestCase):
	class Always:
		"""A random source that always decides to correct, at the first opportunity."""

		def random(self):
			return 0.0

		def randint(self, a, b):
			return a

	def plan(self, typed, intended):
		keyboard = mimic_typing.KeyboardUtils(mock.Mock())

		return keyboard._plan_correction(list(typed), list(intended), self.Always())

	def test_a_same_length_slip_is_planned(self):
		self.assertIsNotNone(self.plan("abxd", "abcd"))

	def test_the_enter_after_the_text_is_allowed(self):
		self.assertIsNotNone(self.plan("abxd" + mimic_typing.Keys.ENTER, "abcd"))

	def test_an_inserted_character_is_not_corrected_it_would_leave_a_stray_letter(self):
		self.assertIsNone(self.plan("abxcd", "abcd"))
		self.assertIsNone(self.plan("abxc", "abc"))

	def test_a_dropped_character_is_not_corrected(self):
		self.assertIsNone(self.plan("acd", "abcd"))

	def test_an_extra_non_enter_key_at_the_end_is_not_corrected(self):
		self.assertIsNone(self.plan("abxdq", "abcd"))

	def test_text_with_no_slip_is_left_alone(self):
		self.assertIsNone(self.plan("abcd", "abcd"))


class TestHabitTimesAreDistinctAndInRange(unittest.TestCase):
	def setUp(self):
		patcher = mock.patch.dict(os.environ, {"REWARDS_FEATURES": "query_sessions"})
		patcher.start()
		self.addCleanup(patcher.stop)

	def test_planned_times_are_all_different_and_inside_what_is_left_of_the_day(self):
		for hour in (0, 7, 9, 13, 18, 21):
			now = datetime(2026, 10, 5, hour, 5)

			for seed in range(60):
				random.seed(seed)
				times = ss.draw_times(now, "someone")

				self.assertEqual(len(times), len(set(times)), (hour, seed))
				self.assertEqual(times, sorted(times))

				begin = max(now.replace(hour=ss.START_HOUR, minute=0, second=0, microsecond=0), now.replace(minute=7))
				end = now.replace(hour=ss.END_HOUR, minute=0, second=0, microsecond=0)

				for t in times:
					self.assertTrue(begin <= t < end, (hour, seed, t))

	def test_late_in_the_day_runs_do_not_pile_up_on_the_start_of_what_is_left(self):
		now = datetime(2026, 10, 5, 17, 30)
		begin = now.replace(minute=32)
		at_the_edge = 0
		total = 0

		for seed in range(300):
			random.seed(seed)

			for t in ss.draw_times(now, "someone"):
				total += 1
				at_the_edge += t == begin

		self.assertLess(at_the_edge / total, 0.02)

	def test_the_full_count_comes_back_for_a_whole_day(self):
		random.seed(3)

		self.assertEqual(len(ss.draw_times(datetime(2026, 10, 5, 0, 5), "someone")), ss.RUNS_PER_DAY)


class TestFollowUpLength(unittest.TestCase):
	def test_a_templated_follow_up_longer_than_a_person_would_type_is_not_used(self):
		seed = "one two three four five six seven eight"

		with mock.patch.object(query_sources, "trending_queries", lambda geo="US": [seed]), \
			mock.patch.object(query_sources, "wikipedia_topics", lambda days_ago=1: []), \
			mock.patch.object(query_sources, "suggestions", lambda term: []):
			batch = query_sources.related_queries(4, rng=random.Random(1), sessions=True)

		self.assertTrue(batch)
		self.assertTrue(all(len(q.split()) <= query_sources.MAX_FOLLOW_UP_WORDS for q in batch), batch)

	def test_short_seeds_still_get_follow_ups(self):
		with mock.patch.object(query_sources, "trending_queries", lambda geo="US": ["blue jays"]), \
			mock.patch.object(query_sources, "wikipedia_topics", lambda days_ago=1: []), \
			mock.patch.object(query_sources, "suggestions", lambda term: []):
			batch = query_sources.related_queries(4, rng=random.Random(2), sessions=True)

		self.assertGreater(len(batch), 1)


if __name__ == "__main__":
	unittest.main()
