#!/bin/sh
# Runs a command with Edge given a virtual display to draw on.
#
# Used instead of xvfb-run, which hung here forever waiting for Xvfb to report
# that it was ready (before the command ever started) when launched from a
# non-interactive container. Starting Xvfb directly and polling for its socket
# has no such handshake to miss.
set -e

DISPLAY_NUM=99

# A container that is restarted rather than recreated keeps /tmp, and Xvfb
# refuses to start over the previous run's lock.
rm -f "/tmp/.X${DISPLAY_NUM}-lock" "/tmp/.X11-unix/X${DISPLAY_NUM}"

Xvfb ":${DISPLAY_NUM}" -screen 0 1920x1080x24 -nolisten tcp >/dev/null 2>&1 &

tries=0
while [ ! -e "/tmp/.X11-unix/X${DISPLAY_NUM}" ]; do
	tries=$((tries + 1))

	if [ "$tries" -gt 100 ]; then
		echo "with-xvfb: Xvfb did not come up on :${DISPLAY_NUM}" >&2
		exit 1
	fi

	sleep 0.1
done

export DISPLAY=":${DISPLAY_NUM}"

exec "$@"
