"""Central logging configuration.

`setup_logging` is called once from `main.py`. Every other module just does
`logger = logging.getLogger(__name__)` at import time, which is safe to do
before this runs, so import order does not matter.
"""

import logging
import os
import re
import sys

LEVEL_ENV_VAR = "REWARDS_FARMER_LOG_LEVEL"
FILE_ENV_VAR = "REWARDS_FARMER_LOG_FILE"

DEFAULT_LEVEL = "INFO"

# Configuring the root logger switches on output for every library that logs,
# not just ours. httpx emits an info line per ollama call, which buries the
# task summary and puts the ollama endpoint in the log file. print never did
# this because it never touched logging at all, so leaving these at their
# default would make the output noisier than what it replaces.
NOISY_LIBRARIES = ("httpx", "httpcore", "urllib3", "selenium")

# Longest a one-line exception summary may get before it is cut. Long enough
# for any real selenium message, short enough that a pathological one cannot
# push a whole screen of text into a single record.
MAX_SUMMARY_LENGTH = 300

_SESSION_INFO = re.compile(r"\s*\(Session info:[^)]*\)")


def exception_summary(exc: BaseException) -> str:
	"""One short line describing an exception, safe to put in a log record.

	str() on a selenium exception is multi-line: the message, then a session
	info line, then the whole msedgedriver stacktrace. Only the first line is
	worth showing in a per-task summary, and the full detail is still attached
	as a traceback when the level is debug.
	"""
	text = str(exc).strip()

	if not text:
		return ""

	text = _SESSION_INFO.sub("", text.splitlines()[0]).strip()

	if len(text) > MAX_SUMMARY_LENGTH:
		# ASCII, because this can land on a Windows console whose encoding
		# cannot represent an ellipsis character.
		text = text[:MAX_SUMMARY_LENGTH - 3].rstrip() + "..."

	return text

# CRITICAL is the longest level name at 8 characters, so pad to that and the
# message column stays aligned no matter what is being logged.
LOG_FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"
DATE_FORMAT = "%H:%M:%S"

# A log file outlives the day it was written on, so its lines carry the date.
FILE_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# A log file is cut back to its newest part when it passes this size, so one that
# nobody reads cannot grow forever.
MAX_LOG_BYTES = 5 * 1024 * 1024
KEEP_LOG_BYTES = 1024 * 1024


def trim_log_file(path: str, limit: int = MAX_LOG_BYTES, keep: int = KEEP_LOG_BYTES) -> bool:
	"""Cut a log file back to its last `keep` bytes (from a whole line) if it is over `limit`."""
	try:
		size = os.path.getsize(path)

		if size <= limit:
			return False

		with open(path, "rb") as handle:
			handle.seek(size - keep)
			tail = handle.read()

		with open(path, "wb") as handle:
			handle.write(tail[tail.find(b"\n") + 1:])

		return True
	except OSError:
		return False

_configured = False


def _resolve_level(level: str | int | None) -> int:
	"""Turn a level name, a level number or None into a level number.

	An unusable value falls back to the default rather than raising. A typo in
	an environment variable must not be able to take down an unattended run.
	"""
	if level is None:
		level = os.environ.get(LEVEL_ENV_VAR, DEFAULT_LEVEL)

	if isinstance(level, int):
		return level

	resolved = logging.getLevelNamesMapping().get(str(level).strip().upper())

	if resolved is None:
		logging.getLogger(__name__).warning(
			"Unknown log level %r, falling back to %s", level, DEFAULT_LEVEL
		)

		return logging.getLevelNamesMapping()[DEFAULT_LEVEL]

	return resolved


def setup_logging(level: str | int | None = None, log_file: str | None = None) -> None:
	"""Configure the root logger. Calling this more than once is a no-op.

	`level` defaults to $REWARDS_FARMER_LOG_LEVEL, then to INFO.
	`log_file` defaults to $REWARDS_FARMER_LOG_FILE, and no file is written
	when neither is set.
	"""
	global _configured

	if _configured:
		return

	root = logging.getLogger()
	root.setLevel(_resolve_level(level))

	formatter = logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT)

	# Card descriptions are scraped from the page and are not ASCII outside the
	# en-US market, which the Windows console encoding cannot represent. Replace
	# those characters instead of letting the write raise.
	if hasattr(sys.stdout, "reconfigure"):
		sys.stdout.reconfigure(errors="replace")

	# stdout rather than the StreamHandler default of stderr, because this
	# replaces print and anyone already redirecting stdout to a file should
	# keep getting the same output there.
	console = logging.StreamHandler(sys.stdout)
	console.setFormatter(formatter)
	root.addHandler(console)

	if log_file is None:
		log_file = os.environ.get(FILE_ENV_VAR)

	if log_file:
		# utf-8 explicitly. Card descriptions are scraped from the page and are
		# not ASCII outside the en-US market, and the Windows default encoding
		# would raise on them.
		try:
			os.makedirs(os.path.dirname(os.path.abspath(log_file)), exist_ok=True)
			trim_log_file(log_file)
			file_handler = logging.FileHandler(log_file, encoding="utf-8")
			file_handler.setFormatter(logging.Formatter(LOG_FORMAT, datefmt=FILE_DATE_FORMAT))
			root.addHandler(file_handler)
		except OSError as exc:
			# A log file that cannot be opened must not stop a run.
			logging.getLogger(__name__).warning("Could not open the log file %s: %s", log_file, exc)

	for name in NOISY_LIBRARIES:
		logging.getLogger(name).setLevel(logging.WARNING)

	_configured = True
