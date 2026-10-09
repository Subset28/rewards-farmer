"""A small control interface for the running app: look at it and stop or restart it without scripts.

    GET  /status             every account: gate, pause, last points, today's runs, health findings; browsers running
    GET  /runs?days=2        the journal of runs
    GET  /tasks?days=14      per account and task: how often it worked, its last outcomes, whether it is failing now
    GET  /settings           what is switched on and how it is set (no secrets: only whether one is present)
    GET  /inspect            the saved Rewards pages (inspect_rewards.py): the list
    GET  /inspect/<name>     one of them, as lines (emails and long numbers taken out)
    GET  /logs               which log files there are
    GET  /logs/<name>?lines=80   the end of one of them (names from the list above, nothing else)
    POST /pause              {"reason": "...", "account": "second"}  stop runs (the account, or all when none is named)
    POST /resume             {"account": "second"}                   start them again (all pauses when none is named)

Every request needs `Authorization: Bearer <CONTROL_TOKEN>`. With no CONTROL_TOKEN set it does not start, so it can
never be open by accident. It is published on the NAS's own loopback only (docker-compose.yml), so it is reached from
a computer through an SSH tunnel, never from the network.

It cannot deploy, start a browser, change a setting, read an account's session or run a command. The most it can do is
write or remove the pause that the schedulers already honour. The Docker socket is not mounted and not needed.

    python src/control_api.py        serves on port 8787 (CONTROL_PORT)
"""

import hmac
import json
import logging
import os
import re
import sys
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import clock
import features
import gate
import health
import journal
import pacing
import points_log
import safety
import status
import task_log
from constants import USER_DATA_DIR

logger = logging.getLogger(__name__)

PORT = int(os.environ.get("CONTROL_PORT", "8787"))
LOG_DIR = os.path.join(USER_DATA_DIR, "logs")
MAX_BODY = 4096
LOG_NAME = re.compile(r"^[A-Za-z0-9_.-]+\.log$")
MAX_LINES = 500
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+")
LONG_NUMBER = re.compile(r"\d[\d\s().-]{7,}\d")
INSPECT_DIR =os.path.join(USER_DATA_DIR, "inspect")
INSPECT_NAME = re.compile(r"^[A-Za-z0-9_.-]+\.txt$")


def token() -> str:
	return os.environ.get("CONTROL_TOKEN", "").strip()


def authorised(header: str | None, expected: str | None = None) -> bool:
	"""Whether `Authorization: Bearer ...` carries the token. Compared in constant time; no token set means no one is."""
	expected = token() if expected is None else expected

	if not expected or not header or not header.startswith("Bearer "):
		return False

	return hmac.compare_digest(header[len("Bearer "):].strip().encode(), expected.encode())


def browsers_running() -> int:
	"""Browsers working an account profile in this container (a run is under way when this is not 0)."""
	count = 0

	try:
		for entry in os.listdir("/proc"):
			if not entry.isdigit():
				continue

			try:
				with open(f"/proc/{entry}/cmdline", "rb") as handle:
					line = handle.read().replace(b"\0", b" ").decode("utf-8", "replace")
			except OSError:
				continue

			if "msedge" in line and "--user-data-dir" in line:
				count += 1
	except OSError:
		return 0

	return count


def account_names() -> list[str]:
	try:
		return status.known_names()
	except ValueError:
		return []


def last_points(name: str) -> dict | None:
	rows = points_log.history(name)

	return rows[-1] if rows else None


def status_view() -> dict:
	names = account_names()
	brake = safety.blocked(names)
	out = {"brake": brake, "browsers_running": browsers_running(), "accounts": {}, "today_runs": journal.events(days=1)}

	for name in names:
		out["accounts"][name] = {
			"gate": gate.reason(name) or "runs",
			"paused": safety.paused_for(name),
			"points": last_points(name),
		}

	try:
		out["health"] = [{"account": f.account, "summary": f.summary, "needs": f.needs} for f in health.findings(names)]
	except Exception as exc:  # a view of the state must still answer when one part of it cannot be read
		out["health"] = f"unavailable: {type(exc).__name__}"

	return out


SECRET_PRESENCE = (
	"CONTROL_TOKEN", "NOTIFY_URL", "OPENROUTER_API_KEY",
	"BACKUP_REPO", "BACKUP_GITHUB_TOKEN", "TRAWL_URL", "OLLAMA_HOST",
)
PLAIN_SETTINGS = re.compile(r"^(REWARDS_[A-Z_]+|QUERY_SOURCE|OPENROUTER_DAILY_LIMIT|TZ)$")


