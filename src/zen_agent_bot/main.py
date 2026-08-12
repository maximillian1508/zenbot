from __future__ import annotations

import asyncio
import logging
import os

import uvicorn

from .config import load_config
from .gateway import Gateway
from .store import ConfigStore
from .transports import run_discord_bots, run_telegram_bots
from .web import create_admin_app

log = logging.getLogger(__name__)


async def run_admin_server(db: ConfigStore, listen: str) -> None:
    host, _, port_str = listen.rpartition(":")
    if not host:
        host, port_str = "0.0.0.0", listen
    port = int(port_str)
    app = create_admin_app(db=db)
    server = uvicorn.Server(
        uvicorn.Config(app, host=host, port=port, log_level="warning", loop="asyncio")
    )
    log.info("Admin UI listening on http://%s:%s", host, port)
    await server.serve()


async def run_gateway() -> None:
    config = load_config()
    gateway = Gateway(config)

    has_discord = bool(config.agents.discord_agents())
    has_telegram = bool(config.agents.telegram_agents())
    if not has_discord and not has_telegram:
        raise SystemExit("No Discord or Telegram agents enabled")

    log.info(
        "Starting zen-agent-bot: %d agent(s), discord=%s telegram=%s admin=%s db=%s",
        len(config.agents.all()),
        has_discord,
        has_telegram,
        config.admin_enabled,
        config.db.path,
    )

    tasks: list[asyncio.Task[None]] = []
    if has_discord:
        tasks.append(asyncio.create_task(run_discord_bots(gateway), name="discord"))
    if has_telegram:
        tasks.append(asyncio.create_task(run_telegram_bots(gateway), name="telegram"))
    if config.admin_enabled:
        tasks.append(
            asyncio.create_task(run_admin_server(config.db, config.admin_listen), name="admin")
        )

    await asyncio.gather(*tasks)


def main() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    asyncio.run(run_gateway())
