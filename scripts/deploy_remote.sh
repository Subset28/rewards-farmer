#!/bin/sh
# Runs on the NAS (sent there by scripts/deploy.sh). Builds the image, and only if nothing is
# mid-run, recreates every rewards-farmer container that is running, the VPN one included,
# then checks that each of them really is on the new image.
#
# Plain POSIX sh: the NAS shell has no bash extras.

cd /volume1/docker/rewards-farmer || exit 2
D=/usr/local/bin/docker

sed -i 's/\r$//' src/*.py with-xvfb.sh
$D compose build rewards-farmer 2>&1 | tail -2

running=$($D ps --format '{{.Names}}' | grep '^rewards-farmer-' | grep -v signin)

if [ -z "$running" ]; then
	echo "nothing is running, so nothing to restart"
	exit 0
fi

busy=0
for c in $running; do
	n=$($D exec "$c" sh -c 'ps -eo args | grep -c "[m]sedge .*--user-data-dir"' 2>/dev/null || echo 0)
	busy=$((busy + n))
done

if [ "$busy" -ne 0 ]; then
	echo "NOT RESTARTED: $busy browser(s) are mid-run. Run this again between runs."
	exit 3
fi

# Which compose services those containers are, from the label compose puts on them.
plain=""
vpn=""
for c in $running; do
	service=$($D inspect -f '{{index .Config.Labels "com.docker.compose.service"}}' "$c")
	case "$service" in
		vpn) vpn="vpn" ;;
		*) plain="$plain $service" ;;
	esac
done

if [ -n "$plain" ]; then
	echo "recreating:$plain"
	$D compose --profile second --profile third up -d --force-recreate --no-deps $plain 2>&1 | tail -8
fi

if [ -n "$vpn" ]; then
	echo "recreating: vpn (the VPN container, which the plain command does not touch)"
	$D compose -f docker-compose.yml -f docker-compose.vpn.yml up -d --force-recreate --no-deps vpn 2>&1 | tail -4
fi

sleep 20

new=$($D image inspect rewards-farmer:runtime -f '{{.Id}}')
stale=0
for c in $($D ps --format '{{.Names}}' | grep '^rewards-farmer-' | grep -v signin); do
	if [ "$($D inspect -f '{{.Image}}' "$c")" = "$new" ]; then
		echo "OK    $c"
	else
		echo "STALE $c"
		stale=$((stale + 1))
	fi
done

if [ "$stale" -ne 0 ]; then
	echo "$stale container(s) are still on the OLD image"
	exit 4
fi

echo "all containers are on the new image"
