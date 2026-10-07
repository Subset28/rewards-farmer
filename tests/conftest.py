"""Test setup shared by every test.

Pacing (rest days, partial quotas, a ramp for new accounts) is on by default, which
is right for a real account and wrong for a test that expects an account to do all
of its work today. Every test starts with pacing neutral and with its own state
file, so none of them depends on the date or writes into the real data-dir; the
tests of pacing itself set what they need.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


@pytest.fixture(autouse=True)
def neutral_pacing(tmp_path, monkeypatch):
	import pacing

	monkeypatch.setattr(pacing, "STATE_FILE", str(tmp_path / "pacing.json"))

	import health

	monkeypatch.setattr(health, "STATE_FILE", str(tmp_path / "health.json"))
	monkeypatch.setenv("REWARDS_REST_DAY_CHANCE", "0")
	monkeypatch.setenv("REWARDS_MIN_DAILY_FRACTION", "1")
	monkeypatch.setenv("REWARDS_RAMP_DAYS", "0")
	monkeypatch.setenv("REWARDS_KEEP_ORDER", "1")

	# The new behaviours (features.py) are off by default in real use and go live one account
	# at a time. The suite exercises them, so they are on here, with a features file of its own;
	# tests/test_features.py clears this to check the default.
	import features

	monkeypatch.setattr(features, "FEATURES_FILE", str(tmp_path / "features.json"))
	monkeypatch.setenv("REWARDS_FEATURES", ",".join(features.KNOWN))
