#!/usr/bin/env bash
# Install zen-agent-bot as a macOS LaunchAgent (uv) — the launchd counterpart
# of scripts/install-host-service.sh.
#
#   ~/apps/zen-agent-bot/scripts/install-launchd-service.sh
#   ~/apps/zen-agent-bot/scripts/install-launchd-service.sh --uninstall
#
# Runs as your user (NOT root): the gateway needs your login context —
# ~/.cursor and ~/.claude credentials, ~/.ssh keys, user PATH.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LABEL="${ZENBOT_LABEL:-dev.maximillianleonard.zen-agent-bot}"
REBUILD_LABEL="${ZENBOT_REBUILD_LABEL:-dev.maximillianleonard.zenbot-rebuild}"
AGENTS_DIR="$HOME/Library/LaunchAgents"
PLIST="$AGENTS_DIR/$LABEL.plist"
REBUILD_PLIST="$AGENTS_DIR/$REBUILD_LABEL.plist"
DOMAIN="gui/$(id -u)"
HEALTH_URL="${ZENBOT_HEALTH_URL:-http://127.0.0.1:8787/health}"

bootout() {  # label -> ignore "not loaded"
  launchctl bootout "$DOMAIN/$1" 2>/dev/null || true
}

if [[ "${1:-}" == "--uninstall" ]]; then
  echo "==> Unloading agents"
  bootout "$REBUILD_LABEL"
  bootout "$LABEL"
  rm -f "$PLIST" "$REBUILD_PLIST"
  echo "Done. Removed $PLIST and $REBUILD_PLIST"
  exit 0
fi

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "ERROR: launchd is macOS-only. On Linux use scripts/install-host-service.sh." >&2
  exit 1
fi

if [[ "$(id -u)" -eq 0 ]]; then
  echo "ERROR: run as your own user, not root (LaunchAgents are per-user)." >&2
  exit 1
fi

UV_BIN="${ZENBOT_UV_BIN:-$(command -v uv || true)}"
if [[ -z "$UV_BIN" ]]; then
  for candidate in "$HOME/.local/bin/uv" /opt/homebrew/bin/uv /usr/local/bin/uv; do
    [[ -x "$candidate" ]] && UV_BIN="$candidate" && break
  done
fi
if [[ -z "$UV_BIN" || ! -x "$UV_BIN" ]]; then
  echo "ERROR: uv not found. Install it (brew install uv) or set ZENBOT_UV_BIN." >&2
  exit 1
fi

ENV_FILE="$ROOT/.env"
if [[ ! -f "$ENV_FILE" ]]; then
  echo "ERROR: missing $ENV_FILE (copy .env.example and fill in tokens)." >&2
  exit 1
fi

# On a Mac there is no Traefik-in-Docker needing host.docker.internal, so keep
# the admin UI loopback-only unless the user already chose otherwise.
if ! grep -q '^ADMIN_LISTEN=' "$ENV_FILE"; then
  echo 'ADMIN_LISTEN=127.0.0.1:8787' >>"$ENV_FILE"
  echo "==> Set ADMIN_LISTEN=127.0.0.1:8787 in .env"
fi
if ! grep -q '^ZEN_AGENT_DATA_DIR=' "$ENV_FILE"; then
  echo "ZEN_AGENT_DATA_DIR=$ROOT/data" >>"$ENV_FILE"
fi

mkdir -p "$ROOT/data/logs" "$AGENTS_DIR"

echo "==> Sync deps with uv"
(cd "$ROOT" && "$UV_BIN" sync)

# Homebrew differs by arch; include both plus user bin.
PATH_VALUE="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
WORKSPACE="${ZENBOT_WORKSPACE:-$HOME}"

render() {  # src dst
  sed -e "s|@ROOT@|$ROOT|g" \
      -e "s|@HOME@|$HOME|g" \
      -e "s|@LABEL@|$LABEL|g" \
      -e "s|@REBUILD_LABEL@|$REBUILD_LABEL|g" \
      -e "s|@PATH_VALUE@|$PATH_VALUE|g" \
      -e "s|@WORKSPACE@|$WORKSPACE|g" \
      -e "s|@UV_BIN@|$UV_BIN|g" \
      "$1" >"$2"
  plutil -lint "$2" >/dev/null
  chmod 0644 "$2"
}

echo "==> Install LaunchAgents"
render "$ROOT/deploy/launchd/dev.maximillianleonard.zen-agent-bot.plist" "$PLIST"
render "$ROOT/deploy/launchd/dev.maximillianleonard.zenbot-rebuild.plist" "$REBUILD_PLIST"

# Replace any previous instance (bootstrap fails if already loaded).
bootout "$LABEL"
bootout "$REBUILD_LABEL"
launchctl bootstrap "$DOMAIN" "$PLIST"
launchctl bootstrap "$DOMAIN" "$REBUILD_PLIST"
launchctl enable "$DOMAIN/$LABEL"

echo "==> Health check"
ok=0
for _ in $(seq 1 30); do
  if curl -sf "$HEALTH_URL" >/dev/null; then
    echo "OK: $HEALTH_URL"
    ok=1
    break
  fi
  sleep 2
done
if [[ "$ok" -ne 1 ]]; then
  echo "WARN: health not ready — check $ROOT/data/logs/launchd.err.log" >&2
  launchctl print "$DOMAIN/$LABEL" 2>/dev/null | head -20 || true
  exit 1
fi

cat <<EOF

Done. Runtime: launchd (LaunchAgent) + uv.
  status:  launchctl print $DOMAIN/$LABEL | head -20
  logs:    tail -f $ROOT/data/logs/launchd.err.log
  restart: launchctl kickstart -k $DOMAIN/$LABEL
  stop:    launchctl bootout $DOMAIN/$LABEL
  remove:  $0 --uninstall
  /rebuild still works via data/REQUEST_REBUILD (WatchPaths -> deploy.sh)

Mac-as-server notes:
  * A LaunchAgent runs only while you are logged in. For unattended use enable
    automatic login, or convert to a LaunchDaemon (loses user credentials).
  * Stop the Mac sleeping or the gateway goes offline:  sudo pmset -a sleep 0
EOF
