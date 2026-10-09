# Handoff, 2026-10-08 evening

Where the project stands, what to do next, and how to operate it. No secrets here. `docs/PLAN.md` is the long plan,
`docs/RUNBOOK.md` the day-to-day commands, `docs/MODULES.md` the module map.

## Live state

- **Two containers** on the NAS: `rewards-farmer-home-1` (default = the brother, second = the owner; each has a daily loop and
  a search loop, plus the control interface) and `rewards-farmer-vpn-1` (third = the owner's mother, behind her own VPN exit).
- **Typing and mouse personalization are always on.** There are no switches for them. `src/gate.py` stops an account that
  has no recorded profile (or whose stored score is above 0.70) and tells its owner once a day.
- **Switches left** (all off): `chains` (searching in tangents; opening result pages is off because it exhausted memory),
  `query_sessions`, `habits`. Each is to be deleted once it has run cleanly on a real account.
- **Pacing:** a normal day fills at least 65% of the search quota; new accounts ramp (second 7 days, third 21 days).
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
4. Build the weekly Discord summary and the redemption reminder (6,500 points).
5. Housekeeping: stale rollback copies on the NAS, the upstream page fixes, GitHub Actions on the fork, third's Discord webhook.

## Decisions waiting for the owner

- Third lives in the same home as the others, so the Ashburn VPN exit is the odd one out. Moving her to the home connection
  would remove the `vpn` container (one container in all). Not decided.
- The gift-card vendor (`redeem.rewardlink.com`) challenges the home IP but not mobile data; no public list shows the IP.
  Redeem on mobile data. Not a reason to stop, and not proof of anything about the bot.

## Rules that came out of the work

- Never chain tests, commit and push with `;`. Never kill containers by a filter with `-q --format`.
- Personalization is the only path: no generic fallback, no toggle that can be wrong.
- Stop on any sign of trouble (`safety.py`); recommend, then wait for the owner on anything about an account.
