FROM python:3.12-slim-bookworm

RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
COPY src ./src
COPY agents ./agents
COPY scripts/docker-entrypoint.sh /app/scripts/docker-entrypoint.sh

RUN uv sync --frozen --no-dev \
    && chmod +x /app/scripts/docker-entrypoint.sh

ENV HOME=/home/maxi
ENV PYTHONUNBUFFERED=1
EXPOSE 8787

ENTRYPOINT ["/app/scripts/docker-entrypoint.sh"]
