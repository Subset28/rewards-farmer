"""Microsoft Rewards Bing Star Bonus & Monthly Behavioral Heuristic Inspector.

    python src/star_bonus.py

The Bing Star Bonus (up to 3,500 pts for Gold / 1,500 for Silver) is Microsoft Rewards'
monthly automated behavioral heuristic audit. Microsoft's anti-bot system evaluates
an account's search entropy, query variety, dwell times, and cadence across the preceding
calendar month:
  - 3,500 / 3,500 (~100%): Treated as completely organic human browsing.
  - 1,000 - 3,000: Partial trust; some automated or low-entropy patterns detected.
  - 5 - 50: Severe bot heuristic penalty (e.g. 5/3,500 = 0.14% trust).
  - Pending: Current month is actively being evaluated; drops on the 1st of next month.
  - Not eligible: Account was new or had insufficient activity during the prior month.
"""

from datetime import datetime, timezone
import re
from typing import Any, Dict, List, Optional

import accounts
import points_log
import status


def parse_star_score(score_str: Optional[str]) -> Dict[str, Any]:
	"""Parse and evaluate a Star Bonus score string from Microsoft Rewards."""
	if not score_str:
		return {
			"raw": "None",
			"earned": None,
			"max": None,
			"percent": None,
			"category": "unknown",
			"rating": "UNKNOWN",
			"verdict": "No bonus data on record yet.",
		}

	cleaned = score_str.strip()
	lower = cleaned.lower()

	if "pending" in lower:
		return {
			"raw": cleaned,
			"earned": None,
			"max": None,
			"percent": None,
			"category": "pending",
			"rating": "EVALUATING",
			"verdict": "Under active evaluation. Final score and points drop on the 1st of the month.",
		}

	if "not eligible" in lower or "ineligible" in lower:
		return {
			"raw": cleaned,
			"earned": None,
			"max": None,
			"percent": None,
			"category": "not_eligible",
			"rating": "NEW_ACCOUNT",
			"verdict": "Not eligible for prior month (account was newly created or inactive).",
		}

	match = re.search(r"([\d,]+)\s*/\s*([\d,]+)", cleaned)
	if match:
		earned = int(match.group(1).replace(",", ""))
		max_pts = int(match.group(2).replace(",", ""))
		pct = (earned / max_pts * 100.0) if max_pts > 0 else 0.0

		if pct >= 90.0:
			category = "human_high"
			rating = "HUMAN (PASSED)"
			verdict = f"Full organic trust ({pct:.1f}%). Microsoft heuristic scored activity as genuine."
		elif pct >= 30.0:
			category = "partial_trust"
			rating = "PARTIAL TRUST"
			verdict = f"Moderate score ({pct:.1f}%). Some searches or patterns were flagged as low-entropy."
		else:
			category = "flagged_bot"
			rating = "FLAGGED BY HEURISTIC"
			verdict = f"Severe bot penalty ({pct:.2f}%: {earned}/{max_pts} pts). Heuristic detected automated patterns."

		return {
			"raw": cleaned,
			"earned": earned,
			"max": max_pts,
			"percent": round(pct, 2),
			"category": category,
			"rating": rating,
			"verdict": verdict,
		}

	return {
		"raw": cleaned,
		"earned": None,
		"max": None,
		"percent": None,
		"category": "custom",
		"rating": cleaned,
		"verdict": f"Custom status reported by dashboard: {cleaned}",
	}


def account_bonus_history(name: str) -> List[Dict[str, Any]]:
	"""Return distinct historical bonus entries for an account, sorted chronologically."""
	rows = points_log.history(name)
	seen = []

	for row in rows:
		bonuses = {
			"time": row.get("time", ""),
			"bing_star": row.get("bing_star_last_month"),
			"level_up": row.get("level_up_last_month"),
			"default_search": row.get("default_search_last_month"),
		}
		if any(bonuses.values()):
			# Avoid duplicate consecutive readings
			if not seen or any(seen[-1].get(k) != v for k, v in bonuses.items() if k != "time"):
				seen.append(bonuses)

	return seen


