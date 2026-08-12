#!/usr/bin/env bash
# Install host systemd path watcher for self-rebuild.
# Must run on the HOST (not inside the zen-agent-bot container):
#   sudo /home/maxi/apps/zen-agent-bot/scripts/install-rebuild-watcher.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
UNIT_SRC="$ROOT/deploy/systemd"
DATA_DIR="${ZENBOT_DATA_DIR:-/home/maxi/apps/zen-agent-bot/data}"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run as root: sudo $0" >&2
  exit 1
fi

if [[ -f /.dockerenv ]] || grep -qE '/docker|/lxc' /proc/1/cgroup 2>/dev/null; then
  echo "WARN: looks like a container — install on the zenbook host instead." >&2
fi

mkdir -p "$DATA_DIR/logs"
chown maxi:maxi "$DATA_DIR" "$DATA_DIR/logs" 2>/dev/null || true
chmod 775 "$DATA_DIR/logs" 2>/dev/null || true
install -m 0644 "$UNIT_SRC/zenbot-rebuild.service" /etc/systemd/system/zenbot-rebuild.service
install -m 0644 "$UNIT_SRC/zenbot-rebuild.path" /etc/systemd/system/zenbot-rebuild.path

# Ensure maxi can use docker (ignore if already set)
usermod -aG docker maxi 2>/dev/null || true

systemctl daemon-reload
systemctl enable --now zenbot-rebuild.path
systemctl status zenbot-rebuild.path --no-pager || true

echo
echo "Installed. Test:"
echo "  echo 'manual test' | sudo -u maxi tee $DATA_DIR/REQUEST_REBUILD"
echo "  journalctl -u zenbot-rebuild.service -f"
echo "  # or: tail -f $DATA_DIR/logs/rebuild.log"
