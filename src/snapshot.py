"""A record of what a page showed at the moment a task failed or was skipped, so a changed page can be fixed from the record.

When Microsoft changes the Rewards page, a task stops finding its button and the log only says so. To fix it, someone has
to see what the page offered instead. This keeps that: the address (without its query), the title, and the visible
labels of the buttons, links and headings, plus the ids of the page's containers. No page text, no cookies, no values.

What is left out on purpose, because the files sit next to an account's data:

  * anything that looks like an email address, a phone number, or a long number;
  * greetings ("Good morning, Name"), which carry the account's display name;
  * the query part of every address.

A snapshot is a small JSON file in data-dir/snapshots, named account-task-time. The newest few per task are kept, and the
folder is capped, so it cannot grow. They are not part of the off-NAS backup (backup.py lists what it copies), and the
control interface serves them to the holder of its token only.

Nothing here ever raises: a page that cannot be read is simply not recorded.
"""

import json
import logging
import os
import re
from urllib.parse import urlsplit

import clock
from constants import USER_DATA_DIR

logger = logging.getLogger(__name__)

SNAPSHOT_DIR = os.path.join(USER_DATA_DIR, "snapshots")
KEEP_PER_TASK = 4
MAX_FILES = 60
MAX_LABELS = 120
MAX_LABEL_CHARS = 90
NAME = re.compile(r"^[A-Za-z0-9_.-]+\.json$")

EMAIL = re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+")
LONG_NUMBER = re.compile(r"\d[\d\s().-]{7,}\d")
GREETING = re.compile(r"^(good\s+(morning|afternoon|evening)|hi|hello|welcome( back)?)\b", re.I)

COLLECT = """
const text = e => (e.innerText || e.getAttribute('aria-label') || '').replace(/\\s+/g, ' ').trim();
const seen = new Set(), out = {buttons: [], links: [], headings: [], ids: []};
for (const [key, sel] of [['buttons', 'button, [role=button]'], ['links', 'a'], ['headings', 'h1, h2, h3']]) {
  for (const e of document.querySelectorAll(sel)) {
    const t = text(e);
    if (!t || seen.has(key + t)) continue;
    seen.add(key + t);
    out[key].push(key === 'links' ? [t, e.getAttribute('href') || ''] : t);
    if (out[key].length >= 150) break;
  }
}
for (const e of document.querySelectorAll('[id]')) { if (out.ids.length < 120) out.ids.push(e.id); }
return {url: location.href, title: document.title, ...out};
"""


def clean_label(text: str) -> str | None:
	"""The label with anything personal taken out, or None when nothing of it should be kept."""
	text = " ".join(str(text or "").split())

	if not text or GREETING.match(text):
		return None

	text = EMAIL.sub("[email]", text)
	text = LONG_NUMBER.sub("[number]", text)

	return text[:MAX_LABEL_CHARS]


def clean_url(url: str) -> str:
	"""Scheme, host and path only: no query, no fragment, no credentials."""
	try:
		parts = urlsplit(str(url or ""))

		if not parts.hostname:
			return parts.path[:200]  # a relative address on the page: its path, never its query

		return f"{parts.scheme}://{parts.hostname}{parts.path}"[:200]
	except ValueError:
		return ""


def clean(raw: dict) -> dict:
	"""A page's collected labels reduced to what may be kept."""
	def labels(items, link=False):
		out, seen = [], set()

		for item in items or []:
			label = clean_label(item[0] if link else item)

			if label is None or label in seen:
				continue

			seen.add(label)
			out.append([label, clean_url(item[1]).split("//", 1)[-1][:120]] if link else label)

			if len(out) >= MAX_LABELS:
				break

		return out

	return {
		"url": clean_url(raw.get("url")),
		"title": clean_label(raw.get("title")) or "",
		"buttons": labels(raw.get("buttons")),
		"links": labels(raw.get("links"), link=True),
		"headings": labels(raw.get("headings")),
		"ids": [str(i)[:60] for i in (raw.get("ids") or [])[:120] if re.match(r"^[\w:.-]+$", str(i))],
	}


def _slug(text: str) -> str:
	return re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")[:40] or "x"


def capture(driver, account: str, task: str, reason: str = "") -> str | None:
	"""Record the page the driver is on. Returns the file name, or None when nothing could be recorded."""
	try:
		raw = driver.execute_script(COLLECT)
		page = clean(raw if isinstance(raw, dict) else {})
		stamp = clock.now().strftime("%Y%m%d-%H%M%S")
		name = f"{_slug(account)}-{_slug(task)}-{stamp}.json"
		record = {"account": account, "task": task, "time": clock.stamp(), "reason": clean_label(reason) or "", **page}
		os.makedirs(SNAPSHOT_DIR, exist_ok=True)

		with open(os.path.join(SNAPSHOT_DIR, name), "w", encoding="utf-8") as handle:
			json.dump(record, handle, indent=1)

		prune(account, task)

		return name
	except Exception as exc:  # any failure here must not disturb the run it is describing
		logger.debug("No snapshot taken: %s", type(exc).__name__)

		return None


def names() -> list[str]:
	try:
		return sorted((n for n in os.listdir(SNAPSHOT_DIR) if NAME.match(n)), reverse=True)
	except OSError:
		return []


def prune(account: str, task: str) -> None:
	"""Keep the newest few for this task and the newest MAX_FILES overall."""
	try:
		own = [n for n in names() if n.startswith(f"{_slug(account)}-{_slug(task)}-")]

		for name in own[KEEP_PER_TASK:]:
			os.remove(os.path.join(SNAPSHOT_DIR, name))

		for name in names()[MAX_FILES:]:
			os.remove(os.path.join(SNAPSHOT_DIR, name))
	except OSError as exc:
		logger.debug("Could not prune snapshots: %s", exc)


def read(name: str) -> dict | None:
	"""One snapshot by file name, or None when it is not one of the files here."""
	if not NAME.match(name or "") or name not in names():
		return None

	try:
		with open(os.path.join(SNAPSHOT_DIR, name), encoding="utf-8") as handle:
			return json.load(handle)
	except (OSError, ValueError):
		return None
