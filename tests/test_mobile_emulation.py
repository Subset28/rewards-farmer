"""Tests for presenting the browser as a phone.

	python -m unittest discover -s tests
"""

import os
import sys
import unittest
from unittest import mock

SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src")
sys.path.insert(0, SRC)

import accounts
import browser
import mobile_emulation as me

FULL_VERSION = "154.0.4258.37"


class FakeDriver:
	def __init__(self, version=FULL_VERSION, fail_on=None, script_result=None):
		self.capabilities = {"browserVersion": version} if version else {}
		self.fail_on = fail_on
		self.commands = []
		self.executed = []
		self.script_result = script_result
		self.quit_called = False

	def execute_cdp_cmd(self, command, params):
		if command == self.fail_on:
			raise RuntimeError("boom")

		self.commands.append((command, params))

	def execute_script(self, script, *args):
		return self.script_result

	def execute(self, command, params=None):
		self.executed.append((command, params))

	def quit(self):
		self.quit_called = True


class TestIdentity(unittest.TestCase):
	def setUp(self):
		self.device = me.DEVICES[0]

	def test_it_is_edge_on_android_not_safari_on_ios(self):
		ua = me.user_agent(self.device, FULL_VERSION)

		self.assertIn("Android", ua)
		self.assertIn("EdgA/154", ua)
		self.assertIn(self.device.model, ua)
		self.assertNotIn("iPhone", ua)
		self.assertNotIn("Safari/60", ua)

	def test_the_user_agent_and_the_client_hints_agree(self):
		ua = me.user_agent(self.device, FULL_VERSION)
		meta = me.user_agent_metadata(self.device, FULL_VERSION)

		self.assertTrue(meta["mobile"])
		self.assertEqual(meta["platform"], "Android")
		self.assertEqual(meta["model"], self.device.model)
		self.assertIn(f"Android {self.device.android_version}", ua)
		self.assertEqual(meta["platformVersion"], self.device.platform_version)

		for brand in meta["brands"][:2]:
			self.assertEqual(brand["version"], "154")
			self.assertIn(f"/{brand['version']}.0.0.0", ua)

		full = {b["brand"]: b["version"] for b in meta["fullVersionList"]}
		self.assertEqual(full["Microsoft Edge"], FULL_VERSION)

	def test_the_brand_names_match_what_desktop_edge_reports(self):
		names = [b["brand"] for b in me.user_agent_metadata(self.device, FULL_VERSION)["brands"]]

		self.assertEqual(names, ["Chromium", "Microsoft Edge", "Not A(Brand"])

	def test_an_unreadable_version_is_an_error_not_a_guess(self):
		with self.assertRaises(me.MobileEmulationError):
			me.user_agent(self.device, "")

		with self.assertRaises(me.MobileEmulationError):
			me.user_agent(self.device, "latest")


class TestAcceptLanguage(unittest.TestCase):
	def test_it_carries_no_q_values_because_the_browser_adds_them(self):
		self.assertNotIn("q=", me.ACCEPT_LANGUAGE)


class TestDeviceFor(unittest.TestCase):
	def test_the_same_account_is_always_the_same_phone(self):
		for name in ("default", "personal", "work", "a"):
			self.assertEqual(me.device_for(name), me.device_for(name))

	def test_every_device_is_one_we_know(self):
		for name in ("x", "y", "z", "one", "two", "three", "four"):
			self.assertIn(me.device_for(name), me.DEVICES)

	def test_different_accounts_are_not_all_the_same_phone(self):
		seen = {me.device_for(f"account-{i}") for i in range(40)}

		self.assertGreater(len(seen), 1)


