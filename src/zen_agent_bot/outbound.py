"""Outbound file attachments: agent reply text -> files for the transport.

Agents mark files with `[[attach: /path/to/file.png]]`. The marker is always
stripped from the visible reply; a file is only sent if it passes validation
(inside an allowed root, a regular file, under the size cap, not obviously a
secret). Rejections come back as short notes so the reply explains itself.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

# Discord's non-boosted upload limit is 10 MiB; stay under it.
DEFAULT_MAX_BYTES = 8 * 1024 * 1024
DEFAULT_MAX_FILES = 10  # Discord allows 10 attachments per message

MARKER_RE = re.compile(r"\[\[\s*attach\s*:\s*([^\]\n]+?)\s*\]\]", re.IGNORECASE)

# Defence in depth. The agent can already paste secrets into reply text, but
# don't make silent bulk exfiltration a one-liner.
DENY_NAMES = frozenset({".env", "credentials", "id_rsa", "id_ed25519", "token"})
DENY_SUFFIXES = frozenset({".key", ".pem", ".p12", ".pfx", ".kdbx"})
DENY_PARTS = frozenset({".ssh", ".aws", ".gnupg", "approvals"})


@dataclass
class OutboundFile:
    path: Path
    name: str


@dataclass
class OutboundReply:
    text: str
    files: list[OutboundFile] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def has_files(self) -> bool:
        return bool(self.files)


def limits_from_env() -> tuple[int, int]:
    """(max_bytes, max_files) — env overrides for odd cases (Nitro, etc.)."""

    def _int(name: str, default: int) -> int:
        raw = (os.environ.get(name) or "").strip()
        if not raw:
            return default
        try:
            value = int(raw)
        except ValueError:
            return default
        return value if value > 0 else default

    return _int("OUTBOUND_MAX_BYTES", DEFAULT_MAX_BYTES), _int(
        "OUTBOUND_MAX_FILES", DEFAULT_MAX_FILES
    )


def _denied(path: Path) -> bool:
    name = path.name.lower()
    if name in DENY_NAMES or path.suffix.lower() in DENY_SUFFIXES:
        return True
    if name.startswith(".env"):
        return True
    lowered = {part.lower() for part in path.parts}
    return bool(lowered & DENY_PARTS)


def _within(path: Path, roots: tuple[Path, ...]) -> bool:
    for root in roots:
        try:
            path.relative_to(root)
            return True
        except ValueError:
            continue
    return False


def _clean_text(text: str) -> str:
    """Drop markers without leaving holes in the prose.

    A marker on its own line takes the whole line with it; blank lines the
    model actually wrote are kept (runs of them collapse to one).
    """
    out: list[str] = []
    for raw in (text or "").splitlines():
        had_marker = bool(MARKER_RE.search(raw))
        line = MARKER_RE.sub("", raw).rstrip()
        if had_marker and not line.strip():
            continue
        if not line and out and not out[-1]:
            continue
        out.append(line)
    return "\n".join(out).strip()


def extract_outbound(
    text: str,
    *,
    allowed_roots: tuple[Path, ...],
    max_bytes: int = DEFAULT_MAX_BYTES,
    max_files: int = DEFAULT_MAX_FILES,
) -> OutboundReply:
    """Pull `[[attach: …]]` files out of reply text.

    Paths are resolved (symlinks included) and must sit inside allowed_roots,
    so `[[attach: ../../etc/shadow]]` and symlink escapes both fail closed.
    """
    matches = MARKER_RE.findall(text or "")
    cleaned = _clean_text(text or "")
    if not matches:
        return OutboundReply(text=cleaned)

    roots = tuple(r.expanduser().resolve() for r in allowed_roots)
    files: list[OutboundFile] = []
    notes: list[str] = []
    seen: set[Path] = set()

    for raw in matches:
        candidate = raw.strip().strip('"').strip("'")
        if not candidate:
            continue
        try:
            path = Path(candidate).expanduser().resolve()
        except (OSError, RuntimeError):
            notes.append(f"⚠️ Could not resolve `{candidate}`")
            continue
        if path in seen:
            continue
        if not _within(path, roots):
            notes.append(f"⚠️ `{path}` is outside the allowed directories — not sent")
            continue
        if _denied(path):
            notes.append(f"⚠️ `{path.name}` looks like a secret — not sent")
            continue
        if not path.is_file():
            notes.append(f"⚠️ `{path}` not found — not sent")
            continue
        try:
            size = path.stat().st_size
        except OSError:
            notes.append(f"⚠️ Could not read `{path}` — not sent")
            continue
        if size > max_bytes:
            notes.append(
                f"⚠️ `{path.name}` is {size // (1024 * 1024)} MiB "
                f"(limit {max_bytes // (1024 * 1024)} MiB) — not sent"
            )
            continue
        if len(files) >= max_files:
            notes.append(f"⚠️ Attachment limit ({max_files}) reached — `{path.name}` skipped")
            continue
        seen.add(path)
        files.append(OutboundFile(path=path, name=path.name))

    return OutboundReply(text=cleaned, files=files, notes=notes)


def format_notes(notes: list[str]) -> str:
    return "\n".join(notes)


def describe_unsupported(files: list[OutboundFile]) -> str:
    """Fallback for transports without upload support — list the paths."""
    if not files:
        return ""
    lines = ["📎 File(s) ready on the host (this transport can't upload yet):"]
    lines += [f"• `{f.path}`" for f in files]
    return "\n".join(lines)