def get_all_bonus_data() -> Dict[str, Any]:
	"""Structured bonus summary across all known accounts."""
	names = status.known_names()
	result: Dict[str, Any] = {"accounts": {}, "next_evaluation": None}

	now = datetime.now()
	# Next payout is the 1st of the next calendar month
	if now.month == 12:
		next_payout = datetime(now.year + 1, 1, 1, 0, 0, 0)
	else:
		next_payout = datetime(now.year, now.month + 1, 1, 0, 0, 0)

	delta = next_payout - now
	days_left = delta.days
	hours_left = delta.seconds // 3600
	result["next_evaluation"] = {
		"payout_date": next_payout.strftime("%Y-%m-%d"),
		"time_remaining": f"{days_left}d {hours_left}h",
	}

	for name in names:
		rows = points_log.history(name)
		last = rows[-1] if rows else {}
		raw_star = last.get("bing_star_last_month")
		raw_level = last.get("level_up_last_month")
		raw_default = last.get("default_search_last_month")

		star_eval = parse_star_score(raw_star)
		history = account_bonus_history(name)

		result["accounts"][name] = {
			"star_bonus": star_eval,
			"level_up_bonus": raw_level or "Not recorded",
			"default_search_bonus": raw_default or "Not recorded",
			"last_checked": last.get("time"),
			"history": history,
		}

	return result


def format_star_bonus_report() -> str:
	"""Generate a terminal-ready formatted report for all accounts."""
	data = get_all_bonus_data()
	lines = []

	lines.append("=" * 78)
	lines.append(" MICROSOFT REWARDS: BING STAR BONUS & BOT DETECTION SCOREBOARD")
	lines.append("=" * 78)
	lines.append(
		"The Bing Star Bonus is Microsoft Rewards' monthly behavioral heuristic audit.\n"
		"Scores reflect whether Microsoft's anti-bot system deemed the previous month's\n"
		"search activity as organic human browsing vs. automated bot traffic.\n"
		"  * Full Score (e.g. 3,500/3,500): 100% Trust (Completely Organic)\n"
		"  * Low Score  (e.g. 5/3,500):     Bot Heuristic Triggered (Penalized)\n"
		"  * Pending:                      Under evaluation (Scores drop 1st of each month)"
	)
	lines.append("-" * 78)

	next_info = data.get("next_evaluation", {})
	lines.append(
		f"Next Monthly Payout / Score Drop: {next_info.get('payout_date')} "
		f"(in {next_info.get('time_remaining')})"
	)
	lines.append("-" * 78)

	for name, acc in data.get("accounts", {}).items():
		star = acc["star_bonus"]
		lines.append(f"\n[{name.upper()}]")
		lines.append(f"  Star Bonus Score:  {star.get('raw')} -> {star.get('rating')}")
		if star.get("percent") is not None:
			lines.append(f"  Trust Rating:      {star.get('percent'):.2f}%")
		lines.append(f"  Heuristic Verdict: {star.get('verdict')}")
		lines.append(f"  Level-Up Bonus:    {acc.get('level_up_bonus')}")
		lines.append(f"  Default Search:    {acc.get('default_search_bonus')}")
		lines.append(f"  Last Checked:      {acc.get('last_checked') or 'Never'}")

		# Historical progression
		history = acc.get("history", [])
		if len(history) > 1:
			lines.append("  Historical Progress:")
			for h in history[-4:]:
				lines.append(
					f"    - {h.get('time')[:10]}: Star={h.get('bing_star') or '?'}, "
					f"LevelUp={h.get('level_up') or '?'}, DefSearch={h.get('default_search') or '?'}"
				)

	lines.append("\n" + "=" * 78)
	return "\n".join(lines)


if __name__ == "__main__":
	print(format_star_bonus_report())
