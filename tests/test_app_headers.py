"""The old header override is off unless asked for: it made the browser disagree with itself."""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import rewards_tasks


class AppHeaderTestCase(unittest.TestCase):
	def build(self, setting):
		"""A RewardsTaskUtils on a fake driver, with REWARDS_APP_HEADERS set to `setting` (None: not set)."""
		driver = mock.MagicMock()
		environment = {k: v for k, v in os.environ.items() if k != "REWARDS_APP_HEADERS"}

		if setting is not None:
			environment["REWARDS_APP_HEADERS"] = setting

		patches = (
			mock.patch.dict(os.environ, environment, clear=True),
			mock.patch.object(rewards_tasks.behavior, "load"),
			mock.patch.object(rewards_tasks.tab_utils, "TabUtils"),
			mock.patch.object(rewards_tasks.mouse_trajectory, "MouseUtils"),
			mock.patch.object(rewards_tasks.mimic_typing, "KeyboardUtils"),
			mock.patch.object(rewards_tasks.element_selectors, "ElementSelectionUtils"),
			mock.patch.object(rewards_tasks.RewardsTaskUtils, "verify_signed_in_state"),
			mock.patch.object(rewards_tasks.logger, "info"),
		)

		for patch in patches:
			patch.start()

		try:
			rewards_tasks.RewardsTaskUtils(driver, "default")
		finally:
			for patch in reversed(patches):
				patch.stop()

		return driver

	def commands(self, driver):
		return [call.args[0] for call in driver.execute_cdp_cmd.call_args_list]


class TestAppHeaders(AppHeaderTestCase):
	def test_by_default_no_header_override_is_sent(self):
		driver = self.build(None)

		self.assertNotIn("Network.setExtraHTTPHeaders", self.commands(driver))
		self.assertNotIn("Network.enable", self.commands(driver))

	def test_any_value_but_one_leaves_it_off(self):
		for value in ("0", "true", "yes", ""):
			self.assertNotIn("Network.setExtraHTTPHeaders", self.commands(self.build(value)), value)

	def test_one_turns_the_old_behaviour_back_on(self):
		driver = self.build("1")
		sent = [c for c in driver.execute_cdp_cmd.call_args_list if c.args[0] == "Network.setExtraHTTPHeaders"]

		self.assertEqual(len(sent), 1)
		self.assertEqual(sent[0].args[1]["headers"]["X-Rewards-Source"], "msrewards-desktop")

	def test_the_rewards_home_page_is_still_opened_either_way(self):
		for setting in (None, "1"):
			driver = self.build(setting)
			driver.get.assert_called_with(rewards_tasks.REWARDS_HOME_URL)


if __name__ == "__main__":
	unittest.main()
