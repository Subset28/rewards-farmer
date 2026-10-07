#!/bin/bash
# Deploy the committed code to the NAS: copy it, build, and restart every running container
# (the VPN one too) if nothing is mid-run, then verify each is on the new image.
#
#   scripts/deploy.sh
#
# It deploys what is committed (git archive HEAD), never the working tree, and refuses if there
# are uncommitted changes to the code, so what runs on the NAS is always something in git.
# Exit status: 0 done, 3 a browser was mid-run (nothing restarted; run it again between runs),
# 4 a container is still on the old image.

set -e
cd "$(dirname "$0")/.."

if ! git diff --quiet HEAD -- src docker-compose.yml docker-compose.vpn.yml Dockerfile with-xvfb.sh; then
	echo "Uncommitted changes in the code. Commit them first; the NAS gets what is in git."
	exit 1
fi

# The NAS clock is a few milliseconds behind this machine's, which makes tar complain on every file.
git archive HEAD . | ssh synology 'cd /volume1/docker/rewards-farmer && tar -xf -' 2> >(grep -v 'in the future' >&2)
sed 's/\r$//' scripts/deploy_remote.sh | ssh synology 'sh -s'
