# Handoff, 2026-10-08 evening

Where the project stands, what to do next, and how to operate it. No secrets here. `docs/PLAN.md` is the long plan,
`docs/RUNBOOK.md` the day-to-day commands, `docs/MODULES.md` the module map.

## Live state

- **One container** on the NAS, `rewards-farmer-home-1`: default (the brother), second (the owner) and third (the owner's
  mother), each with a daily loop and a search loop, plus the control interface. All on the home connection (a clean
  Verizon residential address); the old VPN exit was a flagged data-centre address, so the `vpn` container was removed on 10-08.
- **Typing and mouse personalization are always on.** There are no switches for them. `src/gate.py` stops an account that
  has no recorded profile (or whose stored score is above 0.70) and tells its owner once a day.
- **Switches left** (all off): `chains` (searching in tangents; opening result pages is off because it exhausted memory),
  `query_sessions`. Each is to be deleted once it has run cleanly on a real account.
- **Run times:** each account searches in its own favoured times of the day (always on), and no two accounts start within 45 minutes of each other.
- **Pacing:** a working day fills the whole search quota (minimum fraction 1.0, since 10-08); about one day in ten is a light
  day; new accounts ramp (second 7 days, third 21 days).
- **No snapshots, no backups** (the owner's decision, 10-08): the brake and git history cover mistakes. Details of a failure
  or a quest go to the log.
- **Memory limit:** 2 GB per container.

## Operating it

```
bash scripts/ctl.sh status            accounts, brake, browsers running, today's runs, health
bash scripts/ctl.sh tasks [days]      which tasks work, which are failing now
bash scripts/ctl.sh settings          what is on and how it is set (no secrets)
bash scripts/ctl.sh runs [days] | logs [name [lines]]
bash scripts/ctl.sh pause [account] [why] | resume [account]
bash scripts/deploy.sh [ref]          deploy committed code between runs (refuses mid-run; rolls back with a tag)
```

## Do next, in order

1. Check the first runs on the new setup: searches for default and second, second's daily run, and the first bot run for
   third. `ctl.sh status` and `ctl.sh tasks`.
2. **Visual search is skipped on the brother's account every day.** (The owner's account is on its ramp and does not try it
   until about 10-11.) The brother's daily run (about 10:42 on 10-09) logs the streak labels the page offered: read them with
   `ctl.sh logs scheduler.log 200` and fix `element_selectors.get_open_visual_search_sidebar`.
3. Third's calibration scored typing 0.64 / mouse 0.68 from one short sitting. Have her record again on another day
   (`python src/calibrate.py third`, with the clearer screens), re-score, and copy the profile over only if it is at least as
   good. Until a bot run of hers succeeds, her daily set and search are done by hand.
4. **The one-time "Get started with Rewards" quest (+1,320 points, new accounts, first 30 days).** Seven tasks. Done by
   the bot now (`rewards_tasks.work_onboarding`, allowed during the ramp): browse Earn, learn the level, explore the
   dashboard (opens the six "+5" sections), plus the plain search and daily set. On 10-08 the owner's account went 2/7 to
   5/7. **Open:** "Set a Rewards goal": the control is a react-aria switch ("Set as your Rewards goal") on a gift card's
   page (`/redeem/sku/000800000064`, Amazon); clicking it (Selenium, ActionChains, JS, keyboard) changes nothing and fires
   no network call, so the bot cannot tick it. The owner can tick it by hand in ten seconds (Redeem, Amazon gift card,
   switch on), which finishes that task; the last task, "Search for 7 days", then completes by itself and pays +1,320.
   The mother's account has the same quest: she needs the same one-off tick. `REWARDS_ONLY_STEPS=Quests` runs just that
   step by hand (wrap `src/main.py` in `footprint.virtual_display()`; see the log lines `Get-started task`).
5. Read the level targets from the dashboard ("Progress towards Silver, points to go") instead of `REWARDS_LEVEL_TARGETS`.
6. Build the weekly Discord summary and the redemption reminder (6,500 points).
7. Housekeeping: stale rollback copies on the NAS, the old `snapshots` and `backups` folders and the BACKUP_* lines in the
   NAS `.env`, the private `rewards-backups` GitHub repository (the owner to confirm deleting it), the upstream page fixes,
   GitHub Actions on the fork, third's Discord webhook.

## Longer term (the owner's goals)

Reach Gold on every account; keep perfecting how natural the behavior is; keep the home IP clean; scale to more accounts
(each with its own clean connection, accounts created by the owner) only after the three have run cleanly for about a month.

## Decisions waiting for the owner

- Third lives in the same home as the others, so the Ashburn VPN exit is the odd one out. Moving her to the home connection
  would remove the `vpn` container (one container in all). Not decided.
- The gift-card vendor (`redeem.rewardlink.com`) challenges the home IP but not mobile data; no public list shows the IP.
  Redeem on mobile data. Not a reason to stop, and not proof of anything about the bot.

## Rules that came out of the work

- Never chain tests, commit and push with `;`, and never pipe the test run into `tail` (it hides a failure): write the
  output to a file, check the exit code, then commit. Never kill containers by a filter with `-q --format`.
- Personalization is the only path: no generic fallback, no toggle that can be wrong.
- Stop on any sign of trouble (`safety.py`); recommend, then wait for the owner on anything about an account.
