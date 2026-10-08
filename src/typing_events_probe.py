"""What a web page sees when the bot types as a recorded person: every key event, with its timing.

    with-xvfb python src/typing_events_probe.py

Starts Edge on a throwaway profile (never an account's), opens a page that records each keydown, keyup
and input event the way a site's own script could, types a few searches with a made-up recorded person
through the real KeyboardUtils, and reports what the page saw: whether the events are trusted, how long
each key was held, how often one key went down before the last came up, the gaps between presses, that no
key arrived as a held-down repeat, and that the text in the box is what was meant. This is the check that
the numbers in the unit tests survive contact with a real browser.
"""

import json
import math
import os
import random
import statistics
import sys
import tempfile
import time

import accounts
import behavior
import browser
import mimic_typing
from selenium.webdriver.common.by import By

PAGE = """<!doctype html><meta charset=utf-8><title>keys</title>
<input id=box autofocus style="font-size:28px;width:90%">
<script>
window.__log = [];
const box = document.getElementById('box');
for (const type of ['keydown', 'keyup', 'input']) {
  box.addEventListener(type, e => window.__log.push({
    type, key: e.key, code: e.code, repeat: !!e.repeat, trusted: e.isTrusted, t: performance.now(), value: box.value,
  }));
}
box.addEventListener('keydown', e => { if (e.key === 'Enter') e.preventDefault(); });
</script>"""

SEARCHES = (
	"best pizza near me tonight", "weather forecast for the next ten days", "how to fix a leaky kitchen faucet",
	"who won the basketball game last night", "cheap flights to lisbon in march", "what time does the pharmacy close",
)

def load_person() -> dict:
	"""The made-up person below, or the recorded typing of a real profile when PROBE_PROFILE names its json."""
	path = os.environ.get("PROBE_PROFILE")

	if path:
		with open(path, encoding="utf-8") as handle:
			return behavior.clean_detail(json.load(handle)["typing"]["detail"], behavior.TYPING_DETAIL_RANGES, "noticed_weights")

	return PERSON


PERSON = {
	"log_gap_mu": math.log(0.15), "within_sigma": 0.34, "sigma_sd": 0.05, "tempo_sd": 0.12, "tempo_phi": 0.3, "gap_phi": 0.3,
	"offset_alternate": -0.1, "offset_same_hand": 0.05, "offset_same_finger": 0.3, "offset_other": 0.1, "offset_common_pair": -0.2,
	"day_sd": 0.06, "hold_mu": math.log(88), "hold_sigma": 0.25, "rollover_rate": 0.30,
	"hesitation_rate": 0.0, "start_mu": math.log(0.8), "start_sigma": 0.3,
	"backspace_gap_ms": 120.0, "notice_pause_ms": 500.0, "correction_rate": 1.0, "noticed_weights": [1, 1, 1, 0],
}


def summarise(log: list[dict]) -> dict:
	downs = [e for e in log if e["type"] == "keydown"]
	ups = [e for e in log if e["type"] == "keyup"]
	holds, overlaps = [], 0
	open_keys: dict[str, float] = {}

	for event in sorted((e for e in log if e["type"] in ("keydown", "keyup")), key=lambda e: e["t"]):
		if event["type"] == "keydown":
			if open_keys:
				overlaps += 1

			open_keys[event["code"] + event["key"]] = event["t"]
		else:
			started = open_keys.pop(event["code"] + event["key"], None)

			if started is not None:
				holds.append(event["t"] - started)

	gaps = [(b["t"] - a["t"]) / 1000 for a, b in zip(downs, downs[1:])]

	return {
		"events": len(log),
		"all_trusted": all(e["trusted"] for e in log),
		"repeats": sum(1 for e in log if e["repeat"]),
		"keydowns": len(downs),
		"keyups": len(ups),
		"hold_ms_median": round(statistics.median(holds), 1) if holds else None,
		"hold_ms_range": [round(min(holds), 1), round(max(holds), 1)] if holds else None,
		"overlap_share": round(overlaps / max(1, len(downs) - 1), 3),
		"gap_ms_median": round(statistics.median(gaps) * 1000, 1) if gaps else None,
		"gap_ms_range": [round(min(gaps) * 1000), round(max(gaps) * 1000)] if gaps else None,
	}


