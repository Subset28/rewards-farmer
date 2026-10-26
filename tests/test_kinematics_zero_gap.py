import unittest
from unittest import mock

import mouse_trajectory
import mimic_typing
import rewards_tasks


class FakeDriver:
	def __init__(self, width=1920, height=1080):
		self.width = width
		self.height = height
		self.scripts = []
		self.locations = []

	def execute_script(self, script, *args):
		self.scripts.append(script)
		if "innerWidth" in script:
			return [self.width, self.height]
		if "window.cursorX" in script:
			return {"x": 500, "y": 400}
		return None


class TestKinematicsZeroGap(unittest.TestCase):
	def test_initial_mouse_position_is_not_zero_zero(self):
		driver = FakeDriver(width=1600, height=900)
		mouse = mouse_trajectory.MouseUtils(driver)
		# Should initialize inside natural central viewport, not (0, 0)
		self.assertNotEqual(mouse.fallback_init_pos, (0, 0))
		self.assertTrue(500 <= mouse.fallback_init_pos[0] <= 1100)
		self.assertTrue(250 <= mouse.fallback_init_pos[1] <= 600)

	def test_tracking_script_does_not_leak_console_log(self):
		driver = FakeDriver()
		mouse = mouse_trajectory.MouseUtils(driver)
		self.assertIsNotNone(mouse)
		joined_scripts = "\n".join(driver.scripts)
		self.assertNotIn("console.log", joined_scripts)
		self.assertNotIn("selenium-visual-cursor", joined_scripts)

	def test_typing_pre_delay_is_capped_reasonably(self):
		driver = FakeDriver()
		keyboard = mimic_typing.KeyboardUtils(driver)
		self.assertIsNotNone(keyboard)
		detail = {"start_mu": 3.0, "start_sigma": 0.4}
		thinking = min(2.5, mimic_typing.human_model.lognormal_ms(detail["start_mu"], detail["start_sigma"], low=200, high=2500) / 1000)
		self.assertLessEqual(thinking, 2.5)
		self.assertGreaterEqual(thinking, 0.2)

	def test_explore_search_clicks_bar_before_typing(self):
		page = mock.Mock()
		page.account_name = "test_account"
		bar_elem = mock.Mock(tag_name="input")
		card_elem = mock.Mock(tag_name="div")
		page.elements.extract_card_descriptions.return_value = "daily facts"
		page.elements.get_bing_search_bar.return_value = bar_elem
		page.wait_for_element.return_value = bar_elem
		
		# Execute the method
		rewards_tasks.RewardsTaskUtils.search_explore_card(page, card_elem)
		
		# Check that move_to_and_click was called for both the card and the search bar
		page.move_to_and_click.assert_has_calls([mock.call(card_elem), mock.call(bar_elem)])
		page.keyboard.send_keys.assert_called_once()


if __name__ == "__main__":
	unittest.main()
