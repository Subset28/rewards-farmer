"""Behaviour changes go live one account at a time: off until switched on, per account."""

import json
import os
import sys
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import features
import mimic_typing
import queries
import query_sources
import search_scheduler as ss


class FeatureTestCase(unittest.TestCase):
	"""The suite turns every feature on (conftest); these tests start from a real, empty state."""

	def setUp(self):
		patcher = mock.patch.dict(os.environ, {features.ENV: ""})
		patcher.start()
		self.addCleanup(patcher.stop)
		self.file = Path(features.FEATURES_FILE)

	def write(self, data):
		self.file.write_text(json.dumps(data))


class TestEnabled(FeatureTestCase):
	def test_nothing_is_on_by_default(self):
		for feature in features.KNOWN:
			self.assertFalse(features.enabled(feature, "second"), feature)
			self.assertFalse(features.enabled(feature), feature)

	def test_a_listed_account_has_it_and_others_do_not(self):
		self.write({"chains": ["second"]})

		self.assertTrue(features.enabled("chains", "second"))
		self.assertFalse(features.enabled("chains", "default"))
		self.assertFalse(features.enabled("habits", "second"))

	def test_a_star_means_every_account_and_the_no_account_case(self):
		self.write({"habits": ["*"]})

		self.assertTrue(features.enabled("habits", "default"))
		self.assertTrue(features.enabled("habits", "second"))
		self.assertTrue(features.enabled("habits"))

	def test_a_scheduler_owner_that_works_several_accounts_has_it_if_any_of_them_does(self):
		self.write({"habits": ["second"]})

		self.assertTrue(features.enabled("habits", "default,second"))
		self.assertTrue(features.enabled("habits", "default, second"))
		self.assertFalse(features.enabled("habits", "default,third"))

	def test_with_no_account_only_a_star_or_the_environment_turns_it_on(self):
		self.write({"chains": ["second"]})

		self.assertFalse(features.enabled("chains"))

		with mock.patch.dict(os.environ, {features.ENV: "chains"}):
			self.assertTrue(features.enabled("chains"))
			self.assertTrue(features.enabled("chains", "default"))
			self.assertFalse(features.enabled("habits", "default"))

	def test_an_unknown_feature_is_never_on(self):
		self.write({"made_up": ["*"]})

		self.assertFalse(features.enabled("made_up", "second"))

		with mock.patch.dict(os.environ, {features.ENV: "made_up"}):
			self.assertFalse(features.enabled("made_up", "second"))

	def test_a_missing_or_damaged_file_means_everything_is_off(self):
		self.assertFalse(features.enabled("chains", "second"))

		for text in ("{not json", "[]", "null", '"x"', '{"chains": "second"}', '{"chains": [1, null, ["x"]]}'):
			self.file.write_text(text)
			self.assertFalse(features.enabled("chains", "second"), text)

	def test_the_file_is_read_each_time_so_a_change_needs_no_restart(self):
		self.assertFalse(features.enabled("chains", "second"))
		self.write({"chains": ["second"]})
		self.assertTrue(features.enabled("chains", "second"))
		self.write({"chains": []})
		self.assertFalse(features.enabled("chains", "second"))


class TestSwitching(FeatureTestCase):
	def test_on_and_off_round_trip_and_keep_everything_else(self):
		features.switch("chains", "second", True)
		features.switch("habits", "default", True)
		features.switch("chains", "default", True)
		features.switch("chains", "second", False)

		self.assertEqual(json.loads(self.file.read_text()), {"chains": ["default"], "query_sessions": [], "habits": ["default"]})

	def test_switching_on_twice_does_not_list_an_account_twice(self):
		features.switch("chains", "second", True)
		features.switch("chains", "second", True)

		self.assertEqual(json.loads(self.file.read_text())["chains"], ["second"])

	def test_an_unknown_feature_is_refused_and_nothing_is_written(self):
		with self.assertRaises(ValueError):
			features.switch("made_up", "second", True)

		self.assertFalse(self.file.exists())

	def test_no_temporary_file_is_left_behind(self):
		features.switch("chains", "second", True)

		self.assertEqual([p.name for p in self.file.parent.iterdir() if p.name.startswith("features")], ["features.json"])

	def test_the_description_says_what_is_on_and_what_is_off(self):
		features.switch("chains", "second", True)
		text = features.describe()

		self.assertIn("second", text)
		self.assertIn("off for everyone", text)

	def test_active_for_lists_an_accounts_features(self):
		features.switch("chains", "second", True)
		features.switch("habits", "second", True)

		self.assertEqual(features.active_for("second"), ["chains", "habits"])
		self.assertEqual(features.active_for("default"), [])


class TestTheOldBehaviourRunsUntilSwitchedOn(FeatureTestCase):
	"""Off means the original code, exactly: that is what makes a rollout one change at a time."""

	def keyboard(self, account):
		return mimic_typing.KeyboardUtils(mock.Mock(), None, account=account)

	def test_queries_use_the_original_draw_until_the_feature_is_on(self):
		with mock.patch.object(query_sources, "_related_queries_classic", return_value=["a"]) as classic, \
			mock.patch.object(query_sources, "_related_queries_sessions", return_value=["b"]) as sessions:
			self.assertEqual(query_sources.related_queries(1), ["a"])

		classic.assert_called_once()
		sessions.assert_not_called()

	def test_queries_use_sessions_when_asked_to(self):
		with mock.patch.object(query_sources, "_related_queries_classic", return_value=["a"]), \
			mock.patch.object(query_sources, "_related_queries_sessions", return_value=["b"]):
			self.assertEqual(query_sources.related_queries(1, sessions=True), ["b"])

	def test_each_accounts_own_setting_reaches_the_query_draw(self):
		features.switch("query_sessions", "second", True)

		for account, expected in (("second", True), ("default", False)):
			with mock.patch.object(queries, "_public_feeds", return_value=True), \
				mock.patch.object(queries, "selected_source", return_value="trends"), \
				mock.patch.object(queries.query_history, "avoid_for", return_value=set()), \
				mock.patch.object(queries.query_sources, "related_queries", return_value=["q"]) as draw:
				queries.related_queries(1, account=account)

			self.assertEqual(draw.call_args.kwargs["sessions"], expected, account)

	def test_run_times_are_uniform_until_habits_is_on_for_the_owner(self):
		now = datetime(2026, 10, 5, 7, 0)

		with mock.patch.object(ss, "_habit_time", side_effect=AssertionError("habits used while off")):
			self.assertTrue(ss.draw_times(now, "second"))

	def test_run_times_follow_the_habit_once_it_is_on_for_the_owner(self):
		features.switch("habits", "second", True)
		now = datetime(2026, 10, 5, 7, 0)

		distinct = (datetime(2026, 10, 5, 12, minute) for minute in range(0, 59))

		with mock.patch.object(ss, "_habit_time", side_effect=lambda *args: next(distinct)) as habit:
			ss.draw_times(now, "second")

		self.assertEqual(habit.call_count, ss.RUNS_PER_DAY)


class TestStatusShowsIt(FeatureTestCase):
	def test_status_lists_each_accounts_features(self):
		import status
		import points_log

		features.switch("chains", "second", True)

		with mock.patch.object(status.accounts, "configured", return_value=[]), \
			mock.patch.object(points_log, "history", return_value=[]), \
			mock.patch.object(status.safety, "blocked", return_value=None):
			self.assertIn("chains", status.account_block("second"))
			self.assertIn("switched on: none", status.account_block("default"))


if __name__ == "__main__":
	unittest.main()