def rows_from(log: list[dict]) -> list | None:
	"""One search's feature row from what the page saw, in the same terms as a recording's."""
	import indistinguishable

	downs = [e for e in log if e["type"] == "keydown" and len(e["key"]) == 1]
	gaps = [(b["t"] - a["t"]) / 1000 for a, b in zip(downs, downs[1:])]
	open_keys, holds, overlaps, pairs = {}, [], 0, 0

	for event in sorted((e for e in log if e["type"] in ("keydown", "keyup")), key=lambda e: e["t"]):
		identity = event["code"] + event["key"]

		if event["type"] == "keydown":
			pairs += 1
			overlaps += bool(open_keys)
			open_keys[identity] = event["t"]
		else:
			started = open_keys.pop(identity, None)

			if started is not None:
				holds.append(event["t"] - started)

	return indistinguishable.phrase_row(gaps, holds, overlaps / max(1, pairs - 1))


def run_long(count: int) -> dict:
	"""Type `count` searches the way the real KeyboardUtils does (slips included) and report what the page saw."""
	import calibration
	import search_behavior

	os.environ["REWARDS_FEATURES"] = "typing"
	person = load_person()

	with tempfile.TemporaryDirectory(prefix="typing-probe-") as profile:
		account = accounts.Account(name="probe-typing", user_data_dir=profile, profile_name="Default")
		driver = browser.start_driver(account)

		if driver is None:
			return {"error": "driver did not start"}

		try:
			page = os.path.join(profile, "keys.html")

			with open(page, "w", encoding="utf-8") as handle:
				handle.write(PAGE)

			driver.get(("file:///" if os.name == "nt" else "file://") + page.replace(os.sep, "/"))
			profile_object = behavior.Behavior("probe-typing", "recorded", 0.4, 0.4, 0.2, 0.13, typing_detail=person)
			keyboard = mimic_typing.KeyboardUtils(driver, profile_object, account="probe-typing")
			rng = random.Random(3)
			texts = list(calibration.PHRASES) + list(SEARCHES)
			rows, wrong, slipped_count, backspaces = [], 0, 0, 0

			for n in range(count):
				text = texts[n % len(texts)]
				driver.find_element(By.ID, "box").click()
				driver.execute_script("window.__log.length = 0; document.getElementById('box').value = '';")
				rate, neighbor = keyboard.slip_settings(text)
				typed = search_behavior.with_typo(text, rate, rng, neighbor, keyboard.slip_weights(text))
				slipped_count += typed != text
				keyboard.send_keys(typed + mimic_typing.Keys.ENTER, intended=text, rng=rng)
				log = driver.execute_script("return window.__log")
				wrong += driver.execute_script("return document.getElementById('box').value") != text
				backspaces += sum(1 for e in log if e["type"] == "keydown" and e["key"] == "Backspace")
				row = rows_from(log)

				if row:
					rows.append(row)

			return {"searches": count, "slipped": slipped_count, "ended_wrong": wrong, "backspaces": backspaces, "rows": rows}
		finally:
			driver.quit()


def run() -> dict:
	os.environ["REWARDS_FEATURES"] = "typing"

	with tempfile.TemporaryDirectory(prefix="typing-probe-") as profile:
		account = accounts.Account(name="probe-typing", user_data_dir=profile, profile_name="Default")
		driver = browser.start_driver(account)

		if driver is None:
			return {"error": "driver did not start"}

		try:
			page = os.path.join(profile, "keys.html")

			with open(page, "w", encoding="utf-8") as handle:
				handle.write(PAGE)

			driver.get("file://" + page.replace(os.sep, "/") if os.name != "nt" else "file:///" + page.replace(os.sep, "/"))
			profile_object = behavior.Behavior("probe-typing", "recorded", 0.4, 0.4, 0.2, 0.13, typing_detail=PERSON)
			keyboard = mimic_typing.KeyboardUtils(driver, profile_object, account="probe-typing")
			results = []

			for n, text in enumerate(SEARCHES):
				box = driver.find_element(By.ID, "box")
				box.click()
				driver.execute_script("window.__log.length = 0; document.getElementById('box').value = '';")
				slipped = text[:6] + ("x" if text[6] != "x" else "y") + text[7:] if n % 2 else text
				started = time.time()
				keyboard.send_keys(slipped + mimic_typing.Keys.ENTER, intended=text, rng=random.Random(n))
				took = time.time() - started
				log = driver.execute_script("return window.__log")
				final = driver.execute_script("return document.getElementById('box').value")
				results.append({"text": text, "slipped": slipped != text, "final_value": final, "correct": final == text, "seconds": round(took, 1), **summarise(log)})

			return {"searches": results}
		finally:
			driver.quit()


if __name__ == "__main__":
	if len(sys.argv) > 1 and sys.argv[1] == "long":
		print("ROWS " + json.dumps(run_long(int(sys.argv[2]) if len(sys.argv) > 2 else 40)))
		sys.exit(0)

	print(json.dumps(run(), indent=1, default=str))
	sys.exit(0)