def tasks_view(days: int) -> dict:
	"""Per account and task: runs, how many completed, the last outcomes, and whether it has failed every time lately."""
	cutoff = (clock.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
	out: dict = {}

	for row in task_log.history():
		if row.get("t", "") < cutoff:
			continue

		task = out.setdefault(row.get("account", "?"), {}).setdefault(row["task"], {"runs": 0, "completed": 0, "last": []})
		task["runs"] += 1
		task["completed"] += 1 if row.get("completed") else 0
		task["last"] = (task["last"] + [row.get("tag")])[-6:]
		task["last_run"] = row["t"]

	for tasks in out.values():
		for task in tasks.values():
			task["failing_now"] = len(task["last"]) >= 3 and "OK" not in task["last"][-3:]

	return out


def settings_view() -> dict:
	names = account_names()

	return {
		"environment": {key: value for key, value in sorted(os.environ.items()) if PLAIN_SETTINGS.match(key)},
		"present": {key: bool(os.environ.get(key)) for key in SECRET_PRESENCE},
		"features": {feature: [n for n in names if features.enabled(feature, n)] for feature in features.KNOWN},
		"pacing": {name: pacing.describe(name) for name in names},
		"gate": {name: gate.reason(name) or "runs" for name in names},
		"data_dir": USER_DATA_DIR,
	}


def log_files() -> list[str]:
	try:
		return sorted(name for name in os.listdir(LOG_DIR) if LOG_NAME.match(name))
	except OSError:
		return []


def inspect_files() -> list[str]:
	try:
		return sorted(name for name in os.listdir(INSPECT_DIR) if INSPECT_NAME.match(name))
	except OSError:
		return []


def inspect_text(name: str) -> list[str] | None:
	"""The lines of one saved Rewards page (see inspect_rewards.py), with emails and long numbers taken out."""
	if not INSPECT_NAME.match(name) or name not in inspect_files():
		return None

	try:
		with open(os.path.join(INSPECT_DIR, name), encoding="utf-8", errors="replace") as handle:
			text = handle.read()
	except OSError:
		return None

	return [EMAIL.sub("[email]", LONG_NUMBER.sub("[number]", line))[:200] for line in text.splitlines()][:MAX_LINES * 2]


def tail(name: str, lines: int) -> list[str] | None:
	"""The last `lines` lines of one log, or None when `name` is not one of the log files."""
	if not LOG_NAME.match(name) or name not in log_files():
		return None

	lines = max(1, min(MAX_LINES, lines))

	with open(os.path.join(LOG_DIR, name), "rb") as handle:
		handle.seek(0, os.SEEK_END)
		size = handle.tell()
		handle.seek(max(0, size - 256 * 1024))
		data = handle.read().decode("utf-8", "replace")

	return data.splitlines()[-lines:]


def _number(query: dict, key: str, default: int) -> int:
	try:
		return int(query.get(key, [default])[0])
	except (TypeError, ValueError):
		return default


def _account(body: dict) -> str | None:
	value = body.get("account")

	if value in (None, ""):
		return None

	if not isinstance(value, str) or value not in account_names():
		raise ValueError(f"unknown account {value!r}")

	return value


def handle(method: str, path: str, body: dict | None = None) -> tuple[int, object]:
	"""(HTTP status, JSON-able answer) for one authorised request."""
	url = urlparse(path)
	query = parse_qs(url.query)
	route = url.path.rstrip("/") or "/"
	body = body or {}

	if method == "GET":
		if route == "/status":
			return 200, status_view()

		if route == "/runs":
			return 200, journal.events(days=max(1, min(14, _number(query, "days", 1))))

		if route == "/tasks":
			return 200, tasks_view(max(1, min(60, _number(query, "days", 14))))

		if route == "/settings":
			return 200, settings_view()

		if route == "/inspect":
			return 200, inspect_files()

		if route.startswith("/inspect/"):
			lines = inspect_text(route[len("/inspect/"):])

			return (200, lines) if lines is not None else (404, {"error": "no such page"})

		if route == "/logs":
			return 200, log_files()

		if route.startswith("/logs/"):
			lines = tail(route[len("/logs/"):], _number(query, "lines", 80))

			return (200, lines) if lines is not None else (404, {"error": "no such log"})

	if method == "POST":
		try:
			if route == "/pause":
				reason = str(body.get("reason") or "paused through the control interface")[:200]

				return 200, {"paused": safety.pause(reason, _account(body))}

			if route == "/resume":
				return 200, {"cleared": safety.clear(_account(body))}
		except ValueError as exc:
			return 400, {"error": str(exc)}

	return 404, {"error": "not found"}


class Handler(BaseHTTPRequestHandler):
	server_version = "rewards-control"

	def log_message(self, fmt, *args):
		logger.debug("control: " + fmt, *args)

	def _send(self, code: int, payload: object) -> None:
		data = json.dumps(payload, default=str, indent=1).encode()
		self.send_response(code)
		self.send_header("Content-Type", "application/json")
		self.send_header("Content-Length", str(len(data)))
		self.end_headers()
		self.wfile.write(data)

	def _serve(self, method: str) -> None:
		if not authorised(self.headers.get("Authorization")):
			self._send(401, {"error": "a bearer token is needed"})

			return

		body = {}

		if method == "POST":
			try:
				length = int(self.headers.get("Content-Length") or 0)
			except ValueError:
				length = 0

			if length > MAX_BODY:
				self._send(413, {"error": "too large"})

				return

			try:
				body = json.loads(self.rfile.read(length) or b"{}")
			except ValueError:
				self._send(400, {"error": "the body is not JSON"})

				return

			if not isinstance(body, dict):
				self._send(400, {"error": "the body must be an object"})

				return

		try:
			code, payload = handle(method, self.path, body)
		except Exception as exc:
			logger.error("control: %s %s failed: %s", method, self.path, type(exc).__name__)
			code, payload = 500, {"error": type(exc).__name__}

		self._send(code, payload)

	def do_GET(self):
		self._serve("GET")

	def do_POST(self):
		self._serve("POST")


def main() -> int:
	logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

	if not token():
		logger.error("CONTROL_TOKEN is not set, so the control interface is not started.")

		return 1

	if len(token()) < 24:
		logger.error("CONTROL_TOKEN is shorter than 24 characters, so the control interface is not started.")

		return 1

	logger.info("Control interface on port %d (published on the NAS's loopback only).", PORT)
	ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()

	return 0


if __name__ == "__main__":
	sys.exit(main())
