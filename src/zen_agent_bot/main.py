from __future__ import annotations

import asyncio
import logging
import os
import signal

import uvicorn
from telegram.ext import Application

from .config import load_config
from .gateway import Gateway
from .scheduler import CronScheduler
from .store import ConfigStore
from .transports.discord import AgentDiscordBot, run_discord_bots
from .transports.telegram import run_telegram_bots
from .web import create_admin_app

log = logging.getLogger(__name__)


async def run_admin_server(
    db: ConfigStore,
    listen: str,
    server_out: list[uvicorn.Server],
    gateway: Gateway | None = None,
) -> None:
    host, _, port_str = listen.rpartition(":")
    if not host:
        host, port_str = "0.0.0.0", listen
    port = int(port_str)
    app = create_admin_app(db=db, gateway=gateway)
    server = uvicorn.Server(
        uvicorn.Config(app, host=host, port=port, log_level="warning", loop="asyncio")
    )
    server_out.append(server)
    log.info("Admin UI listening on http://%s:%s", host, port)
    await server.serve()


async def _shutdown_services(
    *,
    gateway: Gateway,
    shutdown_event: asyncio.Event,
    discord_clients: list[AgentDiscordBot],
    telegram_apps: list[Application],
    admin_servers: list[uvicorn.Server],
) -> None:
    await shutdown_event.wait()
    # Keep ≤ systemd TimeoutStopSec. Host unit sets SHUTDOWN_GRACE_SEC=600
    # with TimeoutStopSec=620 after `install-host-service.sh` / unit copy.
    grace = float(os.environ.get("SHUTDOWN_GRACE_SEC", "180"))
    log.info("Shutdown signal received (grace=%ss)", grace)
    await gateway.shutdown(grace_sec=grace)

    for server in admin_servers:
        server.should_exit = True

    for client in discord_clients:
        await client.close()

    for app in telegram_apps:
        await app.updater.stop()  # type: ignore[union-attr]
        await app.stop()
        await app.shutdown()


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

    shutdown_event = asyncio.Event()
    discord_clients: list[AgentDiscordBot] = []
    telegram_apps: list[Application] = []
    admin_servers: list[uvicorn.Server] = []
    scheduler = CronScheduler(gateway)
    gateway.scheduler = scheduler

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, shutdown_event.set)

    tasks: list[asyncio.Task[None]] = []
    if has_discord:
        tasks.append(
            asyncio.create_task(
                run_discord_bots(gateway, clients_out=discord_clients),
                name="discord",
            )
        )
    if has_telegram:
        tasks.append(
            asyncio.create_task(
                run_telegram_bots(gateway, apps_out=telegram_apps),
                name="telegram",
            )
        )
    if config.admin_enabled:
        tasks.append(
            asyncio.create_task(
                run_admin_server(
                    config.db,
                    config.admin_listen,
                    admin_servers,
                    gateway=gateway,
                ),
                name="admin",
            )
        )
    tasks.append(
        asyncio.create_task(scheduler.run(shutdown_event), name="cron")
    )

    shutdown_task = asyncio.create_task(
        _shutdown_services(
            gateway=gateway,
            shutdown_event=shutdown_event,
            discord_clients=discord_clients,
            telegram_apps=telegram_apps,
            admin_servers=admin_servers,
        ),
        name="shutdown",
    )

    try:
        await asyncio.gather(*tasks)
    finally:
        shutdown_event.set()
        if not shutdown_task.done():
            await shutdown_task


def main() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    asyncio.run(run_gateway())
