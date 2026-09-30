"""Present the browser as a phone.

Meant for searches Microsoft Rewards counts as mobile. Not wired into any run:
apply() is opt-in, and whether it earns anything on a given account is a
question for the points breakdown, not for this module.

The identity is Edge on Android, not Safari on iOS. The engine underneath is
Chromium, and a Chromium engine claiming to be iOS Safari contradicts itself in
ways a fingerprinting script reads directly: iOS Safari has no window.chrome and
no client hints, while this browser has both. Edge on Android is what Chromium
really is on a phone, so the user agent, the client hints, the platform and the
touch surface can all agree with each other.

Each part is built to agree with the others, because a half-consistent identity
is easier to spot than either a plain desktop or a complete phone. So apply()
either finishes or raises: it never leaves a session that is a phone in some
respects and a desktop in the rest.

Known limits, measured on a throwaway profile (src/check_mobile_fingerprint.py):

- navigator.platform reads "Linux x86_64" on every page, though the user agent
  says Android: the CDP platform override does not survive a navigation.
- navigator.deviceMemory reports the host's real RAM (16 here), which no phone
  does; the spec caps it at 8, and CDP has no override for it.
- WebGL reports SwiftShader, as on any host with no GPU.
- SannySoft's CHR_MEMORY check fails in this mode and not in the desktop one,
  for a reason not found.

The first two could be papered over with a preload script that redefines the
properties, and that was tried: it turned BrowserScan from Normal to Robot and
changed CreepJS's Navigator fingerprint. A page-level patch is itself a tell, so
the mismatch is left as it is. Re-measure before changing that.
"""

import random
import zlib
from dataclasses import dataclass

from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.actions import interaction
from selenium.webdriver.common.actions.action_builder import ActionBuilder
from selenium.webdriver.common.actions.pointer_input import PointerInput


class MobileEmulationError(RuntimeError):
	"""The browser could not be made to present as a phone."""


@dataclass(frozen=True)
class Device:
	model: str
	android_version: str
	width: int
	height: int
	scale: float

	@property
	def platform_version(self) -> str:
		return f"{self.android_version}.0.0"


# Real phones with real viewport metrics, as reported to web pages (CSS pixels).
DEVICES = (
	Device("Pixel 7", "13", 412, 915, 2.625),
	Device("Pixel 6", "13", 412, 915, 2.625),
	Device("SM-S911B", "14", 360, 780, 3.0),
)

# What navigator.platform reports on an ARM Android phone.
PLATFORM = "Linux armv8l"
# No q-values: the browser adds them, and giving it "en;q=0.9" sent
# "en;q=0.9;q=0.9", a malformed header a server can see.
ACCEPT_LANGUAGE = "en-US,en"
MAX_TOUCH_POINTS = 5


def device_for(account_name: str) -> Device:
	"""The same phone every time for the same account.

	A real account shows up on one handset, not on a different one each run.
	Derived from the name so it survives restarts with nothing stored.
	"""
	return DEVICES[zlib.crc32(account_name.encode("utf-8")) % len(DEVICES)]


def major_version(full_version: str) -> str:
	major = full_version.split(".")[0]

	if not major.isdigit():
		raise MobileEmulationError(f"cannot read a browser version from {full_version!r}")

	return major


def user_agent(device: Device, full_version: str) -> str:
	major = major_version(full_version)

	# The Chrome part is reduced to major.0.0.0, the way Chromium reports it
	# since user agent reduction, and Edge on Android appends its own token.
	return (
		f"Mozilla/5.0 (Linux; Android {device.android_version}; {device.model}) "
		"AppleWebKit/537.36 (KHTML, like Gecko) "
		f"Chrome/{major}.0.0.0 Mobile Safari/537.36 EdgA/{major}.0.0.0"
	)


