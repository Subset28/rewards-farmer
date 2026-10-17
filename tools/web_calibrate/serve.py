"""Serve the in-browser typing and mouse test on the home network, and keep what comes back.

    python tools/web_calibrate/serve.py --account third [--port 8080]

It prints the address to give a family member, such as http://192.168.1.20:8080/?for=third, and saves each finished
test to data-dir/behavior/inbox/<account>-<time>.json. Then, on this computer:

    python src/calibrate.py third --import-web data-dir/behavior/inbox/third-<time>.json

adds it to that account's recording and rebuilds the profile. Stop the server with Ctrl+C when the test is done.

Plain standard library, no packages. It listens on the local network only: do not forward a port to it from the router.
Only the test page and one upload address exist; the upload is capped in size and the account name is checked.
"""

import argparse
import json
import os
import re
import socket
import sys
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
INBOX = os.path.join(os.path.dirname(os.path.dirname(HERE)), "data-dir", "behavior", "inbox")
MAX_BYTES = 8 * 1024 * 1024
ACCOUNT = re.compile(r"^[a-z][a-z0-9_-]{0,30}$")


class Handler(BaseHTTPRequestHandler):
	def _reply(self, code: int, body: bytes = b"", content_type: str = "text/plain; charset=utf-8") -> None:
		self.send_response(code)
		self.send_header("Content-Type", content_type)
		self.send_header("Content-Length", str(len(body)))
		self.send_header("Cache-Control", "no-store")
		self.end_headers()
		self.wfile.write(body)

	def do_GET(self) -> None:
		if self.path.split("?")[0] not in ("/", "/index.html"):
			self._reply(404)

			return

		with open(os.path.join(HERE, "index.html"), "rb") as handle:
			self._reply(200, handle.read(), "text/html; charset=utf-8")

	def do_POST(self) -> None:
		path, _, query = self.path.partition("?")

		if path != "/submit":
			self._reply(404)

			return

		account = ""

		for part in query.split("&"):
			key, _, value = part.partition("=")

			if key == "for":
				account = value.strip().lower()

		if account and not ACCOUNT.match(account):
			self._reply(400, b"bad account")

			return

		try:
			length = int(self.headers.get("Content-Length", "0"))
		except ValueError:
			length = 0

		if not 0 < length <= MAX_BYTES:
			self._reply(413, b"too large or empty")

			return

		try:
			data = json.loads(self.rfile.read(length))
			sittings = data["sessions"]
			ok = isinstance(sittings, list) and sittings and all(s.get("typing") and s.get("mouse") for s in sittings)
		except (ValueError, KeyError, AttributeError, TypeError):
			ok = False

		if not ok:
			self._reply(400, b"not a complete test")

			return

		os.makedirs(INBOX, exist_ok=True)
		name = f"{account or 'unnamed'}-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"

		with open(os.path.join(INBOX, name), "w", encoding="utf-8") as handle:
			json.dump(data, handle)

		print(f"Received {name}", flush=True)
		self._reply(200, b"ok")

	def log_message(self, fmt: str, *args) -> None:
		pass


def lan_address() -> str:
	"""This computer's address on the home network (no traffic is sent)."""
	probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

	try:
		probe.connect(("10.255.255.255", 1))

		return probe.getsockname()[0]
	except OSError:
		return "127.0.0.1"
	finally:
		probe.close()


def main(argv: list[str]) -> int:
	parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
	parser.add_argument("--port", type=int, default=8080)
	parser.add_argument("--account", required=True, help="the account being recorded (default, second, third, fourth); it names the saved files")
	args = parser.parse_args(argv[1:])

	if not ACCOUNT.match(args.account):
		parser.error("--account must be a plain account name such as third")

	server = ThreadingHTTPServer(("0.0.0.0", args.port), Handler)
	print(f"Give this address to the person: http://{lan_address()}:{args.port}/?for={args.account}")
	print("Results are saved to", INBOX)
	print("Ctrl+C to stop.", flush=True)

	try:
		server.serve_forever()
	except KeyboardInterrupt:
		pass

	return 0


if __name__ == "__main__":
	sys.exit(main(sys.argv))
