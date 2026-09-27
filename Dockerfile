# syntax=docker/dockerfile:1.7

# Runs the bot without installing Edge, a driver or Python on the host.
#
# Two stages, because only one of them ever runs unattended: `runtime` is
# what scheduler/rewards-farmer actually use, every day, and carries only
# what main.py reaches (selenium, numpy, dotenv, requests, Edge, its driver).
# `signin` extends it with a GUI/VNC stack (Xvfb, x11vnc, noVNC) that exists
# solely for the one-time interactive sign-in and has no business sitting in
# the image that is idle 23+ hours a day on the NAS. pygetwindow, keyboard,
# matplotlib and pygame are dev-only calibration tools and are never
# installed here at all, two of them are Windows-only besides.
#
# `RUN --mount=type=cache` keeps apt/pip downloads out of the image layers
# (so they don't add to on-disk size) while still caching them across
# rebuilds, on the same BuildKit that `docker compose build` already uses.
#
# QUERY_SOURCE defaults to trends here so a container needs no Ollama account
# and no model download. Set it to llm and point OLLAMA_HOST at a reachable
# host to use a model instead.

FROM python:3.12-slim-bookworm AS runtime

ENV DEBIAN_FRONTEND=noninteractive

# Edge, from Microsoft's own repository.
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
	--mount=type=cache,target=/var/lib/apt,sharing=locked \
	apt-get update \
	&& apt-get install -y --no-install-recommends \
		ca-certificates curl gnupg unzip fonts-liberation tzdata

RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
	--mount=type=cache,target=/var/lib/apt,sharing=locked \
	curl -fsSL https://packages.microsoft.com/keys/microsoft.asc \
		| gpg --dearmor -o /usr/share/keyrings/microsoft.gpg \
	&& echo "deb [arch=amd64 signed-by=/usr/share/keyrings/microsoft.gpg] https://packages.microsoft.com/repos/edge stable main" \
		> /etc/apt/sources.list.d/microsoft-edge.list \
	&& apt-get update \
	&& apt-get install -y --no-install-recommends microsoft-edge-stable

# The driver has to match the browser build, so it is pinned to whatever Edge
# the layer above installed rather than to "latest", which drifts apart from it
# between releases.
RUN --mount=type=cache,target=/var/cache/edgedriver \
	EDGE_VERSION="$(microsoft-edge --version | awk '{print $3}')" \
	&& cached="/var/cache/edgedriver/${EDGE_VERSION}.zip" \
	&& [ -f "$cached" ] || curl -fsSL -o "$cached" \
		"https://msedgedriver.microsoft.com/${EDGE_VERSION}/edgedriver_linux64.zip" \
	&& unzip -j "$cached" msedgedriver -d /usr/local/bin \
	&& chmod +x /usr/local/bin/msedgedriver \
	&& msedgedriver --version

WORKDIR /app

# dotenv: main.py imports it unconditionally even though loading a .env file
# is conditional. requests: llm_utils.py needs it under QUERY_SOURCE=llm,
# which docker-compose.yml documents as a supported option even though
# `trends` is the default -- so this can't wait until something reaches it.
RUN --mount=type=cache,target=/root/.cache/pip \
	pip install "selenium>=4.46.0,<5.0.0" "numpy" "python-dotenv" "requests"

COPY src/ ./src/
COPY nouns.txt ./

# Headless because there is no display, and trends because there is no model.
ENV REWARDS_HEADLESS=1 \
	QUERY_SOURCE=trends \
	PYTHONUNBUFFERED=1

# Sign-in lives here, so it has to outlive the container.
VOLUME ["/app/data-dir"]

CMD ["python", "src/main.py"]

# Only src/signin.py reaches anything below this line; a scheduled run never
# does, which is why it is not in `runtime`.
FROM runtime AS signin

# Signing in needs a browser window, and the profile has to be written by the
# container's own Edge: Chromium takes the cookie key from the operating system,
# and on a Windows or macOS host that key is wrapped with DPAPI or the login
# Keychain, neither of which the container can unwrap. Xvfb gives that browser a
# display and noVNC puts it on the user's screen with nothing installed on the
# host.
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
	--mount=type=cache,target=/var/lib/apt,sharing=locked \
	apt-get update \
	&& apt-get install -y --no-install-recommends \
		xvfb x11vnc novnc websockify \
	# Debian ships vnc.html and no index.html, so a bare localhost:6080 is a 404
	# that reads as the feature being broken.
	&& ln -s /usr/share/novnc/vnc.html /usr/share/novnc/index.html
