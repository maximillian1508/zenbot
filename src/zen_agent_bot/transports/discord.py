from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

import discord
from discord import app_commands

from ..agents.profile import AgentProfile
from ..attachments import (
    DEFAULT_MAX_BYTES,
    DEFAULT_MAX_FILES,
    SavedAttachment,
    attachments_dir,
    merge_prompt_with_attachments,
    write_attachment,
)
from ..gateway.router import Gateway, title_from_prompt
from ..notify import format_close_reply
from ..sessions import ThreadSession

if TYPE_CHECKING:
    from discord.abc import Messageable

log = logging.getLogger(__name__)

DISCORD_CHUNK = 1900


class QueueJobView(discord.ui.View):
    """Stop & send / drop controls on a queued follow-up status message."""

    def __init__(self, gateway: Gateway, session_key: str, job_id: str) -> None:
        super().__init__(timeout=None)
        self.gateway = gateway
        self.session_key = session_key
        self.job_id = job_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user and self.gateway.is_allowed(interaction.user.id):
            return True
        if interaction.response.is_done():
            await interaction.followup.send("Not authorized.", ephemeral=True)
        else:
            await interaction.response.send_message("Not authorized.", ephemeral=True)
        return False

    @discord.ui.button(label="Send now", style=discord.ButtonStyle.primary)
    async def send_now(
        self, interaction: discord.Interaction, button: discord.ui.Button[Any]
    ) -> None:
        await interaction.response.defer()
        result = await self.gateway.send_now(self.session_key, self.job_id)
        if result == "missing":
            await interaction.followup.send(
                "Already started or dropped.", ephemeral=True
            )
            try:
                await interaction.message.edit(view=None)
            except discord.HTTPException:
                pass
            self.stop()

    @discord.ui.button(label="Drop", style=discord.ButtonStyle.secondary)
    async def drop(
        self, interaction: discord.Interaction, button: discord.ui.Button[Any]
    ) -> None:
        await interaction.response.defer()
        ok = await self.gateway.drop_queued(self.session_key, self.job_id)
        if not ok:
            await interaction.followup.send(
                "Already started or dropped.", ephemeral=True
            )
            try:
                await interaction.message.edit(view=None)
            except discord.HTTPException:
                pass
        self.stop()


async def send_chunks(target: Messageable, text: str) -> None:
    if len(text) <= DISCORD_CHUNK:
        await target.send(text)
        return
    start = 0
    while start < len(text):
        await target.send(text[start : start + DISCORD_CHUNK])
        start += DISCORD_CHUNK


async def send_chunks_reply(status_msg: discord.Message, text: str) -> None:
    """Send final output as replies to this job's status message.

    Bare channel.send() lands after any messages posted while the job ran
    (e.g. a queued follow-up), so the reply looks attached to the wrong turn.
    """
    if len(text) <= DISCORD_CHUNK:
        await status_msg.reply(text, mention_author=False)
        return
    start = 0
    while start < len(text):
        await status_msg.reply(text[start : start + DISCORD_CHUNK], mention_author=False)
        start += DISCORD_CHUNK


def thread_key(channel: discord.abc.GuildChannel | discord.Thread) -> str:
    return str(channel.id)


def in_agent_channel(
    message: discord.Message,
    agent_channel_id: int,
) -> bool:
    ch = message.channel
    if isinstance(ch, discord.Thread):
        parent = ch.parent
        return parent is not None and parent.id == agent_channel_id
    return getattr(ch, "id", None) == agent_channel_id


async def save_discord_attachments(
    message: discord.Message,
    dest_dir: Path,
) -> list[SavedAttachment]:
    saved: list[SavedAttachment] = []
    for att in message.attachments[:DEFAULT_MAX_FILES]:
        if att.size and att.size > DEFAULT_MAX_BYTES:
            log.warning("Skip Discord attachment %s — too large (%s)", att.filename, att.size)
            continue
        try:
            data = await att.read()
        except Exception:
            log.exception("Failed to download Discord attachment %s", att.filename)
            continue
        item = await write_attachment(
            dest_dir,
            filename=att.filename or f"attachment-{att.id}",
            data=data,
            content_type=att.content_type,
            original_name=att.filename,
        )
        if item:
            saved.append(item)
    return saved