class TestApply(unittest.TestCase):
	def test_it_sets_the_whole_phone_and_all_of_it_agrees(self):
		driver = FakeDriver()
		device = me.DEVICES[2]
		me.apply(driver, device)

		by_name = dict(driver.commands)

		self.assertEqual(
			set(by_name),
			{
				"Emulation.setDeviceMetricsOverride",
				"Emulation.setTouchEmulationEnabled",
				"Emulation.setEmitTouchEventsForMouse",
				"Emulation.setUserAgentOverride",
			},
		)
		self.assertEqual(by_name["Emulation.setDeviceMetricsOverride"]["width"], device.width)
		self.assertEqual(by_name["Emulation.setDeviceMetricsOverride"]["deviceScaleFactor"], device.scale)
		self.assertTrue(by_name["Emulation.setDeviceMetricsOverride"]["mobile"])
		self.assertGreater(by_name["Emulation.setTouchEmulationEnabled"]["maxTouchPoints"], 0)

		override = by_name["Emulation.setUserAgentOverride"]
		self.assertEqual(override["platform"], me.PLATFORM)
		self.assertEqual(override["userAgent"], me.user_agent(device, FULL_VERSION))
		self.assertEqual(override["userAgentMetadata"], me.user_agent_metadata(device, FULL_VERSION))

	def test_a_failing_step_raises_and_names_the_step(self):
		for failing in (
			"Emulation.setDeviceMetricsOverride",
			"Emulation.setTouchEmulationEnabled",
			"Emulation.setEmitTouchEventsForMouse",
			"Emulation.setUserAgentOverride",
		):
			with self.subTest(step=failing):
				with self.assertRaises(me.MobileEmulationError) as ctx:
					me.apply(FakeDriver(fail_on=failing), me.DEVICES[0])

				self.assertIn(failing, str(ctx.exception))

	def test_a_missing_browser_version_raises_before_anything_is_changed(self):
		driver = FakeDriver(version=None)

		with self.assertRaises(me.MobileEmulationError):
			me.apply(driver, me.DEVICES[0])

		self.assertEqual(driver.commands, [])


class TestStartDriverMobile(unittest.TestCase):
	def _account(self):
		return accounts.Account(name="throwaway", user_data_dir="/tmp/none", profile_name="Default")

	def test_a_phone_session_is_the_accounts_phone(self):
		driver = FakeDriver()
		driver.script = mock.Mock()

		with mock.patch.object(browser.webdriver, "Edge", return_value=driver), \
			mock.patch.object(browser, "build_service", return_value=None):
			self.assertIs(browser.start_driver(self._account(), mobile=True), driver)

		override = dict(driver.commands)["Emulation.setUserAgentOverride"]
		self.assertIn(me.device_for("throwaway").model, override["userAgent"])

	def test_a_desktop_session_is_left_alone(self):
		driver = FakeDriver()
		driver.script = mock.Mock()

		with mock.patch.object(browser.webdriver, "Edge", return_value=driver), \
			mock.patch.object(browser, "build_service", return_value=None):
			browser.start_driver(self._account())

		self.assertFalse(any(c.startswith("Emulation.") for c, _ in driver.commands))

	def test_a_phone_that_cannot_be_completed_shuts_the_browser_down_and_raises(self):
		driver = FakeDriver(fail_on="Emulation.setUserAgentOverride")
		driver.script = mock.Mock()

		with mock.patch.object(browser.webdriver, "Edge", return_value=driver), \
			mock.patch.object(browser, "build_service", return_value=None):
			with self.assertRaises(me.MobileEmulationError):
				browser.start_driver(self._account(), mobile=True)

		self.assertTrue(driver.quit_called)


class TestTouchGestures(unittest.TestCase):
	def _pointer_events(self, driver):
		command, params = driver.executed[-1]
		pointer = next(a for a in params["actions"] if a["type"] == "pointer")

		return pointer["parameters"]["pointerType"], pointer["actions"]

	def test_a_tap_is_a_touch_press_inside_the_element(self):
		driver = FakeDriver(script_result=[100, 200, 80, 40])
		me.tap(driver, object())

		kind, events = self._pointer_events(driver)
		types = [e["type"] for e in events]
		moves = [e for e in events if e["type"] == "pointerMove"]

		self.assertEqual(kind, "touch")
		self.assertIn("pointerDown", types)
		self.assertIn("pointerUp", types)
		self.assertLess(types.index("pointerDown"), types.index("pointerUp"))

		for move in moves:
			self.assertTrue(100 <= move["x"] <= 180, move)
			self.assertTrue(200 <= move["y"] <= 240, move)

	def test_a_swipe_drags_a_touch_upward_and_lets_go(self):
		driver = FakeDriver(script_result=[412, 915])
		me.swipe_scroll(driver, distance=300)

		kind, events = self._pointer_events(driver)
		moves = [e for e in events if e["type"] == "pointerMove"]

		self.assertEqual(kind, "touch")
		self.assertEqual([e["type"] for e in events if e["type"] != "pointerMove" and e["type"] != "pause"], ["pointerDown", "pointerUp"])
		self.assertGreater(moves[0]["y"], moves[-1]["y"])
		self.assertGreater(len(moves), 8)

		for move in moves:
			self.assertTrue(0 <= move["x"] <= 412 and 0 <= move["y"] <= 915, move)


if __name__ == "__main__":
	unittest.main()
