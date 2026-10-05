#!/bin/sh
# Brings this container's own OpenVPN tunnel up behind a kill switch, then runs
# the command. Refuses to start the command if the tunnel does not come up, so
# an account never falls back to the host's real address.
#
#   with-vpn <account> <command> [args...]
set -eu

account="$1"
shift

python /app/src/vpn_config.py up "$account"

exec "$@"
