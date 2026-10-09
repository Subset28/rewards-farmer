#!/bin/bash
# Talk to the running app's control interface (src/control_api.py) through the NAS. The token is read on the NAS
# and never leaves it.
#
#   scripts/ctl.sh status                 every account, the brake, browsers running, today's runs, health
#   scripts/ctl.sh runs [days]            the journal of runs
#   scripts/ctl.sh tasks [days]           per account and task: how often it worked, and which are failing now
#   scripts/ctl.sh settings               what is on and how it is set (no secrets)
#   scripts/ctl.sh snapshots [name]       what the page offered when a task failed (list, or one)
#   scripts/ctl.sh inspect [name]         the saved Rewards pages (list, or one)
#   scripts/ctl.sh logs                   which logs there are
#   scripts/ctl.sh logs scheduler.log [n] the last n lines of one
#   scripts/ctl.sh pause [account] [why]  stop runs (all accounts when none is named)
#   scripts/ctl.sh resume [account]       start them again

set -e

call() {
	local method="$1" path="$2" body="${3:-}"
	ssh synology "T=\$(grep '^CONTROL_TOKEN=' /volume1/docker/rewards-farmer/.env | cut -d= -f2); curl -s -m 20 -X $method -H \"Authorization: Bearer \$T\" -H 'Content-Type: application/json' ${body:+-d '$body'} 'http://127.0.0.1:8787$path'" 2> >(grep -v '^\*\*' >&2)
}

case "${1:-status}" in
	status) call GET /status ;;
	runs) call GET "/runs?days=${2:-1}" ;;
	tasks) call GET "/tasks?days=${2:-14}" ;;
	settings) call GET /settings ;;
	snapshots) if [ -n "$2" ]; then call GET "/snapshots/$2"; else call GET /snapshots; fi ;;
	inspect) if [ -n "$2" ]; then call GET "/inspect/$2"; else call GET /inspect; fi ;;
	logs) if [ -n "$2" ]; then call GET "/logs/$2?lines=${3:-60}"; else call GET /logs; fi ;;
	pause)
		account="${2:-}"; why="${3:-paused by hand}"
		[ "$account" = "all" ] && account=""
		call POST /pause "{\"account\": \"$account\", \"reason\": \"$why\"}" ;;
	resume)
		account="${2:-}"; [ "$account" = "all" ] && account=""
		call POST /resume "{\"account\": \"$account\"}" ;;
	*) echo "usage: scripts/ctl.sh status | runs [days] | tasks [days] | settings | snapshots [name] | inspect [name] | logs [name [lines]] | pause [account] [why] | resume [account]"; exit 2 ;;
esac
echo
