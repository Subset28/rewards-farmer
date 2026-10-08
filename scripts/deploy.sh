#!/bin/bash
# Deploy committed code to the NAS: copy it, build, restart every running container (the VPN
# one too) if nothing is mid-run, verify each is on the new image, and tag what is now live.
#
#   scripts/deploy.sh              deploy HEAD
#   scripts/deploy.sh <ref>        deploy any commit or tag, e.g. to roll back:
#                                  scripts/deploy.sh deploy-20261008-1830
#
# It deploys what is committed (git archive), never the working tree, and refuses if the code
# has uncommitted changes, so what runs on the NAS is always something in git. After a good
# deploy it tags the commit deploy-YYYYMMDD-HHMM and moves the tag "deployed" to it, so
# `git log deployed..main` is what has not gone live yet.
#
# Exit status: 0 done, 3 a browser was mid-run (nothing restarted; run it again between
# runs), 4 a container is still on the old image.

set -e
cd "$(dirname "$0")/.."

ref="${1:-HEAD}"

if [ "$ref" = "HEAD" ] && ! git diff --quiet HEAD -- src docker-compose.yml docker-compose.vpn.yml docker-compose.override.yml Dockerfile with-xvfb.sh; then
	echo "Uncommitted changes in the code. Commit them first; the NAS gets what is in git."
	exit 1
fi

sha=$(git rev-parse --verify "$ref^{commit}")
echo "deploying $(git log -1 --format='%h %s' "$sha")"

# The NAS clock is a few milliseconds behind this machine's, which makes tar complain on every file.
git archive "$sha" . | ssh synology 'cd /volume1/docker/rewards-farmer && tar -xf -' 2> >(grep -v 'in the future' >&2)
sed 's/$//' scripts/deploy_remote.sh | ssh synology 'sh -s'

tag="deploy-$(date +%Y%m%d-%H%M)"
git tag "$tag" "$sha"
git tag -f deployed "$sha" > /dev/null
git push -q origin "$tag" && git push -q -f origin deployed && echo "tagged $tag (and moved 'deployed')"
