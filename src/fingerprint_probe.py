"""Compare browser identity variants on a throwaway profile, never an account's.

Starts Edge through browser.start_driver (the path a real run uses), applies one
identity variant, and records what the page sees (navigator.*, Client Hints), what
a server sees (the request headers), and what two public bot checks say.

    with-xvfb python src/fingerprint_probe.py [variant ...]

Variants: baseline (no override), header-hack (what rewards_tasks did before),
linux (honest user agent, Client Hints made to agree), windows (Windows claimed
everywhere). Needs network access to the check sites; uses a temp profile.
"""

import json
import re
import sys
import tempfile
import time

import accounts
import browser

PAGE_JS = """
const d = navigator.userAgentData;
const hi = d ? await d.getHighEntropyValues(
	['platform','platformVersion','architecture','bitness','fullVersionList','model']) : null;
const gl = document.createElement('canvas').getContext('webgl');
const dbg = gl && gl.getExtension('WEBGL_debug_renderer_info');
return JSON.stringify({
	ua: navigator.userAgent, platform: navigator.platform, vendor: navigator.vendor,
	languages: navigator.languages, webdriver: navigator.webdriver,
	plugins: navigator.plugins.length, cores: navigator.hardwareConcurrency,
	brands: d ? d.brands : null, uadPlatform: d ? d.platform : null, high: hi,
	glVendor: dbg ? gl.getParameter(dbg.UNMASKED_VENDOR_WEBGL) : null,
	glRenderer: dbg ? gl.getParameter(dbg.UNMASKED_RENDERER_WEBGL) : null,
	tz: Intl.DateTimeFormat().resolvedOptions().timeZone,
	chromeObj: typeof window.chrome, outer: [outerWidth, outerHeight],
});
"""


def real_ua(driver) -> str:
	return driver.execute_script("return navigator.userAgent")


def apply(driver, variant: str) -> None:
	full = driver.capabilities["browserVersion"]
	major = full.split(".")[0]

	if variant == "baseline":
		return

	if variant == "header-hack":
		driver.execute_cdp_cmd("Network.enable", {})
		driver.execute_cdp_cmd("Network.setExtraHTTPHeaders", {"headers": {
			"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
				"Chrome/151.0.0.0 Safari/537.36 Edg/151.0.0.0 MSRewards/Desktop/1.1.0",
			"X-Rewards-Source": "msrewards-desktop",
		}})
		return

	brands = [
		{"brand": "Chromium", "version": major},
		{"brand": "Microsoft Edge", "version": major},
		{"brand": "Not.A/Brand", "version": "99"},
	]
	full_list = [{"brand": b["brand"], "version": full if b["brand"] != "Not.A/Brand" else "99.0.0.0"} for b in brands]

	if variant == "linux":
		ua = f"Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{major}.0.0.0 Safari/537.36 Edg/{major}.0.0.0"
		meta_platform, meta_version, nav_platform = "Linux", "6.1.0", "Linux x86_64"
	elif variant == "windows":
		ua = f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{major}.0.0.0 Safari/537.36 Edg/{major}.0.0.0"
		meta_platform, meta_version, nav_platform = "Windows", "10.0.0", "Win32"
	else:
		raise SystemExit(f"unknown variant {variant!r}")

	driver.execute_cdp_cmd("Emulation.setUserAgentOverride", {
		"userAgent": ua,
		"platform": nav_platform,
		"userAgentMetadata": {
			"brands": brands, "fullVersionList": full_list, "fullVersion": full,
			"platform": meta_platform, "platformVersion": meta_version,
			"architecture": "x86", "bitness": "64", "model": "", "mobile": False, "wow64": False,
		},
	})


def server_view(driver) -> dict:
	driver.get("https://httpbin.org/headers")
	text = driver.find_element("tag name", "body").text

	try:
		return json.loads(text).get("headers", {})
	except ValueError:
		return {"error": text[:200]}


def sannysoft(driver) -> list[str]:
	driver.get("https://bot.sannysoft.com")
	time.sleep(6)

	return driver.execute_script(
		"return [...document.querySelectorAll('td.failed')].map(td => "
		"(td.parentElement.innerText || '').replace(/\\s+/g, ' ').trim())"
	)


def browserscan(driver) -> str:
	driver.get("https://www.browserscan.net/bot-detection")
	time.sleep(12)
	text = driver.find_element("tag name", "body").text
	match = re.search(r"Test Results:?\s*(\w+)", text)
	flagged = [line for line in text.splitlines() if re.search(r"\b(Robot|Abnormal|Yes)\b", line)]

	return f"verdict={match.group(1) if match else '?'} flagged={flagged[:8]}"


def run(variant: str) -> dict:
	with tempfile.TemporaryDirectory(prefix="probe-") as profile:
		account = accounts.Account(name=f"probe-{variant}", user_data_dir=profile, profile_name="Default")
		driver = browser.start_driver(account)

		if driver is None:
			return {"variant": variant, "error": "driver did not start"}

		try:
			apply(driver, variant)
			driver.get("about:blank")
			result = {"variant": variant, "server": server_view(driver)}
			result["page"] = json.loads(driver.execute_async_script(
				"const done = arguments[arguments.length - 1];"
				"(async () => {" + PAGE_JS + "})().then(done);"
			))
			result["sannysoft_failed"] = sannysoft(driver)
			result["browserscan"] = browserscan(driver)

			return result
		finally:
			driver.quit()


if __name__ == "__main__":
	for name in sys.argv[1:] or ["baseline", "header-hack", "linux", "windows"]:
		print(json.dumps(run(name), indent=1, default=str), flush=True)
