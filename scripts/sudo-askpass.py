#!/usr/bin/env python3
"""SUDO_ASKPASS helper → zen-agent-bot Discord password modal.

sudo runs this when it needs a password and no tty is available (the shim in
scripts/sudo-shim forces -A). It blocks until the user submits the password
via the Discord modal, then prints it to stdout for sudo. On deny/timeout it
exits non-zero so sudo fails cleanly.

The password only ever travels loopback (127.0.0.1) and is never written to
disk or logs.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_URL = "http://127.0.0.1:8787/internal/sudo"
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


def main() -> int:
    prompt = sys.argv[1] if len(sys.argv) > 1 else "sudo password"
    token = _read_token()
    if not token:
        print("zenbot sudo-askpass: no bridge token", file=sys.stderr)
        return 1

    url = os.environ.get("ZENBOT_SUDO_URL", DEFAULT_URL).strip() or DEFAULT_URL
    timeout = float(os.environ.get("ZENBOT_SUDO_TIMEOUT", "190"))
    body = json.dumps({"prompt": prompt, "cwd": os.getcwd()}).encode("utf-8")
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
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        print(f"zenbot sudo-askpass: denied (HTTP {exc.code})", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"zenbot sudo-askpass: bridge error: {exc}", file=sys.stderr)
        return 1

    password = data.get("password")
    if not isinstance(password, str) or not password:
        return 1
    print(password)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
