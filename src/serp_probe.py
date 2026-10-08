"""What a Bing results page offers a person to follow next, found from a real page.

    with-xvfb python src/serp_probe.py "why do i need vitamin c"

Starts Edge on a throwaway profile (never an account's), opens the search results for a query, and lists
the places a follow-up could come from: related searches, "people also ask" questions, and result titles,
with the selector that found each. Used to choose selectors that really exist on the page.
"""

import json
import os
import sys
import tempfile
import time
import urllib.parse

import accounts
import browser

CANDIDATES = {
	"related_searches": ["#brs a", ".b_rs a", "#b_context .b_rs a", "li.b_ans .b_rs a"],
	"people_also_ask": [".rs_questions", ".b_ans .df_alaqs", "[class*='rs_qa']", ".b_algo ~ li .b_vList a", "div.b_rich .b_focusTextLarge"],
	"related_sidebar": ["#b_context a", "#b_context .b_entityTP a"],
	"result_titles": ["#b_results .b_algo h2 a", "li.b_algo h2 a"],
	"search_box": ["#sb_form_q", "textarea#sb_form_q", "input[name='q']"],
}


def run(query: str) -> dict:
	with tempfile.TemporaryDirectory(prefix="serp-probe-") as profile:
		account = accounts.Account(name="probe-serp", user_data_dir=profile, profile_name="Default")
		driver = browser.start_driver(account)

		if driver is None:
			return {"error": "driver did not start"}

		try:
			driver.get("https://www.bing.com/search?q=" + urllib.parse.quote_plus(query))
			time.sleep(5)
			found = {"url": driver.current_url[:120], "title": driver.title[:80]}

			for label, selectors in CANDIDATES.items():
				found[label] = {}

				for selector in selectors:
					texts = driver.execute_script(
						"return [...document.querySelectorAll(arguments[0])].slice(0, 12).map(e => (e.innerText || e.value || '').trim().replace(/\\s+/g, ' ').slice(0, 90)).filter(Boolean);",
						selector,
					)

					if texts:
						found[label][selector] = texts

			found["ids_in_content"] = driver.execute_script(
				"return [...document.querySelectorAll('#b_content [id], #b_context [id]')].map(e => e.id).slice(0, 40);"
			)
			found["body_has_captcha_words"] = any(w in driver.page_source.lower() for w in ("captcha", "verify you are a human", "unusual traffic"))

			return found
		finally:
			driver.quit()


if __name__ == "__main__":
	print(json.dumps(run(" ".join(sys.argv[1:]) or "why do i need vitamin c"), indent=1, ensure_ascii=False))
	sys.exit(0 if os.name else 0)
