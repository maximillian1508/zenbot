#!/usr/bin/env bash
set -euo pipefail

export HOME="${HOME:-/home/agent}"
export PATH="${HOME}/.local/bin:${PATH}"
export ZEN_AGENT_CONFIG="${ZEN_AGENT_CONFIG:-/app/data/config.yaml}"

if [[ -z "${AGENT_BIN:-}" ]]; then
  latest="$(find "${HOME}/.local/share/cursor-agent/versions" -mindepth 1 -maxdepth 1 -type d 2>/dev/null | sort -r | head -1)"
  if [[ -n "$latest" && -x "$latest/cursor-agent" ]]; then
    export AGENT_BIN="$latest/cursor-agent"
  else
    export AGENT_BIN="agent"
  fi
fi

exec uv run --no-sync zen-agent-bot