class AgentDiscordBot(discord.Client):
    def __init__(
        self,
        *,
        gateway: Gateway,
        profile: AgentProfile,
        guild_id: int | None,
    ) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        intents.guilds = True
        super().__init__(intents=intents)
        self.gateway = gateway
        self.profile = profile
        self.guild_id = guild_id
        self.tree = app_commands.CommandTree(self)
        assert profile.discord is not None
        self.binding = profile.discord

    async def setup_hook(self) -> None:
        agent_id = self.profile.id
        is_manager = self.profile.is_manager

        @self.tree.command(name="new", description="Start a fresh agent session in this thread")
        async def cmd_new(interaction: discord.Interaction) -> None:
            if not interaction.user or not self.gateway.is_allowed(interaction.user.id):
                await interaction.response.send_message("Not authorized.", ephemeral=True)
                return
            channel = interaction.channel
            if channel is None:
                await interaction.response.send_message("No channel.", ephemeral=True)
                return
            key = self.gateway.session_key(agent_id, "discord", thread_key(channel))
            self.gateway.reset_session_resume(key)
            await interaction.response.send_message(
                "New session. Your next message here starts fresh (no `--resume`). "
                "`/model` and `/backend` overrides are kept."
            )

        @self.tree.command(name="status", description="Show agent session for this thread")
        async def cmd_status(interaction: discord.Interaction) -> None:
            if not interaction.user or not self.gateway.is_allowed(interaction.user.id):
                await interaction.response.send_message("Not authorized.", ephemeral=True)
                return
            channel = interaction.channel
            if channel is None:
                await interaction.response.send_message("No channel.", ephemeral=True)
                return
            key = self.gateway.session_key(agent_id, "discord", thread_key(channel))
            sess = self.gateway.get_session(key)
            if sess.session_id:
                msg = f"**Session:** `{sess.session_id}`"
                if sess.title:
                    msg += f"\n**Title:** {sess.title}"
            else:
                msg = "No session yet — next message starts a new agent run."
            msg += "\n\n" + await self.gateway.apply_model_command(
                session_key=key, agent_id=agent_id, raw=None
            )
            await interaction.response.send_message(msg, ephemeral=True)

        @self.tree.command(
            name="cancel",
            description="Cancel the in-flight agent job in this thread",
        )
        async def cmd_cancel(interaction: discord.Interaction) -> None:
            if not interaction.user or not self.gateway.is_allowed(interaction.user.id):
                await interaction.response.send_message("Not authorized.", ephemeral=True)
                return
            channel = interaction.channel
            if channel is None:
                await interaction.response.send_message("No channel.", ephemeral=True)
                return
            key = self.gateway.session_key(agent_id, "discord", thread_key(channel))
            if await self.gateway.cancel_session(key):
                await interaction.response.send_message(
                    "Cancelling the running job…",
                    ephemeral=True,
                )
            else:
                await interaction.response.send_message(
                    "Nothing running in this thread.",
                    ephemeral=True,
                )

        @self.tree.command(
            name="close",
            description="Close this thread's agent session (resume + overrides)",
        )
        async def cmd_close(interaction: discord.Interaction) -> None:
            if not interaction.user or not self.gateway.is_allowed(interaction.user.id):
                await interaction.response.send_message("Not authorized.", ephemeral=True)
                return
            channel = interaction.channel
            if channel is None:
                await interaction.response.send_message("No channel.", ephemeral=True)
                return
            key = self.gateway.session_key(agent_id, "discord", thread_key(channel))
            info = await self.gateway.close_session(key)
            await interaction.response.send_message(
                format_close_reply(
                    cancelled=bool(info["cancelled"]),
                    dropped=int(info["dropped"]),
                )
            )

        @self.tree.command(
            name="model",
            description="Show Cursor models or set the model for this thread",
        )
        @app_commands.describe(
            name="Model id from agent models, or clear/default"
        )
        async def cmd_model(
            interaction: discord.Interaction, name: str | None = None
        ) -> None:
            if not interaction.user or not self.gateway.is_allowed(interaction.user.id):
                await interaction.response.send_message("Not authorized.", ephemeral=True)
                return
            channel = interaction.channel
            if channel is None:
                await interaction.response.send_message("No channel.", ephemeral=True)
                return
            key = self.gateway.session_key(agent_id, "discord", thread_key(channel))
            include_catalog = name is None or name.strip().lower() in {"", "list", "ls"}
            msg = await self.gateway.apply_model_command(
                session_key=key,
                agent_id=agent_id,
                raw=name,
                include_catalog=include_catalog,
                catalog_max_chars=1400,
            )
            await interaction.response.send_message(msg, ephemeral=True)

        @cmd_model.autocomplete("name")
        async def model_autocomplete(
            interaction: discord.Interaction, current: str
        ) -> list[app_commands.Choice[str]]:
            if not interaction.user or not self.gateway.is_allowed(interaction.user.id):
                return []
            q = current.lower().strip()
            choices: list[app_commands.Choice[str]] = []
            extras = [
                ("clear", "clear thread override"),
                ("default", "admin / CLI default"),
            ]
            try:
                models = await self.gateway.cursor_models()
            except Exception:
                models = []
            for mid, label in extras + models:
                hay = f"{mid} {label}".lower()
                if q and q not in hay:
                    continue
                display = f"{label} · {mid}" if label != mid else mid
                if len(display) > 100:
                    display = display[:97] + "…"
                choices.append(app_commands.Choice(name=display, value=mid[:100]))
                if len(choices) >= 25:
                    break
            return choices

        @self.tree.command(
            name="backend",
            description="Show or set the agent backend for this thread",
        )
        @app_commands.describe(
            name="cursor-cli, claude-cli, openrouter, or clear/default"
        )
        async def cmd_backend(
            interaction: discord.Interaction, name: str | None = None
        ) -> None:
            if not interaction.user or not self.gateway.is_allowed(interaction.user.id):
                await interaction.response.send_message("Not authorized.", ephemeral=True)
                return
            channel = interaction.channel
            if channel is None:
                await interaction.response.send_message("No channel.", ephemeral=True)
                return
            key = self.gateway.session_key(agent_id, "discord", thread_key(channel))
            include_catalog = name is None or name.strip().lower() in {"", "list", "ls"}
            msg = self.gateway.apply_backend_command(
                session_key=key,
                agent_id=agent_id,
                raw=name,
                include_catalog=include_catalog,
            )
            await interaction.response.send_message(msg, ephemeral=True)

        @cmd_backend.autocomplete("name")
        async def backend_autocomplete(
            interaction: discord.Interaction, current: str
        ) -> list[app_commands.Choice[str]]:
            if not interaction.user or not self.gateway.is_allowed(interaction.user.id):
                return []
            q = current.lower().strip()
            extras = [
                ("clear", "clear thread override"),
                ("default", "agent profile default"),
            ]
            rows = extras + [
                (bid, bid) for bid in sorted(self.gateway.known_backends())
            ]
            choices: list[app_commands.Choice[str]] = []
            for mid, label in rows:
                hay = f"{mid} {label}".lower()
                if q and q not in hay:
                    continue
                display = f"{label} · {mid}" if label != mid else mid
                choices.append(app_commands.Choice(name=display[:100], value=mid[:100]))
                if len(choices) >= 25:
                    break
            return choices

        if is_manager:

            @self.tree.command(name="agents", description="List configured agent profiles")
            async def cmd_agents(interaction: discord.Interaction) -> None:
                if not interaction.user or not self.gateway.is_allowed(interaction.user.id):
                    await interaction.response.send_message("Not authorized.", ephemeral=True)
                    return
                await interaction.response.send_message(
                    self.gateway.list_agents_markdown(),
                    ephemeral=True,
                )

            @self.tree.command(
                name="rebuild",
                description="Request host rebuild of zen-agent-bot (manager only)",
            )
            async def cmd_rebuild(interaction: discord.Interaction) -> None:
                if not interaction.user or not self.gateway.is_allowed(interaction.user.id):
                    await interaction.response.send_message("Not authorized.", ephemeral=True)
                    return
                if self.gateway.rebuild_pending():
                    await interaction.response.send_message(
                        "Rebuild already requested — waiting for host watcher.",
                        ephemeral=True,
                    )
                    return
                path = self.gateway.request_rebuild(
                    reason=f"discord:/rebuild by {interaction.user.id}"
                )
                await interaction.response.send_message(
                    "Restart requested. Host will `systemctl restart` in ~15s "
                    f"(flag `{path.name}`). Ping this thread after `/health` is OK."
                )

        # Commands are registered as global on the tree. sync(guild=…) only
        # pushes guild-scoped commands (we have none) and leaves globals stale —
        # that is why /cancel disappeared from Discord after we added it.
        guild = discord.Object(id=self.guild_id) if self.guild_id else None
        if guild:
            try:
                self.tree.copy_global_to(guild=guild)
                synced = await self.tree.sync(guild=guild)
                log.info(
                    "Slash commands guild-synced for %s (%s): %s",
                    self.profile.display_name,
                    self.guild_id,
                    ", ".join(sorted(c.name for c in synced)) or "(none)",
                )
            except discord.Forbidden:
                log.warning(
                    "Guild sync failed for %s (bot not in server %s?) — using global sync",
                    self.profile.display_name,
                    self.guild_id,
                )
                synced = await self.tree.sync()
                log.info(
                    "Slash commands global-synced for %s: %s",
                    self.profile.display_name,
                    ", ".join(sorted(c.name for c in synced)) or "(none)",
                )
        else:
            synced = await self.tree.sync()
            log.info(
                "Slash commands global-synced for %s: %s",
                self.profile.display_name,
                ", ".join(sorted(c.name for c in synced)) or "(none)",
            )

    async def on_ready(self) -> None:
        log.info(
            "Discord ready: %s (%s) profile=%s",
            self.user,
            self.user.id if self.user else "?",
            self.profile.id,
        )

    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot:
            return
        if not self.gateway.is_allowed(message.author.id):
            return
        if not in_agent_channel(message, self.binding.agent_channel_id):
            return

        text = message.content.strip()
        if not text and not message.attachments:
            return

        channel = message.channel
        agent_id = self.profile.id
        sess_key = self.gateway.session_key(agent_id, "discord", thread_key(channel))

        dest = attachments_dir(
            self.gateway.config.data_dir,
            transport="discord",
            thread_key=thread_key(channel),
        )
        saved = await save_discord_attachments(message, dest)
        prompt = merge_prompt_with_attachments(text, saved)
        if not prompt:
            return

        title_src = text or (saved[0].original_name if saved else "attachment")

        if isinstance(channel, discord.TextChannel) and channel.id == self.binding.agent_channel_id:
            title = title_from_prompt(title_src)
            thread = await channel.create_thread(name=title[:100], auto_archive_duration=10080)
            preview = text[:500] if text else f"({len(saved)} attachment(s))"
            await thread.send(
                f"**Task from** {message.author.mention}\n\n"
                f"{preview}{'…' if text and len(text) > 500 else ''}"
            )
            channel = thread
            sess_key = self.gateway.session_key(agent_id, "discord", thread_key(thread))
            # Re-home files under the new thread id when starting from the channel.
            if saved:
                new_dest = attachments_dir(
                    self.gateway.config.data_dir,
                    transport="discord",
                    thread_key=thread_key(thread),
                )
                new_dest.mkdir(parents=True, exist_ok=True)
                relocated: list[SavedAttachment] = []
                for item in saved:
                    target = new_dest / item.path.name
                    item.path.replace(target)
                    relocated.append(
                        SavedAttachment(
                            path=target.resolve(),
                            original_name=item.original_name,
                            size=item.size,
                            content_type=item.content_type,
                        )
                    )
                saved = relocated
                prompt = merge_prompt_with_attachments(text, saved)
            self.gateway.store.set(sess_key, ThreadSession(session_id=None, title=title))
            status_msg = await thread.send("⏳ Agent running…")
        else:
            status_msg = await message.reply("⏳ Agent running…", mention_author=False)

        # Bind message objects as defaults so each job keeps its own status
        # target even if the enclosing locals were ever rebound.
        async def send(text_out: str, *, _status: discord.Message = status_msg) -> None:
            await send_chunks_reply(_status, text_out)

        async def edit_status(
            text_out: str,
            *,
            view: discord.ui.View | None = discord.utils.MISSING,  # type: ignore[assignment]
            _status: discord.Message = status_msg,
        ) -> None:
            kwargs: dict[str, Any] = {"content": text_out[:2000]}
            if view is not discord.utils.MISSING:
                kwargs["view"] = view
            await _status.edit(**kwargs)

        async def on_queued(job_id: str, *, _status: discord.Message = status_msg) -> None:
            qview = QueueJobView(self.gateway, sess_key, job_id)
            await _status.edit(view=qview)

        asyncio.create_task(
            self.gateway.run_job(
                agent_id=agent_id,
                session_key=sess_key,
                user_prompt=prompt,
                send=send,
                edit_status=edit_status,
                on_queued=on_queued,
                notify_mention=message.author.mention,
            ),
            name=f"agent-{sess_key}",
        )


async def run_discord_bots(
    gateway: Gateway,
    *,
    clients_out: list[AgentDiscordBot] | None = None,
) -> None:
    profiles = gateway.agents.discord_agents()
    if not profiles:
        log.warning("No Discord agents configured")
        return

    tasks: list[asyncio.Task[None]] = []
    for profile in profiles:
        assert profile.discord is not None
        guild_id = profile.discord.guild_id or gateway.config.discord_guild_id
        bot = AgentDiscordBot(gateway=gateway, profile=profile, guild_id=guild_id)
        if clients_out is not None:
            clients_out.append(bot)

        async def _run(client: AgentDiscordBot = bot, token: str = profile.discord.token) -> None:
            await client.start(token)

        task = asyncio.create_task(_run(), name=f"discord-{profile.id}")
        tasks.append(task)

    await asyncio.gather(*tasks)
