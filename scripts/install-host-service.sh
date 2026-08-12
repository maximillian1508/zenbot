#!/usr/bin/env bash
# Install zen-agent-bot as a host systemd service (uv), wire Traefik, stop Docker.
# Run on the HOST (SSH), not inside a container:
#   sudo /home/maxi/apps/zen-agent-bot/scripts/install-host-service.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
UNIT_SRC="$ROOT/deploy/systemd/zen-agent-bot.service"
TRAEFIK_DIR="${TRAEFIK_DIR:-/srv/apps/traefik}"
COMPOSE_DIR="${ZENBOT_COMPOSE_DIR:-/srv/apps/zen-agent-bot}"
ENV_FILE="$ROOT/.env"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run as root: sudo $0" >&2
  exit 1
fi

if [[ -f /.dockerenv ]]; then
  echo "ERROR: run this on the zenbook host, not inside the container." >&2
  exit 1
fi

if [[ ! -x /home/maxi/.local/bin/uv ]]; then
  echo "ERROR: /home/maxi/.local/bin/uv missing — install uv for user maxi first." >&2
  exit 1
fi

if [[ ! -f "$ENV_FILE" ]]; then
  echo "ERROR: missing $ENV_FILE" >&2
  exit 1
fi

# Traefik (Docker) reaches the host via host.docker.internal (= bridge IP),
# NOT 127.0.0.1 — so admin must listen on all interfaces (or the bridge).
# Access is still Tailscale-only via Traefik :443; set ADMIN_PASSWORD.
if grep -q '^ADMIN_LISTEN=' "$ENV_FILE"; then
  sed -i 's|^ADMIN_LISTEN=.*|ADMIN_LISTEN=0.0.0.0:8787|' "$ENV_FILE"
else
  echo 'ADMIN_LISTEN=0.0.0.0:8787' >>"$ENV_FILE"
fi
if grep -q '^ZEN_AGENT_DATA_DIR=' "$ENV_FILE"; then
  sed -i 's|^ZEN_AGENT_DATA_DIR=.*|ZEN_AGENT_DATA_DIR=/home/maxi/apps/zen-agent-bot/data|' "$ENV_FILE"
else
  echo 'ZEN_AGENT_DATA_DIR=/home/maxi/apps/zen-agent-bot/data' >>"$ENV_FILE"
fi

mkdir -p "$ROOT/data/logs"
# Docker often left root-owned .venv / __pycache__ — uv sync runs as maxi
chown -R maxi:maxi "$ROOT" || true
chmod -R u+rwX "$ROOT/.venv" 2>/dev/null || true
# Docker agent also wrote Cursor chat DBs as root under ~/.cursor → sqlite
# "attempt to write a readonly database" when host agent --resume's them.
if [[ -d /home/maxi/.cursor ]]; then
  find /home/maxi/.cursor -user root -exec chown maxi:maxi {} + 2>/dev/null || true
fi

echo "==> Sync deps with uv"
sudo -u maxi bash -lc "cd '$ROOT' && /home/maxi/.local/bin/uv sync"

echo "==> Install systemd unit"
install -m 0644 "$UNIT_SRC" /etc/systemd/system/zen-agent-bot.service
systemctl daemon-reload

echo "==> Traefik file provider + host route"
mkdir -p "$TRAEFIK_DIR/dynamic"
install -m 0644 "$ROOT/deploy/traefik/zen-agent-bot.yml" \
  "$TRAEFIK_DIR/dynamic/zen-agent-bot.yml"

COMPOSE="$TRAEFIK_DIR/docker-compose.yml"
if [[ -f "$COMPOSE" ]] && ! grep -q 'providers.file.directory' "$COMPOSE"; then
  python3 - "$COMPOSE" <<'PY'
from pathlib import Path
import sys
path = Path(sys.argv[1])
text = path.read_text()
needle = "      - --providers.docker.network=proxy\n"
insert = (
    needle
    + "      - --providers.file.directory=/etc/traefik/dynamic\n"
    + "      - --providers.file.watch=true\n"
)
if "providers.file.directory" in text:
    print("Traefik file provider already configured")
    raise SystemExit(0)
if needle not in text:
    raise SystemExit(f"Could not patch {path}: docker.network line missing")
text = text.replace(needle, insert, 1)
# volumes
vol = "      - ./data/acme.json:/data/acme.json\n"
vol_ins = vol + "      - ./dynamic:/etc/traefik/dynamic:ro\n"
if "./dynamic:/etc/traefik/dynamic" not in text:
    if vol not in text:
        raise SystemExit(f"Could not patch {path}: acme volume missing")
    text = text.replace(vol, vol_ins, 1)
# extra_hosts for host.docker.internal
if "host.docker.internal" not in text:
    marker = "    networks:\n      - proxy\n"
    extra = (
        "    extra_hosts:\n"
        "      - \"host.docker.internal:host-gateway\"\n"
        + marker
    )
    if marker not in text:
        raise SystemExit(f"Could not patch {path}: networks block missing")
    text = text.replace(marker, extra, 1)
path.write_text(text)
print(f"Patched {path}")
PY
  echo "==> Recreate Traefik to load file provider"
  (cd "$TRAEFIK_DIR" && docker compose up -d --force-recreate)
else
  echo "Traefik file provider present (or compose missing) — copying route only"
  # still recreate traefik if dynamic dir was empty before? file watch should pick up
fi

echo "==> Stop Docker zen-agent-bot (if running)"
if [[ -f "$COMPOSE_DIR/docker-compose.yml" ]]; then
  (cd "$COMPOSE_DIR" && docker compose down) || true
fi

# Retire docker rebuild watcher → host restart watcher
if systemctl list-unit-files zenbot-rebuild.path &>/dev/null; then
  systemctl disable --now zenbot-rebuild.path 2>/dev/null || true
fi
install -m 0644 "$ROOT/deploy/systemd/zenbot-rebuild.service" /etc/systemd/system/zenbot-rebuild.service
install -m 0644 "$ROOT/deploy/systemd/zenbot-rebuild.path" /etc/systemd/system/zenbot-rebuild.path
systemctl daemon-reload
systemctl enable --now zenbot-rebuild.path

echo "==> Enable + start host service"
systemctl enable --now zen-agent-bot.service
sleep 2
systemctl --no-pager --full status zen-agent-bot.service || true

echo "==> Health check"
ok=0
for _ in $(seq 1 30); do
  if curl -sf http://127.0.0.1:8787/health >/dev/null; then
    echo "OK: http://127.0.0.1:8787/health"
    ok=1
    break
  fi
  sleep 1
done
if [[ "$ok" -ne 1 ]]; then
  echo "WARN: health not ready — check: journalctl -u zen-agent-bot -e" >&2
  exit 1
fi

echo
echo "Done. Runtime: systemd + uv (not Docker)."
echo "  logs:    journalctl -u zen-agent-bot -f"
echo "  restart: sudo systemctl restart zen-agent-bot"
echo "  /rebuild still uses data/REQUEST_REBUILD → zenbot-rebuild.path"
echo "  admin:   https://agents.maximillianleonard.dev"
