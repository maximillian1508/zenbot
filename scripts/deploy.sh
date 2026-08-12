#!/usr/bin/env bash
# Host-side restart for zen-agent-bot. Triggered by systemd path unit when
# data/REQUEST_REBUILD appears, or run manually.
# (Host systemd + uv — not Docker.)
set -euo pipefail

DATA_DIR="${ZENBOT_DATA_DIR:-/home/maxi/apps/zen-agent-bot/data}"
FLAG="${DATA_DIR}/REQUEST_REBUILD"
LOG_DIR="${DATA_DIR}/logs"
UNIT="${ZENBOT_SYSTEMD_UNIT:-zen-agent-bot.service}"
mkdir -p "$LOG_DIR" 2>/dev/null || true
LOG="${LOG_DIR}/rebuild.log"
if [[ ! -w "$LOG_DIR" ]]; then
  LOG="${TMPDIR:-/tmp}/zenbot-rebuild.log"
  echo "WARN: $LOG_DIR not writable — logging to $LOG" >&2
fi

DELAY_SEC="${ZENBOT_REBUILD_DELAY_SEC:-15}"

{
  echo "==== $(date -Is) restart start ===="
  if [[ -f "$FLAG" ]]; then
    echo "Flag contents:"
    cat "$FLAG" || true
    echo
  fi

  if [[ "$DELAY_SEC" -gt 0 ]]; then
    echo "Sleeping ${DELAY_SEC}s before restart…"
    sleep "$DELAY_SEC"
  fi

  echo "==> systemctl restart ${UNIT}"
  systemctl restart "$UNIT"

  rm -f "$FLAG"

  echo "==> Waiting for health…"
  ok=0
  for _ in $(seq 1 45); do
    if curl -sf http://127.0.0.1:8787/health >/dev/null; then
      echo "OK: /health"
      systemctl is-active "$UNIT" || true
      ok=1
      break
    fi
    sleep 2
  done

  if [[ "$ok" -ne 1 ]]; then
    echo "WARN: /health not ready after ~90s"
    journalctl -u "$UNIT" -n 80 --no-pager || true
    exit 1
  fi
  echo "==== $(date -Is) restart done ===="
} 2>&1 | tee -a "$LOG"
