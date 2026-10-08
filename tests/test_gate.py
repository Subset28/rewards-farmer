import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import behavior
import gate


def write_profile(folder: str, name: str, notes: dict | None = None):
	profile = behavior.provisional(name)
	path = os.path.join(folder, f"{name}.json")
	typing = {"fast_share": 0.4, "medium_share": 0.4, "detail": {}}
	mouse = {"fitts_a": 0.2, "fitts_b": 0.13, "detail": {}}

	with open(path, "w", encoding="utf-8") as handle:
		json.dump({"typing": typing, "mouse": mouse, "notes": notes or {}}, handle)

	return path, profile


class TestTheGate(unittest.TestCase):
	def setUp(self):
		self.folder = tempfile.TemporaryDirectory()
		self.addCleanup(self.folder.cleanup)
		patch = mock.patch.object(behavior, "profile_path", lambda name: os.path.join(self.folder.name, f"{name}.json"))
		patch.start()
		self.addCleanup(patch.stop)
		alerts = mock.patch.object(gate, "ALERTS_FILE", os.path.join(self.folder.name, "gate_alerts.json"))
		alerts.start()
		self.addCleanup(alerts.stop)

	def test_an_account_with_no_recording_waits_and_is_told_how(self):
		why = gate.reason("mom")

		self.assertIn("calibrate.py mom", why)

	def test_a_recorded_account_runs(self):
		write_profile(self.folder.name, "second")

		self.assertIsNone(gate.reason("second"))

	def test_a_stored_score_that_can_be_told_apart_holds_the_account_back(self):
		write_profile(self.folder.name, "second", {"typing_score": 0.82, "mouse_score": 0.55})

		self.assertIn("0.82", gate.reason("second"))

	def test_scores_near_a_coin_flip_pass(self):
		write_profile(self.folder.name, "second", {"typing_score": 0.53, "mouse_score": 0.58})

		self.assertIsNone(gate.reason("second"))

	def test_an_older_profile_with_no_score_passes(self):
		write_profile(self.folder.name, "second", {"recorded_with": "calibrate.py"})

		self.assertIsNone(gate.reason("second"))

	def test_the_owner_is_told_once_a_day_not_every_run(self):
		sent = []
		send = lambda *args, **kwargs: sent.append((args, kwargs))

		self.assertIsNotNone(gate.blocked("mom", send=send))
		self.assertIsNotNone(gate.blocked("mom", send=send))

		self.assertEqual(len(sent), 1)
		self.assertEqual(sent[0][1]["account"], "mom")

	def test_a_recorded_account_is_not_alerted(self):
		write_profile(self.folder.name, "second")
		sent = []

		self.assertIsNone(gate.blocked("second", send=lambda *a, **k: sent.append(1)))
		self.assertEqual(sent, [])
