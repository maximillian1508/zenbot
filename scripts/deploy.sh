#!/usr/bin/env bash
# Host-side restart for zen-agent-bot. Triggered when data/REQUEST_REBUILD
# appears (systemd path unit on Linux, launchd WatchPaths on macOS), or run
# manually. Host init + uv — not Docker.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DATA_DIR="${ZENBOT_DATA_DIR:-$ROOT/data}"
FLAG="${DATA_DIR}/REQUEST_REBUILD"
LOG_DIR="${DATA_DIR}/logs"
UNIT="${ZENBOT_SYSTEMD_UNIT:-zen-agent-bot.service}"
LABEL="${ZENBOT_LAUNCHD_LABEL:-dev.maximillianleonard.zen-agent-bot}"
HEALTH_URL="${ZENBOT_HEALTH_URL:-http://127.0.0.1:8787/health}"
mkdir -p "$LOG_DIR" 2>/dev/null || true
LOG="${LOG_DIR}/rebuild.log"
if [[ ! -w "$LOG_DIR" ]]; then
  LOG="${TMPDIR:-/tmp}/zenbot-rebuild.log"
  echo "WARN: $LOG_DIR not writable — logging to $LOG" >&2
fi

DELAY_SEC="${ZENBOT_REBUILD_DELAY_SEC:-15}"

# systemd | launchd — override with ZENBOT_INIT.
detect_init() {
  if [[ -n "${ZENBOT_INIT:-}" ]]; then
    echo "$ZENBOT_INIT"
  elif [[ "$(uname -s)" == "Darwin" ]]; then
    echo "launchd"
  elif command -v systemctl >/dev/null 2>&1; then
    echo "systemd"
  else
    echo "unknown"
  fi
}
INIT="$(detect_init)"

restart_service() {
  if [[ "${ZENBOT_DRY_RUN:-0}" == "1" ]]; then
    echo "DRY RUN: would restart via $INIT"
    return 0
  fi
  case "$INIT" in
    systemd) systemctl restart "$UNIT" ;;
    launchd) launchctl kickstart -k "gui/$(id -u)/$LABEL" ;;
    *) echo "ERROR: no supported init system (set ZENBOT_INIT)" >&2; return 1 ;;
  esac
}

dump_logs() {
  case "$INIT" in
    systemd) journalctl -u "$UNIT" -n 80 --no-pager || true ;;
    launchd)
      tail -n 80 "$LOG_DIR/launchd.err.log" 2>/dev/null || true
      launchctl print "gui/$(id -u)/$LABEL" 2>/dev/null | head -20 || true
      ;;
  esac
}

report_active() {
  case "$INIT" in
    systemd) systemctl is-active "$UNIT" || true ;;
    launchd) launchctl print "gui/$(id -u)/$LABEL" 2>/dev/null | awk '/state =/{print; exit}' || true ;;
  esac
}

{
  echo "==== $(date -Is) restart start (init=$INIT) ===="
  if [[ -f "$FLAG" ]]; then
    echo "Flag contents:"
    cat "$FLAG" || true
    echo
  elif [[ "${ZENBOT_FORCE_RESTART:-0}" != "1" ]]; then
    # launchd WatchPaths also fires on the flag's deletion below; without this
    # guard that second event would restart the gateway a second time.
    echo "No $FLAG — nothing to do (set ZENBOT_FORCE_RESTART=1 to override)."
    echo "==== $(date -Is) restart skipped ===="
    exit 0
  fi

  if [[ "$DELAY_SEC" -gt 0 ]]; then
    echo "Sleeping ${DELAY_SEC}s before restart…"
    sleep "$DELAY_SEC"
  fi

  echo "==> restart ($INIT)"
  restart_service

  rm -f "$FLAG"

  echo "==> Waiting for health…"
  ok=0
  for _ in $(seq 1 45); do
    if curl -sf "$HEALTH_URL" >/dev/null; then
      echo "OK: $HEALTH_URL"
      report_active
      ok=1
      break
    fi
    sleep 2
  done

  if [[ "$ok" -ne 1 ]]; then
    echo "WARN: health not ready after ~90s"
    dump_logs
    exit 1
  fi
  echo "==== $(date -Is) restart done ===="
} 2>&1 | tee -a "$LOG"
