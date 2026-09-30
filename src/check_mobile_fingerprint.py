"""Dev tool: how a phone session looks from the outside, on a throwaway profile.

Starts a mobile-emulated browser with a temporary profile that is signed in to
nothing, and prints what a page can read (JS surface), what a server is sent
(HTTP headers), and how three public detectors rate it. Never touches an account:
run this before a phone session is used against one.

	REWARDS_HEADLESS=1 REWARDS_VIRTUAL_DISPLAY=1 with-xvfb python src/check_mobile_fingerprint.py
"""

import re
import shutil
import tempfile
import time

from selenium.webdriver.common.by import By

import accounts
import browser

JS_SURFACE = """
const gl = document.createElement('canvas').getContext('webgl');
const ext = gl && gl.getExtension('WEBGL_debug_renderer_info');
const uad = navigator.userAgentData;
return {
  ua: navigator.userAgent,
  platform: navigator.platform,
  uad_mobile: uad ? uad.mobile : null,
  uad_platform: uad ? uad.platform : null,
  uad_brands: uad ? uad.brands.map(b => b.brand + '/' + b.version) : null,
  maxTouchPoints: navigator.maxTouchPoints,
  ontouchstart: 'ontouchstart' in window,
  pointer_coarse: matchMedia('(pointer: coarse)').matches,
  hover_none: matchMedia('(hover: none)').matches,
  screen: [screen.width, screen.height],
  inner: [window.innerWidth, window.innerHeight],
  dpr: window.devicePixelRatio,
  orientation: screen.orientation ? screen.orientation.type : null,
  webdriver: navigator.webdriver,
  webgl: gl && ext ? gl.getParameter(ext.UNMASKED_RENDERER_WEBGL) : null,
  cores: navigator.hardwareConcurrency,
  memory: navigator.deviceMemory,
  perf_memory: performance.memory ? Object.keys(Object.getPrototypeOf(performance.memory)).length : null,
  secure: window.isSecureContext,
};
"""


def section(title):
	print(f"\n== {title}")


def main() -> int:
	profile = tempfile.mkdtemp(prefix="mobile-check-")
	account = accounts.Account(name="mobile-check", user_data_dir=profile, profile_name="Default")

	driver = browser.start_driver(account, mobile=True)

	if driver is None:
		return 1

	try:
		# On a real https page, not the blank one the browser starts on: that one
		# has no viewport tag, so a phone gives it a 980px layout, and it is not
		# a secure context, so userAgentData and deviceMemory are absent.
		driver.get("https://www.bing.com/")
		time.sleep(6)

		section("what a page can read (bing.com)")
		for key, value in driver.execute_script(JS_SURFACE).items():
			print(f"  {key}: {value}")

		section("what a server is sent (httpbin.org/headers)")
		driver.get("https://httpbin.org/headers")
		time.sleep(4)
		body = driver.find_element(By.TAG_NAME, "body").text
		for line in body.splitlines():
			if re.search(r"User-Agent|Sec-Ch-Ua|Accept-Language", line, re.I):
				print(" ", line.strip())

		section("SannySoft")
		driver.get("https://bot.sannysoft.com/")
		time.sleep(12)
		failed = []
		for row in driver.find_elements(By.CSS_SELECTOR, "table tr"):
			cells = row.find_elements(By.TAG_NAME, "td")
			if "failed" in (row.get_attribute("class") or "") or any("failed" in (c.get_attribute("class") or "") for c in cells):
				failed.append(" | ".join(c.text.strip()[:50] for c in cells).replace("\n", " "))
		print(f"  failed: {len(failed)}")
		for line in failed:
			print("   ", line)

		section("BrowserScan bot detection")
		driver.get("https://www.browserscan.net/bot-detection")
		time.sleep(20)
		lines = driver.find_element(By.TAG_NAME, "body").text.splitlines()
		print("  verdicts:", [l.strip() for l in lines if l.strip() in ("Robot", "Normal")][:6])

		section("CreepJS")
		driver.get("https://abrahamjuliot.github.io/creepjs/")
		time.sleep(40)
		lines = driver.find_element(By.TAG_NAME, "body").text.splitlines()
		for line in lines:
			if re.search(r"headless|stealth|lies", line, re.I):
				print("  ", line.strip()[:70])
	finally:
		driver.quit()
		shutil.rmtree(profile, ignore_errors=True)

	return 0


if __name__ == "__main__":
	raise SystemExit(main())
