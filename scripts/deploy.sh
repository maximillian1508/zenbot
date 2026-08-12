#!/usr/bin/env bash
# Host-side deploy for zen-agent-bot. Triggered by systemd path unit when
# data/REQUEST_REBUILD appears, or run manually.
set -euo pipefail

COMPOSE_DIR="${ZENBOT_COMPOSE_DIR:-/srv/apps/zen-agent-bot}"
DATA_DIR="${ZENBOT_DATA_DIR:-/home/maxi/apps/zen-agent-bot/data}"
FLAG="${DATA_DIR}/REQUEST_REBUILD"
LOG_DIR="${DATA_DIR}/logs"
mkdir -p "$LOG_DIR" 2>/dev/null || true
LOG="${LOG_DIR}/rebuild.log"
if [[ ! -w "$LOG_DIR" ]]; then
  LOG="${TMPDIR:-/tmp}/zenbot-rebuild.log"
  echo "WARN: $LOG_DIR not writable — logging to $LOG" >&2
fi

# Delay so Discord replies / agent final messages can flush before recreate.
DELAY_SEC="${ZENBOT_REBUILD_DELAY_SEC:-15}"

{
  echo "==== $(date -Is) rebuild start ===="
  if [[ -f "$FLAG" ]]; then
    echo "Flag contents:"
    cat "$FLAG" || true
    echo
  fi

  if [[ "$DELAY_SEC" -gt 0 ]]; then
    echo "Sleeping ${DELAY_SEC}s before recreate…"
    sleep "$DELAY_SEC"
  fi

  cd "$COMPOSE_DIR"
  echo "==> Building…"
  docker compose build

  echo "==> Recreating…"
  docker compose up -d --force-recreate

  # Clear flag after recreate starts so path unit can re-arm.
  rm -f "$FLAG"

  echo "==> Waiting for health…"
  ok=0
  for _ in $(seq 1 45); do
    if curl -sf http://127.0.0.1:8787/health >/dev/null; then
      echo "OK: /health"
      docker compose ps
      ok=1
      break
    fi
    sleep 2
  done

  if [[ "$ok" -ne 1 ]]; then
    echo "WARN: /health not ready after ~90s"
    docker compose logs --tail=80 zen-agent-bot || true
    exit 1
  fi
  echo "==== $(date -Is) rebuild done ===="
} 2>&1 | tee -a "$LOG"
