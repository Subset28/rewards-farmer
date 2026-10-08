# User0332/rewards-farmer

Automation for MS Rewards based on [https://youtu.be/4qdPcMNaioA](https://youtu.be/4qdPcMNaioA).

> Where things stand and what is next: [docs/PLAN.md](docs/PLAN.md). How to run and fix things: [docs/RUNBOOK.md](docs/RUNBOOK.md). What each file is: [docs/MODULES.md](docs/MODULES.md).

## Table of Contents

- [Core Setup & Running Instructions](#core-setup--running-instructions)
	- [Where search queries come from](#where-search-queries-come-from)
	- [Installing Dependencies](#installing-dependencies)
	- [Profile Setup](#profile-setup)
- [If Edge will not start](#if-edge-will-not-start)
- [Running more than one account](#running-more-than-one-account)
- [Docker](#docker)
- [Runbook: checking, rolling out, rolling back](docs/RUNBOOK.md)
- [Rolling changes out one at a time](#rolling-changes-out-one-at-a-time)
- [Pacing](#pacing-not-a-machine-that-does-the-maximum-every-day)
- [A VPN per account (Docker)](#a-vpn-per-account-docker)
- [Logging](#logging)
- [Windows Virtual Desktop (Windows only)](#windows-virtual-desktop-windows-only)


## Core Setup & Running Instructions

IMPORTANT: Use at your own risk. Microsoft may take action against your account for using automated scripts to gain rewards points. The YouTube video contains more details about the techniques implemented to avoid detection of this script.

Clone the repository.

```sh
git clone https://github.com/User0332/rewards-farmer
```

A sample `nouns.txt` file is included in the project root and can be modified by the user to contain seed words for the LLM to complete 20 searches. The wordlist should be separated by newline.

```sh
cd rewards-farmer
# Edit the included nouns.txt file to add or replace words as needed
```

### Where search queries come from

The bot needs short strings to type into Bing. Two backends produce them, set with `QUERY_SOURCE`:

| `QUERY_SOURCE` | Needs | Notes |
| --- | --- | --- |
| `llm` (default) | OpenRouter or Ollama account + model | `llm` via Ollama is the current behaviour, unchanged |
| `trends` | nothing | Google Trends, Wikipedia and Bing autosuggest |

```sh
QUERY_SOURCE=trends python src/main.py          # bash
$env:QUERY_SOURCE="trends"; python src/main.py  # PowerShell
```

`trends` needs no account, no API key and no model download, so the LLM setup below is optional if you use it. If every feed is unreachable it falls back to `nouns.txt` rather than failing the run.

If you would like to use LLMs, you should also configure an LLM provider through a `.env` file in the project root. The script now talks to either OpenRouter or a local OpenAI-compatible LLM endpoint depending on `LLM_PROVIDER`.

Example `.env` values:

```env
LLM_PROVIDER=openrouter
OPENROUTER_API_KEY=your_key_here
OPENROUTER_MODEL=openai/gpt-4o-mini

# Or use a local endpoint instead
# LLM_PROVIDER=local
# LOCAL_LLM_BASE_URL=http://localhost:11434/v1
# LOCAL_LLM_MODEL=gemma3:4b
```

For OpenRouter, the code uses the OpenAI-compatible chat completions API at `https://openrouter.ai/api/v1/chat/completions`. For local models, the endpoint must also be OpenAI-compatible. More configuration options can be found in [`.env.example`](.env.example).

If the configuration options are not provided, they default to `LLM_PROVIDER=local`, `LOCAL_LLM_BASE_URL=http://localhost:11434/v1`, `LOCAL_LLM_MODEL=gemma4:cloud`, and `OPENROUTER_MODEL=openrouter/free`.

You must also provide an image for the script to upload to complete the visual search task. A helper script is included at `src/random_image_for_visual_search.py` that will download an image from Wikipedia named `visual_search.jpg` into the project root for you. You may also provide an image of your own, just ensure that the absolute path of the image is placed in the `VISUAL_SEARCH_IMAGE_PATH` constant at the top of `rewards_tasks.py`.

### Installing Dependencies

Activate the virtual environment & install dependencies (you may have to use `python -m poetry` instead of `poetry`).
You must have Python 3.12+ and Poetry installed.

If `iex (poetry env activate)` fails with *"Cannot bind argument to parameter 'Command' because it is null"*, `poetry install` did not create an environment. Run `python --version` first: an older Python leaves poetry with nothing to activate, and the message explaining that goes to stderr rather than into `iex`.

Windows (PowerShell)

```sh
poetry install
iex (poetry env activate)
```

\*nix (Bash)

```sh
poetry install
eval $(poetry env activate)
```

You must also have a [webdriver for Microsoft Edge](https://learn.microsoft.com/en-us/microsoft-edge/webdriver/?tabs=c-sharp) installed. If you already have the Edge Browser installed, you probably have this component as well.

### Profile Setup

The profile directory in `src/constants.py` is set to `Default`. If this signs you in to a global profile that you do not want to use for automation, then you can create a new profile from within the webdriver instance manually and then change the `PROFILE_NAME` constant to `Profile 1` (or the equivalent number).

Run main.py (`python src/main.py`; paths are resolved from the repository, so it can be started from any directory), wait for the page to launch, and then CTRL-C to quit the application immediately. Sign in to the created profile with your Microsoft account on both Bing and `rewards.bing.com`.

EU Users: you may have to accept a consent banner once on `rewards.bing.com` and on the Bing search page, `bing.com`. Once you consent, your choice will be saved for future runs using the same profile, so you will not need to interact with the banner during automated runs.

Close all webdriver browser instances. Run `main.py` again; the automation should start working.

## If Edge will not start

When the browser fails to start, the log names the likely cause from the driver's own message, and falls back to printing that message as is. Three optional environment variables help when it does not:

| Variable | Effect |
| --- | --- |
| `MSEDGEDRIVER_PATH` | Full path to `msedgedriver` to use, instead of letting selenium look for one. Try this first on *Unable to obtain driver for MicrosoftEdge*. |
| `EDGE_BINARY` | Full path to the Edge executable, for an install selenium does not find on its own. |
| `REWARDS_DRIVER_LOG` | Path to write a verbose msedgedriver log to. On *Chrome instance exited* this log holds Edge's actual reason; attach it to a bug report. |

```sh
$env:REWARDS_DRIVER_LOG="msedgedriver.log"; python src/main.py   # PowerShell
REWARDS_DRIVER_LOG=msedgedriver.log python src/main.py          # bash
```

`src/check_selectors.py` starts Edge the same way and reads the same variables.

## Running more than one account

Rewards is per Microsoft account and the browser profile holds the sign-in, so an account here is a profile directory. `REWARDS_ACCOUNTS` takes a comma separated list, and each name gets its own directory under `data-dir`:

```sh
REWARDS_ACCOUNTS=personal,spare python src/main.py
```

Each is signed in once by hand, the same way as the single profile, using its own directory:

```
msedge --user-data-dir="<repo>\data-dir\personal" --profile-directory=Default https://rewards.bing.com
```

They run one after another, and an account that fails is reported and skipped rather than ending the run, whether it fails to start or dies partway through. Leave `REWARDS_ACCOUNTS` unset and everything behaves exactly as before, using the single profile in `data-dir`.

## Docker

Runs the bot without installing Edge, a driver or Python on the host.

```sh
docker compose build
docker compose run --rm rewards-farmer
```

The container defaults to `QUERY_SOURCE=trends`, so it needs no Ollama account and no model. Set `QUERY_SOURCE=llm` and `OLLAMA_HOST` to a reachable address to use a model instead.

**Sign in first.** The profile in `data-dir` starts logged out. Sign in from inside the container, which opens the Rewards page in a browser there and puts that browser on your screen as a web page:

```sh
docker compose run --rm --service-ports signin
```

Open <http://localhost:6080>, sign in, then close the Edge window on that screen. The container exits on its own and the profile is ready. Nothing is installed on the host, and the host operating system stops mattering, because the profile is written inside the container rather than on the host. The screen is an ordinary web page, so whatever browser you already have will do.

`--service-ports` is not optional. `docker compose run` publishes no ports without it, and the page then never loads.

One account at a time, since it is one browser window:

```sh
REWARDS_ACCOUNTS=personal docker compose run --rm --service-ports signin
```

The port is published on `127.0.0.1` only by default, so it is not reachable from the network. On a headless NAS whose SSH blocks tunnelling, set `SIGNIN_BIND` in `.env` to the NAS's LAN address and open `http://<that address>:6080` instead; that exposes the sign-in page to your home network while the container runs, so stop it when you are done. While the service is up it is showing a live Microsoft sign-in page.

Signing in signs the browser in, not just the website, so Edge may sync bookmarks and autofill into the profile it just created. `data-dir` is a bot profile living in the project directory rather than your everyday browser profile, and it is gitignored, but it is worth knowing what ends up there.

<details>
<summary>Why sign-in has to happen inside the container</summary>

Chromium encrypts cookie values with a key it gets from the operating system, and the container has to be able to unwrap that key to read the profile.

On **Linux** with no keyring running it falls back to a fixed key, which is true both on a plain Linux host and inside the image, so a profile signed in on such a host does carry straight in.

On **Windows** the key is wrapped with DPAPI and tied to the Windows account that wrote it, and the container has no DPAPI. A profile signed in with a normal Windows Edge window reported 73 cookies on disk, of which Edge in the container could read 19 — the ones it had just set itself — while `.MSA.Auth` and `ANON`, the ones the sign-in actually rests on, came back absent. The container starts, looks healthy and behaves as though it were logged out. **macOS** wraps the key with the login Keychain, which the container cannot reach either.

Signing in through the container sidesteps all of this: the profile is written by the same Edge that later reads it, so the two never disagree about the key.

</details>

You can still sign in with a host browser if you prefer, and on a Linux host it works. Close every window of that profile afterwards, and close them rather than killing them: Chromium allows one process per profile directory, and a browser that was killed leaves a `SingletonLock` naming the machine that wrote it, which the container reads as the profile being open somewhere else.

**Provide the visual search image on the host too.** `visual_search.jpg` is not in the repository and is not built into the image, so create it once in the project root and the compose file mounts it in:

```sh
python src/random_image_for_visual_search.py
```

Without it every other task still runs; only the visual search one fails.

Multiple accounts work the same way in the container. Sign each profile in once, one at a time, then run them together:

```sh
REWARDS_ACCOUNTS=personal docker compose run --rm --service-ports signin
REWARDS_ACCOUNTS=spare    docker compose run --rm --service-ports signin

REWARDS_ACCOUNTS=personal,spare docker compose run --rm rewards-farmer
```

`REWARDS_HEADLESS=1` is set in the image. It also works on the host if you want a run with no visible window; the pointer code needs an explicit window size in that mode, which `main.py` sets.

## The brake, alerts and the points record

A run that lands on a sign-in page, a human check or a restriction notice stops, writes `data-dir/PAUSED`, and every scheduled run after it is skipped until someone clears it. The pause covers all accounts, because accounts run from one connection are not independent.

```
python src/safety.py status
python src/safety.py clear
```

Alerts go to a Discord webhook or an [ntfy](https://ntfy.sh) topic, and each account can have its own: set `NOTIFY_URL_DEFAULT` and `NOTIFY_URL_SECOND` (the account's name, upper-cased) in `.env`. `NOTIFY_URL` is the fallback for an account with none of its own. A webhook address is a secret, so keep it in `.env`, which is not committed. Unset, alerts only log.

What is sent: the brake tripping (including an account signed out), a round of searches that earns nothing, a scheduled search or daily run that fails (any exit code but the brake's own 3, which has already alerted), and once per account after each daily run a line with today, month and lifetime points and how far the next level is.

## The journal and the logs

A container's own log is gone when it is recreated, so a new build used to have no idea what the old one had done. Two things in `data-dir` now survive a rebuild:

- `journal.jsonl` records every scheduled run (start, end and outcome, plus each search run's quota reading). A new build reads it to see which planned runs finished, which were cut off and which were missed, and redoes a cut-off or missed search run up to 3 hours late while the 08:00 to 23:00 window is open. A run is skipped when every account's quota is already full. Read it with `python src/journal.py` (today) or `python src/journal.py 3` (three days). The file keeps its newest 6000 lines.
- `logs/<service>.log` holds the readable log lines of each service (`scheduler.log`, `search-scheduler-second.log` and so on), capped at 5 MB each.

Swapping a build mid-day is therefore safe, but not while a run is live: recreating a container kills the run in it. Check that no `msedge --user-data-dir` process is running first.

`data-dir/points.jsonl` gets one line per daily run (today, this month, lifetime). `python src/points_log.py` prints, for each account, the latest reading, the points to that account's next level this month (`REWARDS_LEVEL_TARGETS`) and the daily rate. Pass an account name to see just one.

| Variable | Default | Meaning |
|---|---|---|
| `TRAWL_URL` | unset | Address of a trawl service, used as a fallback for public feeds that refuse a plain request. Never used for account pages. |
| `NOTIFY_URL` | unset | Shared alert address (Discord webhook or ntfy topic) for any account without its own. |
| `NOTIFY_URL_<ACCOUNT>` | unset | One account's own alert address, e.g. `NOTIFY_URL_SECOND`. |
| `REWARDS_SEARCHES_PER_RUN` | `5-8` | Searches one scheduled search run makes before stopping, so the quota fills across the day. |
| `REWARDS_ACCOUNT_GAP_MINUTES` | `20-60` | Wait between one account and the next. Accounts are always worked one at a time. |
| `REWARDS_LEVEL_TARGETS` | `default=750,second=500,third=500` | Monthly points that reach each account's next level, as `name=points,name=points`, for the progress line. An account not listed gets no progress line. |

## Browser identity

A request that says Windows and Chrome 151 while the page says Linux and Edge 154 is a mismatch a bot check looks for. `src/fingerprint_probe.py` compares identity variants on a throwaway profile (never an account's): what the page sees (`navigator.*`, Client Hints), what a server sees, and what two public bot checks say. Run it with `with-xvfb python src/fingerprint_probe.py [baseline|header-hack|linux|windows]`. It found that the old header override set a User-Agent and an `X-Rewards-Source` header on every request, Bing searches included, that disagreed with the browser, so the override is now off unless `REWARDS_APP_HEADERS=1`.

## What the Rewards pages say, and the monthly bonuses

`src/inspect_rewards.py` is a read-only look: it opens each account's Rewards home, earn and dashboard pages, clicks nothing, and saves their text to `data-dir/inspect/<account>-<page>.txt` (it takes the same lock as a scheduled run, so it never overlaps one). Run it with `REWARDS_ACCOUNTS=default,second with-xvfb python src/inspect_rewards.py`.

What it showed, and what each daily run now records in `points.jsonl`:

- **Monthly level-up bonus** (700 Gold, 300 Silver, 100 Member): paid on the first of the month, by the level held at the end of the month before. It is the jump on the first, not a login bonus. An account's first month pays nothing.
- **Default search bonus** (350, 150, 50): searching on 14 days in a month, paid at the start of the next.
- **Bing Star bonus** (up to 3,500, 1,500, 500): scored on whether a month of searching looks consistent and natural; not a task list.
- **Streaks**: the daily set and a Bing search seven days in a row are level-up activities, and Gold needs two a month, so a missed day costs more than one day's points.

The dashboard's "Earned last month" figure for each bonus is saved with the daily points line (`bing_star_last_month`, `level_up_last_month`, `default_search_last_month`), so each first of the month is on record.

## trawl for feeds that refuse a plain request

The public feeds the search queries come from (trends, autosuggest) sometimes answer a plain request with a refusal or a Cloudflare challenge page. If `TRAWL_URL` points at a [trawl](https://github.com/germondai/trawl) service (a FlareSolverr-compatible API that loads the page in a hardened browser), the same page is then asked for through it:

```
TRAWL_URL=http://192.168.35.12:8191
```

It is a fallback only, and unset it does nothing. It is never used for an account: Microsoft's sign-in, Rewards, Bing and related domains are refused by `src/trawl_client.py` whatever the caller asks, because a verification prompt on an account is a reason to stop and look (the brake pauses the run and alerts), not something to click through. In the VPN setup the browsers run inside the account namespaces, which cannot reach the LAN, so trawl is reachable only from the container's own network and the feeds fall back to a plain request there.

## Search queries from OpenRouter's free models

`QUERY_SOURCE=openrouter` asks OpenRouter's free models for the day's search queries (`src/openrouter_queries.py`). The free tier allows 50 requests a day and 20 a minute, so a request is spent on a whole batch of 24 queries, kept as a pool for the day, and a day costs a handful of requests. A hard daily cap below the allowance is enforced across all containers (`OPENROUTER_DAILY_LIMIT`, 40), failed attempts count against it, a 429 or a rejected key makes it go quiet for a while, and anything that goes wrong falls back to the public feeds, so a run is never short of queries.

Put the settings in a `.env` file next to `docker-compose.yml` on the NAS (it is gitignored; never paste the key into chat or a command line you keep in history):

```
QUERY_SOURCE=openrouter
OPENROUTER_API_KEY=sk-or-...
OPENROUTER_MODEL=google/gemma-4-26b-a4b-it:free,google/gemma-4-31b-it:free,nvidia/nemotron-3-super-120b-a12b:free
```

Then `docker compose up -d --force-recreate home`. The task cards (Explore on Bing and so on) keep using the public feeds, so the allowance goes to the daily searches.

- **Sessions and a persona.** The queries come as sessions of two to four searches that narrow a topic, used back to back. An account without interests gets a persona invented once (one request) and kept in `data-dir/persona/<account>.json`, so it has the same interests every day. That makes its searching consistent; it does not make it the owner's own.
- **Free models only.** `OPENROUTER_MODEL` must end in `:free` (or be `openrouter/free`); the paid model has the same name without the suffix, so anything else is replaced by the default with a warning unless `OPENROUTER_ALLOW_PAID=1`. A response that reports a cost stops all requests for a day.
- **The default models** are `google/gemma-4-26b-a4b-it:free`, then `google/gemma-4-31b-it:free`, then NVIDIA's `nvidia/nemotron-3-super-120b-a12b:free` (a different provider, for when Google's shared pool is full; it thinks by default, so replies are stripped of reasoning and the output cap is generous), picked from the free text models by their published latency and availability. `OPENROUTER_MODEL` takes a comma-separated list, tried in order. A 429 whose body says the shared upstream pool is full moves on to the next model; if every model is full it waits 10 minutes and the public feeds cover the gap. A 429 about the account itself stops requests instead. The Inkling free endpoints are for agentic harnesses only, and Nemotron 3 Super thinks by default and would spend its output limit doing so.
- **Errors** follow OpenRouter's documented codes: 429 backs off for the time given, 401/403 and 400/402/404 stop requests for hours (they would only repeat), 502 is treated as brief. Every attempt counts against `OPENROUTER_DAILY_LIMIT`.

Optionally give an account interests, one per line, in `data-dir/interests/<account>.txt` (the first account is `default`). About half its queries are then about those, so its searching has a subject. Free endpoints log what they receive and may train on it, so keep anything personal out of that file. Whatever an account has already searched is never offered again, from any source.

## A second account, with its own hands

Every account types and moves with its own profile (`src/behavior.py`), so two accounts do not look like one operator. The account that was already running keeps the original measurements. Any other account gets a **provisional** profile, stable and different from every other account's, until you record its owner's real one. The log says which kind is in use.

**1. Record the owner's hands**, on the machine and with the hands of the person the account belongs to. One program, about five minutes, one window with a page after each page (typing, then mouse), writing the profile at the end:

```
python src/calibrate.py second
python src/calibrate.py --show second     # what profile an account has, and whether it is recorded
```

It measures their typing rhythm, how often they slip and which kind (a neighbouring key or two letters swapped), how many keys go by before they notice, how long they pause before Backspace, how many slips they fix, their mid-phrase hesitations and the wait before starting; and for the mouse, Fitts' law, how long they take to start moving, how long they hover before a click, how long they hold the button, how often they miss or overshoot, and how straight their path is. The raw keys and movements are kept in `<account>.raw.json` so a better analysis later does not need the person back.

Copy `data-dir/behavior/second.json` to the same place in the NAS's `data-dir`. The finer measurements take effect per account behind the `typing` switch (slip habits, pauses) and the `mouse` switch (hold time, hover); until then only the rhythm and Fitts' law are used, as before. (`typing_test.py`, `fitts_law.py` and `make_behavior_profile.py` still work but only record the rhythm and Fitts' law.)

**2. Sign the account in** through the container, the same way as the first (type the password yourself, it never goes through the bot):

```
REWARDS_ACCOUNTS=second docker compose run --rm --service-ports signin
```

**3. Start its schedulers.** Every account on the home connection is worked by the one `home` container (a daily and a search loop each, see `docker-compose.yml`), so a new one is a pair of commands added there, then:

```
docker compose up -d --force-recreate home
```

They run one at a time with the first account (the run lock makes an overlap wait), by default at 15:00 instead of 09:00 (`SECOND_ANCHOR_HOUR`) and on 3 search runs a day (`SECOND_SEARCH_RUNS_PER_DAY`). A run for a different account than the one that just finished also waits out a 15 minute cooldown (`REWARDS_ACCOUNT_COOLDOWN_MINUTES`, 0 turns it off), so the two are never used back to back.

None of this hides that both accounts share a connection and a machine. It only keeps their activity from overlapping or touching. Two household members on one connection is ordinary; the bot's own patterns are the part that can link them.

A plain sign-out pauses only the account it happened on (`python src/safety.py clear second`). A human check or a restriction notice still pauses every account.

## One command for the whole picture

`python src/status.py` prints, for each account, what today holds (a working or light day, the ramp), its points and how far the next level is, what last month's bonuses paid, and how today's search quota stands, then the brake and today's runs. It is read-only: no browser, nothing changed. On the NAS: `docker exec rewards-farmer-home-1 python src/status.py`.

## Rolling changes out one at a time

Each of the behaviour changes below changes what Microsoft sees from a real account. Tests only prove the code runs; they cannot show whether Microsoft finds the behaviour natural. Only live results do: points earned, verification prompts, and the Bing Star score. Turned on together, an account that gets flagged cannot be traced to the change that did it, and a ban costs the account. So every one of them is **off until it is switched on**, per account, a few days apart, starting with the account that matters least.

| Feature | What it changes |
|---|---|
| `typing` | Typing in the search box: typos are mostly noticed and corrected (backspace and retype), pauses at word boundaries, a short pause before the first key (`src/mimic_typing.py`). |
| `query_sessions` | Queries come in short topical sessions with follow-ups ("x", then "x review") instead of unrelated topics; follow-ups come from Bing's suggestions or from templates (`src/query_sources.py`). |
| `habits` | Each owner has its own favoured times of day for search runs, different on weekends, instead of uniformly random ones (`src/search_scheduler.py`). |

`data-dir/features.json` lists the accounts each feature is on for (`"*"` means every account). Nothing is on by default, and a missing or damaged file means everything is off: the original behaviour, exactly. The file is read on every use, so a change takes effect on the next search without restarting anything.

```
python src/features.py                      what is on
python src/features.py on typing second     switch typing on for the "second" account
python src/features.py off typing second    and off again
```

On the NAS, from `/volume1/docker/rewards-farmer`: `docker run --rm -v "$PWD/data-dir:/app/data-dir" --entrypoint python rewards-farmer:runtime src/features.py on typing second`. `python src/status.py` shows each account's features next to its points.

**Suggested order**, one change at a time and a few days apart, the lower-value account first:

1. `typing` on for the second account. Watch `status.py`, points per day, and any verification prompt or brake alert for a few days. Then the same for the first account.
2. `query_sessions`, the same way.
3. `habits`, the same way.
4. The VPN (see below), the same way.

Turn a feature off again the moment something looks wrong, and tell the changes apart before turning the next one on. The pacing changes (light days, variable totals, the new-account ramp) and the header change were already live before this switch existed.

## Pacing: not a machine that does the maximum every day

An account that earns its full quota at the same rate, every day, from its first day, is the clearest pattern an automated account leaves. `src/pacing.py` makes each account behave more like a person. Everything is worked out from the account's name and the date, so a restart or a new build gives the same answer and nothing has to be saved.

- **Light days.** Now and then (about one day in ten, never two running) an account does only the bare minimum: the daily set, one small search and the daily claim. Never nothing: Rewards counts streaks (the daily set and a Bing search seven days in a row are level-up activities, and Gold needs two a month) and searching on 14 days a month earns the default search bonus, so a day with no activity would cost real points.
- **Variable totals.** On a working day an account fills a share of its search quota (80% to 100%), not always all of it.
- **A ramp.** For an account's first 7 days it asks for less each day (30% rising to a full day), takes no light days, and does only the daily set and its searches.
- **Order.** The accounts of one run are taken in a different order each time.
- **No shared queries.** An account avoids the queries any other account searched in the last 7 days, as well as its own from the last 30.

`python src/pacing.py` prints what today holds for each account. An account's first day is the date of its first points reading, or the day it was first seen; `data-dir/pacing.json` holds it, and you can edit it (`{"default": {"first_day": "2026-09-01"}}`) to say an account is not new.

| Variable | Default | Meaning |
|---|---|---|
| `REWARDS_REST_DAY_CHANCE` | `0.10` | Chance a day is a light day. `0` turns light days off. |
| `REWARDS_MIN_DAILY_FRACTION` | `0.8` (the compose file sets `1.0`) | Least share of the search quota filled on a working day. `1` means always all of it. |
| `REWARDS_RAMP_DAYS` | `7` | Days of an account's ramp. `0` turns it off. |
| `REWARDS_KEEP_ORDER` | `0` | `1` keeps the accounts in the order listed. |
| `REWARDS_APP_HEADERS` | unset | `1` sends the old Windows/`MSRewards` header override. Off by default: it made the request header disagree with the browser's own identity. Set it only if a rewards-only quest stops earning. |

None of this makes automation allowed or undetectable; it only avoids the most regular pattern.

## Behaving like a particular person

Four switches (per account, `python src/features.py on <switch> <account>`), each off until switched on, change what a website can see:

| Switch | What it changes |
|---|---|
| `typing` | corrected typos; with a recorded profile, that person's own rhythm, key holds and overlaps, slips and pauses (`mimic_typing.py`, `human_model.py`) |
| `mouse` | with a recorded profile, that person's own click hold, hover, scatter of move times, and the shape of their pointer paths (`pointer_path.py`) |
| `chains` | searching in tangents: the next search comes from the results page's related searches, with reading between and the odd result opened (`chains.py`, `reading.py`) |
| `query_sessions`, `habits` | topical query groups; each owner's own favoured times |

**Recording a person** takes about eight minutes in one window: `python src/calibrate.py <account>`. Do it again on another day (each sitting is added to `data-dir/behavior/<account>.raw.json`) and the day-to-day spread is measured instead of assumed. `python src/calibrate.py <account> --reanalyze` rebuilds the profile from the recordings with the analysis as it is now.

**The results page ends with scores** from `indistinguishable.py`: a classifier is trained to tell the person's real typing (and mouse paths) from the bot built out of their numbers, on features a page script can see. 0.5 means it is guessing, 1.0 means the bot is obvious. It is checked on phrases it was not fitted on, and each recorded mouse move is left out of its own comparison. It says how well the numbers it can see match; it cannot speak for signals it cannot see (the connection, the browser, what an account does over weeks).

Real-browser checks (throwaway profile, never an account): `typing_events_probe.py`, `mouse_events_probe.py`, `serp_probe.py`, and `chain_probe.py` (memory). A long run keeps itself inside the container's memory with `memory_guard.py`.

## Alerts that say who has to act

`src/health.py` runs at the start of each scheduler cycle and, when something is wrong, sends one message to that account's Discord channel, at most once a day while it lasts:

- `[NEEDS YOU]` is for what only a person can do: the brake has been on for 12+ hours (finish the verification in the sign-in browser, then `python src/safety.py clear <account>`).
- `[NEEDS CLAUDE]` is for the bot itself: two or more failed runs in a day, no points gained for two days, or no run recorded for a day and a half. The message ends with the exact sentence to say after opening Claude Code in the project folder.

`python src/health.py` shows what would be sent right now (it sends nothing). State is in `data-dir/health.json`.

## Footprint: nearly free when idle, light while running

Idle, the schedulers sleep (about 0% CPU, 20-35 MB each). The virtual display (Xvfb, 35-70 MB) is not kept running: with `REWARDS_LAZY_DISPLAY=1`, which the compose scheduler services set, `src/footprint.py` starts one for the length of a run and stops it after. An existing `DISPLAY` (the one-shot service, the VPN container) is left alone. The schedulers also run at nice 10, which every browser they start inherits, so a run yields to the NAS's other containers. Edge's disk and media cache are capped at 64 MB per profile (it had grown to over 500 MB); pages cannot see that.

## A VPN per account (Docker)

`docker-compose.vpn.yml` puts every account behind its own OpenVPN tunnel, in one container, each with its own kill switch and its own VPN location. It is modelled on the binhex `qbittorrentvpn` images (a default-drop iptables firewall, a tunnel that must come up before anything runs, a supervisor that notices a dead tunnel) and on ArmaanOChrome's entrypoint, with one change: a tunnel per account.

**Why namespaces.** This NAS's kernel (4.4) has no iptables `owner` match and ignores per-user routing rules, so accounts cannot be told apart by user. Each account gets a network namespace instead:

```
account namespace                       container
  tun0   <- OpenVPN, the only way out     vh<N>  may reach this account's VPN servers
  vn<N>  ------ veth pair ------------           only (FORWARD), then NAT out of eth0
  firewall: drop all, allow lo, tun0,
  and this account's VPN servers
```

Nothing in a namespace can see another account's tunnel or interfaces. The browser for an account runs inside its namespace (`src/isolation.py`), without the capabilities that could change the firewall. An account whose tunnel is down, or that has no namespace, is skipped and never run on the real connection.

**Per account**, put the provider's files in `data-dir/<account>/openvpn/`, where `<account>` is `default` for the unnamed profile:

```
config.ovpn   the provider's config (certs inline, or beside it in the same folder)
auth.txt      optional: username on the first line, password on the second
timezone      optional: the exit's timezone, such as America/Chicago (the browser reports it)
```

Give every account a different server. The container checks each exit address and refuses a tunnel whose exit cannot be read (so it carries nothing), or is a second account's, or is listed in `HOME_IP_BLACKLIST` if you set one.

**Check it** before relying on it. On a host that allows `NET_ADMIN` and `SYS_ADMIN`, `tests/integration/vpn_netns.sh` builds real namespaces with a stand-in for OpenVPN and checks the separation, the kill switch (tunnel dropped, route pointed at the real side, nothing gets out), the restart and the capability drop. The command to run it is at the top of the script.

**Start it** (the four per-account services would work the accounts on the real connection, so stop them first):

```sh
docker compose stop home
docker compose -f docker-compose.yml -f docker-compose.vpn.yml up -d vpn
```

One container runs both schedulers. `VPN_ACCOUNTS` in `.env` (default `default,second`) says which accounts get a tunnel; they are worked one at a time with the usual gap.

**Using a real provider (Surfshark as the worked example).**
- Download the provider's OpenVPN config (UDP) and put it at `data-dir/<account>/openvpn/config.ovpn`. The login the config asks for goes in `auth.txt` next to it: the provider's *service* username on line 1 and password on line 2 (for Surfshark: Manual setup, Credentials, not your account login). A config that asks for a login and has no `auth.txt` is refused at once.
- **Pin each account to one server.** A provider's city name is usually a pool, and each connection can land on a different server with a different exit address. Edit the `remote` line to a single server's IP and delete `remote-random`, so the account always leaves from the same address. `getent ahostsv4 <the pool name>` lists the pool's servers; the exit address is usually the server's address plus one.
- **A different server per account.** Two accounts on one server share an exit address, which the container refuses. The same login can be used on several tunnels at once. Prefer servers on different networks (check the owner with `curl http://ip-api.com/json/<exit address>`), not just different addresses next to each other.
- A provider's own `ping-restart` / `ping-exit` lines are removed and ours applied, so a dead tunnel is always noticed (Surfshark's `ping-restart 0` would otherwise hide it).
- **Check an address before an account uses it.** `src/vpn_browser_check.py` starts Edge on a throwaway profile, loads a Bing search, the Rewards page and the Microsoft sign-in page, and reports whether any looks like a human check. Run it inside the tunnel: `nsenter --net=<the account's namespace> with-xvfb python src/vpn_browser_check.py`. It signs in to nothing. A clean result is a sample, not a guarantee: challenges more often appear after sign-in or after repeated behaviour.
- `tests/integration/vpn_real_provider.sh` runs the real tunnels end to end against the configs in `data-dir` (copies of them, so nothing live is touched): exits, the kill switch with a real OpenVPN killed, restart, a soak, speed, DNS and IPv6.
- **Stage the rollout.** Start with one account, the cheapest to lose, and leave the others on the normal schedulers: `VPN_ACCOUNTS=second` in `.env`, stop only that account's two schedulers, start the `vpn` service. Add the next account after a few clean days.

**Failures.** A tunnel that dies is restarted on its own (at most every 30 seconds) and its account is held until it is back. If one will not come back after 10 tries the container exits so Docker rebuilds it. Each of these sends that account's Discord channel a message (down, restored, not up, will not come back); an account added later needs its own `NOTIFY_URL_<NAME>` in `.env`, which the container reads in full. Until the tunnel is back nothing leaves except through it.

| Variable | Default | Meaning |
|---|---|---|
| `VPN_ACCOUNTS` | `default,second` | Accounts that get a tunnel. |
| `NAME_SERVERS` | `1.1.1.1,1.0.0.1` | Resolvers used through the tunnels. Avoid Google and OpenDNS: they pass on the client subnet. |
| `HOME_IP_BLACKLIST` | unset | Optional, and not needed: the firewall already makes it impossible for anything to leave outside the tunnel. Comma-separated addresses that must never be an exit, as a second opinion on the result. |
| `VPN_OPTIONS` | unset | Extra OpenVPN command-line options. |

Only the supervisor and the two schedulers run outside the namespaces, on the container's own connection. They send the Discord alerts and nothing else; every account's browser, searches and queries go through its tunnel. The tunnel logs are `data-dir/logs/openvpn-<account>.log`. `auth.txt` is a secret; `data-dir` is gitignored, keep it that way. The container needs `NET_ADMIN` and `SYS_ADMIN` (for the namespaces) and `/dev/net/tun`. WireGuard is not supported: this kernel has no module for it.

**What this does and does not do.** It gives each account its own address and keeps the real one off the wire. It does not make automation allowed, and a commercial VPN address can itself be treated with suspicion by Microsoft. Use one location per account, keep it stable, and put the account's real timezone in `timezone`. Start with one account and watch for sign-in challenges before adding more.

## Logging

The script logs to the console. Two optional environment variables change that:

| Variable | Default | Effect |
| --- | --- | --- |
| `REWARDS_FARMER_LOG_LEVEL` | `INFO` | Set to `DEBUG` to also attach the full stack trace to every `[FAIL]` line. |
| `REWARDS_FARMER_LOG_FILE` | unset | Path to also write the log to, useful for unattended runs. |

Windows (PowerShell)
```sh
$env:REWARDS_FARMER_LOG_LEVEL="DEBUG"; $env:REWARDS_FARMER_LOG_FILE="run.log"; python src/main.py
```

*nix (Bash)
```sh
REWARDS_FARMER_LOG_LEVEL=DEBUG REWARDS_FARMER_LOG_FILE=run.log python src/main.py
```

If you are opening an issue about a crash, running with `REWARDS_FARMER_LOG_LEVEL=DEBUG` and attaching the log is the most useful thing you can include.

## Windows Virtual Desktop (Windows only)

To run the browser on a separate Windows Virtual Desktop so searches run in the background without interrupting your current workspace:

| Variable | Default | Effect |
| --- | --- | --- |
| `USE_VIRTUAL_DESKTOP` | `false` | When `true`, automatically creates a new Windows Virtual Desktop via `Win+Ctrl+D` and launches the browser there. Windows only. |
| `SWITCH_BACK_TO_MAIN_DESKTOP` | `true` | When `true` (and `USE_VIRTUAL_DESKTOP` is enabled), automatically switches back to your starting desktop after launching Edge. |
| `SWITCH_BACK_DELAY_SECONDS` | `1.5` | Delay in seconds to wait before switching back, giving Edge time to attach its window to the new desktop. |
| `CLEANUP_VIRTUAL_DESKTOP` | `true` | When `true`, automatically closes the created worker virtual desktop via `Win+Ctrl+F4` after completing all profiles and pressing Enter, returning focus to your main desktop. |

Set these in your `.env` file or provide them as environment variables:

Windows (PowerShell)
```sh
$env:USE_VIRTUAL_DESKTOP="true"; python src/main.py
```

> **Note:** The script automatically detects which virtual desktop you started from and calculates the exact number of navigation hops so it returns directly to your starting desktop. When `CLEANUP_VIRTUAL_DESKTOP=true`, the worker desktop is safely closed after you press Enter on exit, returning you to your main desktop.

Please open up a GitHub issue if you run into any difficulties.

