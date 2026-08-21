#!/usr/bin/env bash
# launchd entrypoint for zen-agent-bot.
# launchd has no EnvironmentFile= (systemd) equivalent, so source .env here,
# then exec uv. Keeps the plist free of secrets.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ -f "$ROOT/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$ROOT/.env"
  set +a
fi

UV_BIN="${ZENBOT_UV_BIN:-}"
if [[ -z "$UV_BIN" || ! -x "$UV_BIN" ]]; then
  UV_BIN="$(command -v uv || true)"
fi
if [[ -z "$UV_BIN" ]]; then
  echo "ERROR: uv not found (set ZENBOT_UV_BIN in the plist)" >&2
  exit 1
fi

exec "$UV_BIN" run --directory "$ROOT" zen-agent-bot
