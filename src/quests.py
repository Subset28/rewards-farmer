"""Which quest (punch card) tasks the bot will do, and which it leaves alone.

A quest is a short list of tasks on rewards.bing.com/earn/quest/<id>. Some are
plain links the bot can open the way it opens any card: a Bing search, a visit
to the dashboard. Others need something it does not have, a Spotify account, the
phone app, the Windows taskbar. Those are skipped, never attempted. Pure
decisions only, so they can be tested without a browser.
"""

import re
from urllib.parse import urlparse

# Quest ids that need an account, device or OS the bot does not have. Matched
# against the lowercased URL.
UNREACHABLE_QUESTS = ("spotify", "rewardsapp", "wsb", "taskbar", "xbox", "gamepass", "edge_download")

# Hosts a task link may point at. Anything else (an app store, a partner site,
# the Rewards site's own pages) is left alone.
BING_HOSTS = ("www.bing.com", "bing.com")

NON_TASK_TEXT = ("more activities", "learn more", "order history", "faq", "sitemap", "feedback", "english")

PROGRESS = re.compile(r"(\d+)\s*/\s*(\d+)\s*tasks?", re.I)


def progress(text: str) -> tuple[int, int] | None:
	"""(done, total) from a quest card's "1/4 tasks", or None when it shows none."""
	match = PROGRESS.search(text or "")

	return (int(match.group(1)), int(match.group(2))) if match else None


def wants_quest(href: str, card_text: str = "") -> bool:
	"""Whether to open this quest at all."""
	lowered = (href or "").lower()

	if "/earn/quest/" not in lowered:
		return False

	if any(marker in lowered for marker in UNREACHABLE_QUESTS):
		return False

	seen = progress(card_text)

	# A finished quest has nothing left to do. Unknown progress is worth a look.
	return not (seen and seen[0] >= seen[1])


def is_task_link(href: str, text: str = "") -> bool:
	"""Whether a link on a quest page is a task the bot may open.

	Only a Bing page: an absolute link to bing.com, which a quest opens in a new
	tab, the same way the misc cards do. In-site links (Earn now, Explore now)
	navigate the Rewards page itself, and the first one tried wedged the browser
	for minutes in a supervised run, so they are left to a person.
	"""
	if not href or (text or "").strip().lower() in NON_TASK_TEXT:
		return False

	parsed = urlparse(href)

	return parsed.scheme in ("http", "https") and (parsed.netloc or "").lower() in BING_HOSTS


# The one-off "Get started with Rewards" quest (new members, first 30 days, +1,320). Its tasks are visits to the
# Rewards site's own pages, so they are named here one by one rather than allowed as a class: a link on a quest
# page that leads into the site is otherwise left alone.
ONBOARDING_QUEST = "onboarding_offer"
ONBOARDING_ACTIONS = ("set a goal", "earn now", "learn more", "explore now")


def is_onboarding(href: str) -> bool:
	return ONBOARDING_QUEST in (href or "").lower()


def pick_onboarding(candidates: list[tuple[str, str]], tried: set[str]) -> tuple[str, str] | None:
	"""The first (href, text) of an onboarding task not yet done, else None.

	A finished task shows no link, so a link that is there is a task still open."""
	for href, text in candidates:
		label = (text or "").strip().lower()

		if label in ONBOARDING_ACTIONS and label not in tried and href.startswith("/"):
			return href, label

	return None


def pick_task(candidates: list[tuple[str, str]], tried: set[str]) -> tuple[str, str] | None:
	"""The first (href, text) not yet tried that is a task link, else None."""
	for href, text in candidates:
		if href in tried:
			continue

		if is_task_link(href, text):
			return href, text

	return None
