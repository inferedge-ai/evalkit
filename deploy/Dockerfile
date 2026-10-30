# Stage 1: build the UI
FROM node:25-slim AS ui
WORKDIR /build/ui
COPY ui/package.json ui/package-lock.json ./
RUN npm ci
COPY ui/ ./
COPY docs/user/ /build/docs/user/
COPY src/evalkit/contracts/notes.json /build/src/evalkit/contracts/notes.json
RUN npx tsc -b && npx vite build

# Stage 2: the server image
FROM python:3.13-slim
# The base image's pip vendors old certifi, urllib3, msgpack and setuptools; uv replaces it.
RUN python -m pip uninstall -y pip
COPY --from=ghcr.io/astral-sh/uv:0.13.5 /uv /uvx /usr/local/bin/

WORKDIR /app
# Dependency layer first: cached until the lockfile changes.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --extra server --no-dev --no-install-project

# Content layers: skills, contracts. Must precede the project sync —
# hatchling force-includes them into the wheel and fails if they are missing.
COPY hatch_build.py ./
COPY skills/ skills/
COPY contracts/ contracts/
COPY docs/user/ docs/user/

# Project layer. evalkit installs as a regular (not editable) package, the
# way pip installs it: the web UI and cost_map.json must be in place first,
# because the wheel bundles them.
COPY README.md ./
COPY src/ src/
# cost_map.json is not checked in; fetch it at build time so the image never fetches at runtime.
COPY scripts/fetch_cost_map.py scripts/fetch_cost_map.py
RUN PYTHONPATH=src uv run --no-sync python scripts/fetch_cost_map.py
COPY --from=ui /build/ui/dist ui/dist
RUN uv sync --frozen --extra server --no-dev --no-editable

ENV PATH="/app/.venv/bin:$PATH"
# OpenSSL in `cryptography` dies with SIGILL (exit 132) on Apple M4 under Docker when it
# probes ARM CPU features; x86 ignores this.
ENV OPENSSL_armcap=0
EXPOSE 8020
CMD ["evalkit", "serve", "--config", "/etc/evalkit/config.yaml", "--host", "0.0.0.0", "--port", "8020"]
