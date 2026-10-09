#!/bin/sh
# Runs on the NAS (sent there by scripts/deploy.sh). Builds the image, and only if nothing is
# mid-run, recreates every rewards-farmer container that is running,
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
	n=$($D exec "$c" sh -c 'ps -eo args | grep -c "[m]sedge .*--user-data-dir"' 2>/dev/null)

	# If it could not be read, treat it as busy: better to refuse than to kill a live run.
	case "$n" in
		''|*[!0-9]*) n=1 ;;
	esac

	busy=$((busy + n))
done

if [ "$busy" -ne 0 ]; then
	echo "NOT RESTARTED: $busy browser(s) are mid-run. Run this again between runs."
	exit 3
fi

# Which compose services those containers are, from the label compose puts on them.
plain=""
legacy=""
for c in $running; do
	service=$($D inspect -f '{{index .Config.Labels "com.docker.compose.service"}}' "$c")
	case "$service" in
		# The containers `home` replaced: one daily and one search container per account, and the VPN container
		# (every account now runs on the home connection, whose address is the cleaner one). Removed once, here.
		vpn|scheduler|search-scheduler|scheduler-second|search-scheduler-second|scheduler-third|search-scheduler-third)
			legacy="$legacy $c"
			case "$plain" in *" home"*) ;; *) plain="$plain home" ;; esac
			;;
		*) plain="$plain $service" ;;
	esac
done

if [ -n "$legacy" ]; then
	echo "removing the old per-account containers:$legacy"
	$D stop $legacy >/dev/null 2>&1
	$D rm $legacy >/dev/null 2>&1
fi

if [ -n "$plain" ]; then
	echo "recreating:$plain"
	$D compose --profile second --profile third up -d --force-recreate --no-deps $plain 2>&1 | tail -8
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
