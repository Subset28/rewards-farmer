# Plan

The one place that says where things stand, what is next, and what is still open. Update it when a step is done. How to do each thing is in `RUNBOOK.md`; what each file is, in `MODULES.md`.

## Where the three accounts stand (2026-10-07)

| Account | Whose | Level | Connection | Pacing | Switched on |
|---|---|---|---|---|---|
| `default` | brother | Gold | home | normal | nothing |
| `second` | the owner | Member, Silver soon | home | normal (ramp ended about 10-11) | `typing` |
| `third` | mom | new | **own VPN exit** (Surfshark Ashburn, 185.156.46.101) | 21-day ramp from 10-07, light | nothing |

`third` runs only inside the `vpn` container, never as plain `scheduler-third`. It is the VPN's first live account.

## Order of the rollout

One change at a time, one account at a time, a few days apart, `python src/status.py` clean between steps. A step slips on a brake alert, a verification prompt, or points falling. Never two changes on one account in the same few days.

| Date | Step | Needs first |
|---|---|---|
| 10-05 | `typing` on for `second` | done |
| 10-08 | `typing` on for `default` | two clean days on `second` |
| 10-11 | `mouse` on for `second` | `second`'s hands recorded (`calibrate.py second`) |
| 10-14 | `mouse` on for `default` | `default`'s hands recorded |
| 10-17 | `query_sessions` on for `second` | |
| 10-20 | `query_sessions` on for `default` | |
| 10-23 | `habits` on for `second` | |
| 10-26 | `habits` on for `default` | |
| 10-29 | VPN for `second` | a real-provider test (`vpn_three_accounts.sh`) the same week |
| 11-01 | read what each account's monthly bonuses paid. This is also the first full month of bot activity, so the Bing Star score (an organic-use score; September's 5 of 3,500 covered only about five days of bot use) is the first real scoreboard for the behaviour work | |
| 11-02 | VPN for `default` | |

`third`'s own steps: `typing` and `mouse` only after its hands are recorded and its ramp is over (about 10-28), and only after the other accounts have shown a clean result.

## Open items, most important first

1. **Record the hands** of all three people with `python src/calibrate.py <account>` (5 minutes each). Until `data-dir/behavior/<account>.json` exists the account uses a provisional profile.
2. **Bring in the upstream page fixes.** The fork is 36 commits behind `User0332/rewards-farmer`. Small ones that matter: Explore on Bing cards that are still loading or went stale (worth about 60 points a day), the Visual search sidebar states (ours skips it as "not available"), forcing English pages, the cursor overlay failing softly. Do them one by one as cherry-picks on a branch (the full merge conflicts in seven files), test on a throwaway profile, then deploy.
3. **See a task stop paying.** The health check catches a failed run or a stalled account, not one task that quietly earns 0. Record each task's payout and alert when one that used to pay pays nothing for three days.
4. **A copy of `data-dir` off the NAS.** `backup.py` keeps 14 daily copies on the same disk, which covers a bad edit or deploy, not a dead NAS. Needs a place to copy to (a decision for the owner).
5. **Know when the NAS itself is off.** The Discord alerts come from the schedulers, so a dead NAS is silent. Needs an outside "heartbeat" service (a decision for the owner).
6. **Third account's Discord webhook** (`NOTIFY_URL_THIRD` in the NAS `.env`) and the owner's confirmation that mom agrees to the terms risk.
7. **Housekeeping on the NAS.** `src_prev`, `Dockerfile.prev` and `docker-compose.yml.prev` are old hand-made rollback copies. Rollback is now `scripts/deploy.sh <tag>`; delete them once that has been used once.

## Rules that stay

- Deploy only with `scripts/deploy.sh`: it deploys committed code, restarts every running container (the VPN one too) only when no browser is mid-run, checks each is on the new image, and tags it.
- Small tested fixes go straight to `main`; a pull request is for big or risky changes (the upstream merge is one).
- Behaviour that Microsoft can see is switched on per account in `features.json`, never for everyone at once.
- A change to what the browser does is checked on a throwaway profile before it touches an account.
- Secrets (webhooks, keys, VPN logins) live in the NAS `.env` and `data-dir`, never in git or chat.
- A real, consenting person owns every account. A bot-verification prompt is cleared by that person, never by the bot.

## Known limits

- The unit tests prove the logic, not the live pages. A change on Microsoft's side shows up as a task earning nothing until item 3 is built.
- Everything is on one disk on one NAS and one home connection.
- The browser needs about 1.3 GB while a run is live, so the 1.5 GB container limit stays.
