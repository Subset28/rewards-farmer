"""Does Microsoft challenge a browser on this address? Checked on a throwaway profile, never an account's.

Starts Edge with a temporary profile (through browser.start_driver, the path a real run
uses), loads a Bing search, the Rewards page and the Microsoft sign-in page, and reports
what each came back as and whether it looks like a human check. Run it inside an
account's VPN namespace to see how its address is treated before the account uses it:

    nsenter --net=<namespace> with-xvfb python src/vpn_browser_check.py

It signs in to nothing and clicks nothing on any page. Run it once per server, not on a loop.
"""

import json
import random
import sys
import tempfile
import time

import accounts
import browser

PAGES = (
	("bing search", "https://www.bing.com/search?q=weather+tomorrow"),
	("rewards", "https://rewards.bing.com/"),
	("sign-in", "https://login.live.com/"),
)

# What a human check or a block reads like. A page that says none of these is not
# proven clean, only not obviously challenged.
CHALLENGE_MARKERS = (
	"captcha", "unusual traffic", "verify you are a human", "verify you're a human", "are you a robot",
	"prove you're human", "prove you are human", "suspicious activity", "press & hold", "press and hold",
	"hcaptcha", "arkose", "complete the challenge", "automated queries", "access denied", "temporarily blocked",
)


def challenge_markers(text: str, url: str = "") -> list[str]:
	"""The signs of a human check or block found in a page's text or address."""
	lowered = f"{url}\n{text}".lower()

	return [marker for marker in CHALLENGE_MARKERS if marker in lowered]


def check_page(driver, label: str, url: str) -> dict:
	driver.get(url)
	time.sleep(random.uniform(5, 8))
	text = driver.find_element("tag name", "body").text
	found = challenge_markers(text, driver.current_url)

	return {
		"page": label,
		"landed_on": driver.current_url[:90],
		"title": driver.title[:70],
		"characters": len(text),
		"challenge_markers": found,
		"verdict": "CHALLENGED" if found else "no challenge seen",
		"first_lines": [line.strip()[:80] for line in text.splitlines() if line.strip()][:4],
	}


def run() -> list[dict]:
	with tempfile.TemporaryDirectory(prefix="vpncheck-") as profile:
		account = accounts.Account(name="probe-vpn", user_data_dir=profile, profile_name="Default")
		driver = browser.start_driver(account)

		if driver is None:
			return [{"error": "the browser did not start"}]

		try:
			results = []

			for label, url in PAGES:
				results.append(check_page(driver, label, url))
				time.sleep(random.uniform(3, 6))

			return results
		finally:
			driver.quit()


if __name__ == "__main__":
	results = run()
	print(json.dumps(results, indent=1))
	challenged = [r for r in results if r.get("challenge_markers") or r.get("error")]
	print(f"\n{len(results) - len(challenged)} of {len(results)} pages came back without a challenge")
	sys.exit(1 if challenged else 0)
