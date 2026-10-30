#!/usr/bin/env python3
"""Cross-platform CLI control tool for rewards-farmer running on Synology NAS.

Usage:
    python scripts/ctl.py status
    python scripts/ctl.py star
    python scripts/ctl.py runs [days]
    python scripts/ctl.py tasks [days]
    python scripts/ctl.py settings
    python scripts/ctl.py inspect [name]
    python scripts/ctl.py logs [name] [lines]
    python scripts/ctl.py pause [account] [reason]
    python scripts/ctl.py resume [account]
"""

import json
import subprocess
import sys


def call(method: str, path: str, body: dict | None = None) -> str:
	body_str = json.dumps(body) if body else ""
	cmd = (
		"T=$(grep '^CONTROL_TOKEN=' /volume1/docker/rewards-farmer/.env | cut -d= -f2); "
		f"curl -s -m 20 -X {method} -H \"Authorization: Bearer $T\" -H 'Content-Type: application/json' "
		+ (f"-d '{body_str}' " if body_str else "")
		+ f"'http://127.0.0.1:8787{path}'"
	)
	res = subprocess.run(["ssh", "synology", cmd], capture_output=True, text=True, check=False)
	if res.returncode != 0:
		return f"Error ({res.returncode}): {res.stderr.strip()}"
	return res.stdout.strip()


def main():
	action = sys.argv[1] if len(sys.argv) > 1 else "status"

	if action in ("status", "stat"):
		print(call("GET", "/status"))
	elif action in ("star", "star_bonus", "bonuses"):
		raw = call("GET", "/star-bonus")
		try:
			data = json.loads(raw)
			import os
			sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
			import star_bonus
			print(star_bonus.format_star_bonus_report(data))
		except Exception:
			print(raw)
	elif action == "runs":
		days = sys.argv[2] if len(sys.argv) > 2 else "1"
		print(call("GET", f"/runs?days={days}"))
	elif action == "tasks":
		days = sys.argv[2] if len(sys.argv) > 2 else "14"
		print(call("GET", f"/tasks?days={days}"))
	elif action == "settings":
		print(call("GET", "/settings"))
	elif action == "inspect":
		name = sys.argv[2] if len(sys.argv) > 2 else ""
		print(call("GET", f"/inspect/{name}" if name else "/inspect"))
	elif action == "logs":
		name = sys.argv[2] if len(sys.argv) > 2 else ""
		lines = sys.argv[3] if len(sys.argv) > 3 else "60"
		print(call("GET", f"/logs/{name}?lines={lines}" if name else "/logs"))
	elif action == "pause":
		acc = sys.argv[2] if len(sys.argv) > 2 and sys.argv[2] != "all" else ""
		why = sys.argv[3] if len(sys.argv) > 3 else "paused by hand"
		print(call("POST", "/pause", {"account": acc, "reason": why}))
	elif action == "resume":
		acc = sys.argv[2] if len(sys.argv) > 2 and sys.argv[2] != "all" else ""
		print(call("POST", "/resume", {"account": acc}))
	else:
		print("Usage: python scripts/ctl.py [status|star|runs|tasks|settings|inspect|logs|pause|resume]")
		sys.exit(2)


if __name__ == "__main__":
	main()
