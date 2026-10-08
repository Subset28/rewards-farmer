"""What a web page sees when the bot moves the pointer: every mousemove, with its timing and position.

    with-xvfb python src/mouse_events_probe.py

Starts Edge on a throwaway profile (never an account's), opens a page that records each mousemove, mousedown
and mouseup the way a site's own script could, moves the pointer between spots with the real MouseUtils, and
reports what the page saw: how many events arrive and how far apart in time, how the speed rises and falls
over a move, how far the path strays from a straight line, and whether the events are trusted.
"""

import json
import math
import os
import statistics
import sys
import tempfile

import accounts
import behavior
import browser
import mouse_trajectory

PAGE = """<!doctype html><meta charset=utf-8><title>mouse</title>
<body style="margin:0;height:100vh;background:#eee">
<script>
window.__log = [];
for (const type of ['mousemove', 'mousedown', 'mouseup']) {
  document.addEventListener(type, e => window.__log.push({type, x: e.clientX, y: e.clientY, t: performance.now(), trusted: e.isTrusted}));
}
</script>"""

MOVES = (((120, 140), (900, 520)), ((900, 520), (260, 700)), ((260, 700), (330, 640)), ((330, 640), (1400, 200)))


def shape(events: list[dict]) -> dict:
	moves = [e for e in events if e["type"] == "mousemove"]

	if len(moves) < 4:
		return {"events": len(moves)}

	times = [m["t"] for m in moves]
	gaps = [b - a for a, b in zip(times, times[1:])]
	points = [(m["x"], m["y"]) for m in moves]
	length = sum(math.dist(a, b) for a, b in zip(points, points[1:]))
	straight = math.dist(points[0], points[-1])
	speeds = [math.dist(a, b) / max(1e-3, (tb - ta) / 1000) for (a, b), ta, tb in zip(zip(points, points[1:]), times, times[1:])]
	peak = max(range(len(speeds)), key=speeds.__getitem__)
	third = max(1, len(speeds) // 3)

	return {
		"events": len(moves),
		"all_trusted": all(m["trusted"] for m in events),
		"duration_ms": round(times[-1] - times[0]),
		"interval_ms_median": round(statistics.median(gaps), 1),
		"interval_ms_range": [round(min(gaps), 1), round(max(gaps), 1)],
		"efficiency": round(straight / length, 3) if length else None,
		"peak_position": round(peak / len(speeds), 2),
		"first_third_speed_over_peak": round(statistics.mean(speeds[:third]) / max(speeds), 2),
		"last_third_speed_over_peak": round(statistics.mean(speeds[-third:]) / max(speeds), 2),
	}


def run() -> dict:
	with tempfile.TemporaryDirectory(prefix="mouse-probe-") as profile:
		account = accounts.Account(name="probe-mouse", user_data_dir=profile, profile_name="Default")
		driver = browser.start_driver(account)

		if driver is None:
			return {"error": "driver did not start"}

		try:
			page = os.path.join(profile, "mouse.html")

			with open(page, "w", encoding="utf-8") as handle:
				handle.write(PAGE)

			driver.get(("file:///" if os.name == "nt" else "file://") + page.replace(os.sep, "/"))
			mouse = mouse_trajectory.MouseUtils(driver, behavior.Behavior("probe-mouse", "recorded", 0.4, 0.4, 0.4, 0.15))
			results = []

			for start, end in MOVES:
				driver.execute_script("window.__log.length = 0;")
				mouse.fallback_init_pos = start
				distance = math.dist(start, end)
				move_time = mouse_trajectory.get_movement_time_from_fitts_law(distance, 80, 0.4, 0.15)
				path = mouse_trajectory.get_final_path_from_real_time(move_time, start, end)
				mouse.move_mouse(move_time, path, False)
				results.append({"from": start, "to": end, "distance": round(distance), "intended_ms": round(move_time * 1000), **shape(driver.execute_script("return window.__log"))})

			return {"moves": results}
		finally:
			driver.quit()


if __name__ == "__main__":
	print(json.dumps(run(), indent=1, default=str))
	sys.exit(0)
