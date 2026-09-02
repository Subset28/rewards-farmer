# Runs the bot without installing Edge, a driver or Python on the host.
#
# The image carries only what main.py actually reaches: selenium and numpy.
# pygetwindow, keyboard, matplotlib and pygame are used solely by the
# recording and visualisation scripts, which are developer tools rather than
# part of a run, and two of them are Windows-only.
#
# QUERY_SOURCE defaults to trends here so a container needs no Ollama account
# and no model download. Set it to llm and point OLLAMA_HOST at a reachable
# host to use a model instead.

FROM python:3.12-slim-bookworm

ENV DEBIAN_FRONTEND=noninteractive

# Edge, from Microsoft's own repository.
RUN apt-get update \
	&& apt-get install -y --no-install-recommends \
		ca-certificates curl gnupg unzip fonts-liberation \
	&& curl -fsSL https://packages.microsoft.com/keys/microsoft.asc \
		| gpg --dearmor -o /usr/share/keyrings/microsoft.gpg \
	&& echo "deb [arch=amd64 signed-by=/usr/share/keyrings/microsoft.gpg] https://packages.microsoft.com/repos/edge stable main" \
		> /etc/apt/sources.list.d/microsoft-edge.list \
	&& apt-get update \
	&& apt-get install -y --no-install-recommends microsoft-edge-stable \
	&& rm -rf /var/lib/apt/lists/*

# The driver has to match the browser build, so it is pinned to whatever Edge
# the layer above installed rather than to "latest", which drifts apart from it
# between releases.
RUN EDGE_VERSION="$(microsoft-edge --version | awk '{print $3}')" \
	&& curl -fsSL -o /tmp/edgedriver.zip \
		"https://msedgedriver.microsoft.com/${EDGE_VERSION}/edgedriver_linux64.zip" \
	&& unzip -j /tmp/edgedriver.zip msedgedriver -d /usr/local/bin \
	&& chmod +x /usr/local/bin/msedgedriver \
	&& rm /tmp/edgedriver.zip \
	&& msedgedriver --version

WORKDIR /app

RUN pip install --no-cache-dir "selenium>=4.46.0,<5.0.0" "numpy"

COPY src/ ./src/
COPY nouns.txt ./

# Headless because there is no display, and trends because there is no model.
ENV REWARDS_HEADLESS=1 \
	QUERY_SOURCE=trends \
	PYTHONUNBUFFERED=1

# Sign-in lives here, so it has to outlive the container.
VOLUME ["/app/data-dir"]

CMD ["python", "src/main.py"]
