# Runbook

How to look at the bot, move it forward, and pull it back. Everything here is a command you can paste. The NAS is `ssh synology`, the project is in `/volume1/docker/rewards-farmer`, and `docker` is `/usr/local/bin/docker` there (over `ssh` the bare `docker` may not be on the path, so use the full path in the one-line commands).

## Is it healthy? (once a day)

```sh
cd /volume1/docker/rewards-farmer
docker exec rewards-farmer-scheduler-1 python src/status.py
```

For each account: what today holds (working day, light day, new-account ramp), which features are switched on, points and distance to the next level, what last month's bonuses paid, and how today's search quota stands. Then the brake (`running normally` or `PAUSED`) and today's runs.

Healthy looks like: brake normal, every run `ok` or `skipped (quota already complete)`, points rising by a normal amount. Look closer if: `PAUSED`, a run `failed`, a day with far fewer points than usual, or a Discord alert.

More detail:

```sh
docker exec rewards-farmer-scheduler-1 python src/journal.py 3     # the last 3 days of runs
docker exec rewards-farmer-scheduler-1 python src/points_log.py    # points per account
docker exec rewards-farmer-scheduler-1 python src/safety.py status # the brake
tail -50 data-dir/logs/search-scheduler.log                        # readable logs, one per service
```

## Rolling a behaviour change out (and back)

Typing, mouse, query sessions and per-owner habits change what Microsoft sees, so each is **off until you switch it on, one account at a time**. Order: `typing`, then `mouse`, then `query_sessions`, then `habits`, then the VPN. Start with the account that matters least ("second"), give each step a few days, and check `status.py` between steps.

```sh
F='docker run --rm -v /volume1/docker/rewards-farmer/data-dir:/app/data-dir --entrypoint python rewards-farmer:runtime src/features.py'
$F                              # what is on
$F on typing default            # switch typing on for the first account
$F off typing second            # roll it back, effective on the next search, no restart
```

Turn it off at once if you see a brake alert, a verification prompt, or points falling. Switch on only one thing at a time, or you cannot tell which one caused it.

## The brake and verification prompts

A sign-in page, a human check or a restriction notice pauses the account and sends its Discord channel an alert. The bot never tries to click through a verification. You do:

1. Open the sign-in page on your LAN: `REWARDS_ACCOUNTS=second docker compose run --rm --service-ports signin`, then `http://<the NAS address>:6080` (the address is `SIGNIN_BIND` in `.env`).
2. Finish the verification yourself in that browser, close it, and let the container exit.
3. `docker exec rewards-farmer-scheduler-1 python src/safety.py clear second` (or `clear` with no name for a pause that covers every account).

## Deploying a change

Never recreate the containers while a run is live: it kills the run and leaves a stale lock. `scripts/deploy.sh` does all of this safely: it deploys what is committed, restarts every running container (the VPN one too, which the plain `up` below does not touch) only when no browser is mid-run, and checks each one is on the new image. Run it from the project folder; it exits 3 if a run was live (try again later) and 4 if any container is still on old code.

```sh
scripts/deploy.sh
```

The manual steps it replaces, for reference:

```sh
git archive HEAD . | ssh synology 'cd /volume1/docker/rewards-farmer && tar -xf - && sed -i "s/\r$//" src/*.py && docker compose build rewards-farmer'
ssh synology 'for c in $(docker ps --format "{{.Names}}" | grep "rewards-farmer-.*scheduler"); do docker exec $c sh -c "ps -eo args | grep -c \"[m]sedge .*--user-data-dir\""; done'   # all 0 means idle
ssh synology 'cd /volume1/docker/rewards-farmer && docker compose --profile second up -d --force-recreate scheduler search-scheduler scheduler-second search-scheduler-second'
```

Deploy once a batch is finished and tested, not per fix. Secrets (webhooks, keys) live in the NAS `.env` and in `data-dir`, never in git.

## Turning the VPN on (the last rollout step)

Each account gets its own tunnel and kill switch in one container. Files per account in `data-dir/<account>/openvpn/`: `config.ovpn`, `auth.txt`, and `timezone`. Read the README's VPN section first. The short version, starting with one account:

```sh
# 1. prove it end to end first (touches copies of the configs, not the live data)
docker run --rm -i --cap-add NET_ADMIN --cap-add SYS_ADMIN --device /dev/net/tun --sysctl net.ipv4.ip_forward=1 \
  -v "$PWD/data-dir:/app/data-dir:ro" -v "$PWD/src:/app/src:ro" --entrypoint bash rewards-farmer:runtime -s < tests/integration/vpn_real_provider.sh
# 2. only the second account on the VPN: in .env set VPN_ACCOUNTS=second, then
docker compose stop scheduler-second search-scheduler-second
docker compose -f docker-compose.yml -f docker-compose.vpn.yml up -d vpn
```

Go back: `docker compose -f docker-compose.yml -f docker-compose.vpn.yml stop vpn`, then `docker compose --profile second up -d scheduler-second search-scheduler-second`.

While it runs, a tunnel that drops is restarted by itself and that account is held until it is back; nothing leaves on the real connection. You get a Discord alert for each of down, restored, not up, and will not come back.

## When a Discord alert says it needs someone

`[NEEDS CLAUDE] <account>: ...` means open Claude Code in the rewards-farmer folder and say the sentence at the end of the message. `[NEEDS YOU]` means a human step (a verification in the sign-in browser, then `safety.py clear`). `python src/health.py` lists what is wrong at the moment without sending anything.

## Memory and idle cost

Idle: each scheduler container is about 20-35 MB and ~0% CPU, with no display running. During a run Edge is the whole cost, at low priority. See what a container really uses with `docker stats --no-stream` (the cgroup figure includes reclaimable page cache, so "near the limit" is not itself a problem).

## What lives where

| Thing | Where |
|---|---|
| Features per account | `data-dir/features.json` |
| Each day's runs and quota | `data-dir/journal.jsonl` |
| Points readings, with last month's bonuses | `data-dir/points.jsonl` |
| When each account started (for the new-account ramp) | `data-dir/pacing.json` |
| Readable logs, one per service | `data-dir/logs/` |
| Behaviour profiles (typing, mouse) | `data-dir/behavior/` |
| Webhooks, API keys, `SIGNIN_BIND`, `TRAWL_URL` | the NAS `.env` (never in git) |
| VPN configs and logins | `data-dir/<account>/openvpn/` (never in git) |

## Already live, not behind a switch

Light days and variable daily totals, the 7-day ramp for a new account, shuffled account order, no shared queries between accounts, the run journal and its catch-up, one clock for all dates, the Discord alerts, and the browser identity fix (the header override is off unless `REWARDS_APP_HEADERS=1`). `python src/pacing.py` shows what today holds.
