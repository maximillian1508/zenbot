from __future__ import annotations

import asyncio
import logging
import os
import signal
from pathlib import Path

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


def _write_approval_token(data_dir: Path, token: str) -> None:
    path = data_dir / "approvals" / "token"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(token + "\n", encoding="utf-8")
    path.chmod(0o600)
    log.info("Wrote Discord approval token to %s", path)


def _install_sudo_askpass_env() -> None:
    """Point child processes (agent CLI, SDK bridge, claude) at the Discord
    sudo prompt: a PATH shim forces `sudo -A`, and SUDO_ASKPASS blocks on the
    gateway's /internal/sudo modal flow."""
    scripts = Path(__file__).resolve().parents[2] / "scripts"
    askpass = scripts / "sudo-askpass.py"
    shim_dir = scripts / "sudo-shim"
    if not askpass.is_file() or not (shim_dir / "sudo").is_file():
        log.warning("sudo askpass helpers missing under %s — sudo prompts disabled", scripts)
        return
    os.environ["SUDO_ASKPASS"] = str(askpass)
    path = os.environ.get("PATH", "")
    if str(shim_dir) not in path.split(":"):
        os.environ["PATH"] = f"{shim_dir}:{path}"
    log.info("sudo askpass bridge enabled (%s)", askpass)


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
    _write_approval_token(config.data_dir, gateway.approvals.token)
    _install_sudo_askpass_env()

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
