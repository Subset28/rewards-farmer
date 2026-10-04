# User0332/rewards-farmer

Automation for MS Rewards based on [https://youtu.be/4qdPcMNaioA](https://youtu.be/4qdPcMNaioA).

## Table of Contents

- [Core Setup & Running Instructions](#core-setup--running-instructions)
	- [Where search queries come from](#where-search-queries-come-from)
	- [Installing Dependencies](#installing-dependencies)
	- [Profile Setup](#profile-setup)
- [If Edge will not start](#if-edge-will-not-start)
- [Running more than one account](#running-more-than-one-account)
- [Docker](#docker)
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

The port is published on `127.0.0.1` only, so it is not reachable from the network. While the service is up it is showing a live Microsoft sign-in page.

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

Set `NOTIFY_URL` to an [ntfy](https://ntfy.sh) topic to get a phone alert when the brake trips or a round of searches earns nothing. Unset, it only logs.

`data-dir/points.jsonl` gets one line per daily run (today, this month, lifetime). `python src/points_log.py` prints the latest, the points to the next level this month (`REWARDS_GOLD_AT`, default 750) and the daily rate.

| Variable | Default | Meaning |
|---|---|---|
| `NOTIFY_URL` | unset | ntfy topic URL for alerts. |
| `REWARDS_SEARCHES_PER_RUN` | `5-8` | Searches one scheduled search run makes before stopping, so the quota fills across the day. |
| `REWARDS_ACCOUNT_GAP_MINUTES` | `20-60` | Wait between one account and the next. Accounts are always worked one at a time. |
| `REWARDS_GOLD_AT` | `750` | Monthly points that reach the next level, for the progress line. |

## Search queries from OpenRouter's free models

`QUERY_SOURCE=openrouter` asks OpenRouter's free models for the day's search queries (`src/openrouter_queries.py`). The free tier allows 50 requests a day and 20 a minute, so a request is spent on a whole batch of 24 queries, kept as a pool for the day, and a day costs a handful of requests. A hard daily cap below the allowance is enforced across all containers (`OPENROUTER_DAILY_LIMIT`, 40), failed attempts count against it, a 429 or a rejected key makes it go quiet for a while, and anything that goes wrong falls back to the public feeds, so a run is never short of queries.

Put the settings in a `.env` file next to `docker-compose.yml` on the NAS (it is gitignored; never paste the key into chat or a command line you keep in history):

```
QUERY_SOURCE=openrouter
OPENROUTER_API_KEY=sk-or-...
OPENROUTER_MODEL=google/gemma-4-26b-a4b-it:free
```

Then `docker compose up -d --force-recreate scheduler search-scheduler`. The task cards (Explore on Bing and so on) keep using the public feeds, so the allowance goes to the daily searches.

- **Sessions and a persona.** The queries come as sessions of two to four searches that narrow a topic, used back to back. An account without interests gets a persona invented once (one request) and kept in `data-dir/persona/<account>.json`, so it has the same interests every day. That makes its searching consistent; it does not make it the owner's own.
- **Free models only.** `OPENROUTER_MODEL` must end in `:free` (or be `openrouter/free`); the paid model has the same name without the suffix, so anything else is replaced by the default with a warning unless `OPENROUTER_ALLOW_PAID=1`. A response that reports a cost stops all requests for a day.
- **The default model** is `google/gemma-4-26b-a4b-it:free`, picked from the free text models by their published latency and availability. The Inkling free endpoints are for agentic harnesses only, and Nemotron 3 Super thinks by default and would spend its output limit doing so.
- **Errors** follow OpenRouter's documented codes: 429 backs off for the time given, 401/403 and 400/402/404 stop requests for hours (they would only repeat), 502 is treated as brief. Every attempt counts against `OPENROUTER_DAILY_LIMIT`.

Optionally give an account interests, one per line, in `data-dir/interests/<account>.txt` (the first account is `default`). About half its queries are then about those, so its searching has a subject. Free endpoints log what they receive and may train on it, so keep anything personal out of that file. Whatever an account has already searched is never offered again, from any source.

## A second account, with its own hands

Every account types and moves with its own profile (`src/behavior.py`), so two accounts do not look like one operator. The account that was already running keeps the original measurements. Any other account gets a **provisional** profile, stable and different from every other account's, until you record its owner's real one. The log says which kind is in use.

**1. Record the owner's typing and mouse speed**, on the machine and with the hands of the person the account belongs to:

```
python src/recordpress.py     # type normally for a few minutes in the Edge window, then press Enter
python src/fitts_law.py       # 18 quick clicks; note "MT = a + b * ID"
python src/make_behavior_profile.py second --keys keypress_times.txt --fitts 0.43 0.16
python src/make_behavior_profile.py --show second
```

Copy the resulting `data-dir/behavior/second.json` to the same place in the NAS's `data-dir`.

**2. Sign the account in** through the container, the same way as the first (type the password yourself, it never goes through the bot):

```
REWARDS_ACCOUNTS=second docker compose run --rm --service-ports signin
```

**3. Start its schedulers.** They are behind a compose profile, so a plain `docker compose up -d` leaves them off:

```
docker compose --profile second up -d scheduler-second search-scheduler-second
```

They run one at a time with the first account (the run lock makes an overlap wait), by default at 15:00 instead of 09:00 (`SECOND_ANCHOR_HOUR`) and on 3 search runs a day (`SECOND_SEARCH_RUNS_PER_DAY`). A run for a different account than the one that just finished also waits out a 15 minute cooldown (`REWARDS_ACCOUNT_COOLDOWN_MINUTES`, 0 turns it off), so the two are never used back to back.

None of this hides that both accounts share a connection and a machine. It only keeps their activity from overlapping or touching. Two household members on one connection is ordinary; the bot's own patterns are the part that can link them.

A plain sign-out pauses only the account it happened on (`python src/safety.py clear second`). A human check or a restriction notice still pauses every account.

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