def user_agent_metadata(device: Device, full_version: str) -> dict:
	"""The client hints, matching the user agent string in every field they share."""
	major = major_version(full_version)

	return {
		"brands": [
			{"brand": "Chromium", "version": major},
			{"brand": "Microsoft Edge", "version": major},
			{"brand": "Not A(Brand", "version": "99"},
		],
		"fullVersionList": [
			{"brand": "Chromium", "version": full_version},
			{"brand": "Microsoft Edge", "version": full_version},
			{"brand": "Not A(Brand", "version": "99.0.0.0"},
		],
		"platform": "Android",
		"platformVersion": device.platform_version,
		"architecture": "",
		"model": device.model,
		"mobile": True,
		"bitness": "",
		"wow64": False,
	}


def apply(driver, device: Device) -> None:
	"""Make the browser a phone, or raise MobileEmulationError.

	Covers the tab the driver is on. A tab opened later is a fresh target with
	none of this, so a mobile session should stay in one tab.
	"""
	full_version = driver.capabilities.get("browserVersion", "")

	steps = (
		("Emulation.setDeviceMetricsOverride", {
			"width": device.width,
			"height": device.height,
			"deviceScaleFactor": device.scale,
			"mobile": True,
			"screenOrientation": {"type": "portraitPrimary", "angle": 0},
		}),
		("Emulation.setTouchEmulationEnabled", {"enabled": True, "maxTouchPoints": MAX_TOUCH_POINTS}),
		("Emulation.setEmitTouchEventsForMouse", {"enabled": True, "configuration": "mobile"}),
		("Emulation.setUserAgentOverride", {
			"userAgent": user_agent(device, full_version),
			"acceptLanguage": ACCEPT_LANGUAGE,
			"platform": PLATFORM,
			"userAgentMetadata": user_agent_metadata(device, full_version),
		}),
	)

	for command, params in steps:
		try:
			driver.execute_cdp_cmd(command, params)
		except Exception as exc:
			raise MobileEmulationError(f"{command} failed, so this would be a half-emulated phone: {exc}") from exc


def _touch_actions(driver) -> ActionChains:
	actions = ActionChains(driver, duration=0)
	actions.w3c_actions = ActionBuilder(driver, mouse=PointerInput(interaction.POINTER_TOUCH, "touch"), duration=0)

	return actions


def tap(driver, element) -> None:
	"""Tap an element the way a thumb does: near the middle, not exactly, with a brief hold."""
	x, y, width, height = driver.execute_script(
		"var r = arguments[0].getBoundingClientRect(); return [r.left, r.top, r.width, r.height];",
		element,
	)

	# Gaussian around the center, kept well inside the element.
	px = x + width * min(max(random.gauss(0.5, 0.12), 0.15), 0.85)
	py = y + height * min(max(random.gauss(0.5, 0.12), 0.15), 0.85)

	actions = _touch_actions(driver)
	pointer = actions.w3c_actions.pointer_action
	pointer.move_to_location(int(px), int(py))
	pointer.pointer_down()
	pointer.pause(random.uniform(0.05, 0.13))
	pointer.pointer_up()
	actions.perform()


def swipe_scroll(driver, distance: int | None = None) -> None:
	"""Scroll down with a swipe: a finger dragged up the screen, fast at first then slowing."""
	width, height = driver.execute_script("return [window.innerWidth, window.innerHeight];")

	x = random.randint(int(width * 0.3), int(width * 0.7))
	start_y = random.randint(int(height * 0.6), int(height * 0.8))
	travel = distance if distance is not None else random.randint(150, int(height * 0.5))
	end_y = max(20, start_y - travel)

	actions = _touch_actions(driver)
	pointer = actions.w3c_actions.pointer_action
	pointer.move_to_location(x, start_y)
	pointer.pointer_down()

	steps = random.randint(8, 14)

	for i in range(1, steps + 1):
		t = i / steps
		eased = 1 - (1 - t) ** 2
		pointer.move_to_location(x + random.randint(-3, 3), int(start_y + (end_y - start_y) * eased))
		pointer.pause(random.uniform(0.012, 0.03))

	pointer.pointer_up()
	actions.perform()
