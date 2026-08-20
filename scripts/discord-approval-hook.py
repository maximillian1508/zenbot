#!/usr/bin/env python3
"""Cursor hook → zen-agent-bot Discord Accept/Deny bridge.

Used from ~/.cursor/hooks.json for beforeShellExecution / beforeMCPExecution.
Blocks until the gateway posts Accept/Deny on Discord (trust=approve jobs only).
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_URL = "http://127.0.0.1:8787/internal/approvals"
TOKEN_CANDIDATES = [
    Path.home() / "apps" / "zen-agent-bot" / "data" / "approvals" / "token",
    Path("/home/maxi/apps/zen-agent-bot/data/approvals/token"),
]


def _read_token() -> str:
    env = os.environ.get("ZENBOT_APPROVAL_TOKEN", "").strip()
    if env:
        return env
    for path in TOKEN_CANDIDATES:
        if path.is_file():
            return path.read_text(encoding="utf-8").strip()
    return ""


def _payload_from_hook(raw: dict) -> dict:
    if "command" in raw:
        cmd = str(raw.get("command") or "")
        return {
            "kind": "shell",
            "summary": cmd[:180],
            "detail": cmd,
            "cwd": raw.get("cwd"),
            "timeout_sec": int(os.environ.get("ZENBOT_APPROVAL_TIMEOUT", "300")),
        }
    tool = str(raw.get("tool_name") or "mcp")
    tool_input = raw.get("tool_input")
    detail = tool_input if isinstance(tool_input, str) else json.dumps(tool_input or {})
    return {
        "kind": "mcp",
        "summary": tool[:180],
        "detail": detail[:4000],
        "cwd": raw.get("cwd"),
        "timeout_sec": int(os.environ.get("ZENBOT_APPROVAL_TIMEOUT", "300")),
    }


def main() -> int:
    try:
        raw = json.load(sys.stdin)
    except Exception:
        print(json.dumps({"permission": "deny", "user_message": "invalid hook input"}))
        return 0
    if not isinstance(raw, dict):
        print(json.dumps({"permission": "deny", "user_message": "invalid hook input"}))
        return 0

    token = _read_token()
    if not token:
        # No bridge configured — fail open so normal force-mode jobs keep working.
        print(json.dumps({"permission": "allow"}))
        return 0

    url = os.environ.get("ZENBOT_APPROVAL_URL", DEFAULT_URL).strip() or DEFAULT_URL
    body = json.dumps(_payload_from_hook(raw)).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=float(os.environ.get("ZENBOT_APPROVAL_TIMEOUT", "310"))) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        # 409 = no approve-mode job for this cwd → allow (force-mode / other agent).
        if exc.code == 409:
            print(json.dumps({"permission": "allow"}))
            return 0
        print(
            json.dumps(
                {
                    "permission": "deny",
                    "user_message": f"approval bridge HTTP {exc.code}",
                }
            )
        )
        return 0
    except Exception as exc:
        print(
            json.dumps(
                {
                    "permission": "deny",
                    "user_message": f"approval bridge error: {exc}",
                }
            )
        )
        return 0

    permission = str(data.get("permission") or "deny")
    if permission not in ("allow", "deny"):
        permission = "deny"
    print(json.dumps({"permission": permission}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
