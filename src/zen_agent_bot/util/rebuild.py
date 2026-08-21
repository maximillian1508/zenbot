from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import discord
    from telegram.ext import Application

log = logging.getLogger(__name__)

REBUILD_FLAG_NAME = "REQUEST_REBUILD"
REBUILD_NOTIFY_NAME = "REBUILD_NOTIFY.json"


@dataclass(frozen=True)
class RebuildNotify:
    transport: str  # discord | telegram
    channel_id: str
    user_id: str
    mention: str
    agent_id: str = "manager"


def rebuild_flag_path(data_dir: Path) -> Path:
    return data_dir / REBUILD_FLAG_NAME


def rebuild_notify_path(data_dir: Path) -> Path:
    return data_dir / REBUILD_NOTIFY_NAME


def save_rebuild_notify(data_dir: Path, notify: RebuildNotify) -> Path:
    data_dir.mkdir(parents=True, exist_ok=True)
    path = rebuild_notify_path(data_dir)
    path.write_text(json.dumps(asdict(notify), indent=2) + "\n", encoding="utf-8")
    return path


def load_rebuild_notify(data_dir: Path) -> RebuildNotify | None:
    path = rebuild_notify_path(data_dir)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return None
        return RebuildNotify(
            transport=str(data["transport"]),
            channel_id=str(data["channel_id"]),
            user_id=str(data["user_id"]),
            mention=str(data.get("mention") or ""),
            agent_id=str(data.get("agent_id") or "manager"),
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        log.warning("Ignoring corrupt rebuild notify file %s", path)
        return None


def clear_rebuild_notify(data_dir: Path) -> None:
    rebuild_notify_path(data_dir).unlink(missing_ok=True)


def request_rebuild(
    data_dir: Path,
    *,
    reason: str = "",
    notify: RebuildNotify | None = None,
) -> Path:
    """Write the host-watched rebuild flag. Returns the flag path."""
    data_dir.mkdir(parents=True, exist_ok=True)
    path = rebuild_flag_path(data_dir)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    body = f"requested_at={stamp}\n"
    if reason.strip():
        body += f"reason={reason.strip()[:500]}\n"
    path.write_text(body, encoding="utf-8")
    if notify is not None:
        save_rebuild_notify(data_dir, notify)
    return path


def rebuild_pending(data_dir: Path) -> bool:
    return rebuild_flag_path(data_dir).is_file()


async def deliver_rebuild_notify_discord(
    client: discord.Client,
    data_dir: Path,
) -> bool:
    """Ping the requester on Discord after a successful restart."""
    notify = load_rebuild_notify(data_dir)
    if notify is None or notify.transport != "discord":
        return False
    from ..notify import format_rebuild_done_ping

    try:
        channel = client.get_channel(int(notify.channel_id))
        if channel is None:
            channel = await client.fetch_channel(int(notify.channel_id))
        await channel.send(format_rebuild_done_ping(mention=notify.mention or None))
        clear_rebuild_notify(data_dir)
        log.info("Rebuild done ping sent to Discord channel %s", notify.channel_id)
        return True
    except Exception:
        log.exception(
            "Failed to deliver rebuild notify to Discord channel %s",
            notify.channel_id,
        )
        return False


async def deliver_rebuild_notify_telegram(
    app: Application,
    *,
    agent_id: str,
    data_dir: Path,
) -> bool:
    notify = load_rebuild_notify(data_dir)
    if notify is None or notify.transport != "telegram":
        return False
    if notify.agent_id != agent_id:
        return False
    from ..notify import format_rebuild_done_ping

    try:
        bot = app.bot
        text = format_rebuild_done_ping(mention=notify.mention or None)
        await bot.send_message(chat_id=int(notify.channel_id), text=text)
        clear_rebuild_notify(data_dir)
        log.info("Rebuild done ping sent to Telegram chat %s", notify.channel_id)
        return True
    except Exception:
        log.exception(
            "Failed to deliver rebuild notify to Telegram chat %s",
            notify.channel_id,
        )
        return False
