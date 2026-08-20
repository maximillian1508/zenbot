#!/usr/bin/env bash
# Install Cursor hooks that route shell/MCP approvals to Discord Accept/Deny.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HOOK="$ROOT/scripts/discord-approval-hook.py"
TOKEN_DIR="$ROOT/data/approvals"
HOOKS_DIR="${HOME}/.cursor"
HOOKS_JSON="${HOOKS_DIR}/hooks.json"

chmod +x "$HOOK"
mkdir -p "$TOKEN_DIR" "$HOOKS_DIR"

HOOK="$HOOK" HOOKS_JSON="$HOOKS_JSON" python3 - <<'PY'
import json
import os
from pathlib import Path

hooks_path = Path(os.environ["HOOKS_JSON"])
hook_cmd = os.environ["HOOK"]
payload = {
    "version": 1,
    "hooks": {
        "beforeShellExecution": [
            {
                "command": hook_cmd,
                "timeout": 320,
                "failClosed": True,
            }
        ],
        "beforeMCPExecution": [
            {
                "command": hook_cmd,
                "timeout": 320,
                "failClosed": True,
            }
        ],
    },
}
if hooks_path.is_file():
    existing = json.loads(hooks_path.read_text(encoding="utf-8"))
    merged = dict(existing)
    hooks = dict(merged.get("hooks") or {})
    hooks["beforeShellExecution"] = payload["hooks"]["beforeShellExecution"]
    hooks["beforeMCPExecution"] = payload["hooks"]["beforeMCPExecution"]
    merged["hooks"] = hooks
    merged["version"] = existing.get("version", 1)
    hooks_path.write_text(json.dumps(merged, indent=2) + "\n", encoding="utf-8")
else:
    hooks_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
print(f"Wrote {hooks_path}")
PY

echo "Installed Discord approval hooks."
echo "Token file is written by zen-agent-bot on start: $TOKEN_DIR/token"
echo "Use /trust approve + /backend cursor-sdk in a Discord thread to exercise Accept/Deny."
