from __future__ import annotations

import asyncio
import logging
import re
from typing import TYPE_CHECKING

import discord
from discord import app_commands

from ..agents.profile import AgentProfile
from ..gateway.router import Gateway, title_from_prompt
from ..sessions import ThreadSession

if TYPE_CHECKING:
    from discord.abc import Messageable

log = logging.getLogger(__name__)

DISCORD_CHUNK = 1900


async def send_chunks(target: Messageable, text: str) -> None:
    if len(text) <= DISCORD_CHUNK:
        await target.send(text)
        return
    start = 0
    while start < len(text):
        await target.send(text[start : start + DISCORD_CHUNK])
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
            self.gateway.clear_session(key)
            await interaction.response.send_message(
                "New session. Your next message here starts fresh (no `--resume`)."
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

        guild = discord.Object(id=self.guild_id) if self.guild_id else None
        if guild:
            try:
                await self.tree.sync(guild=guild)
            except discord.Forbidden:
                log.warning(
                    "Guild sync failed for %s (bot not in server %s?) — using global sync",
                    self.profile.display_name,
                    self.guild_id,
                )
                await self.tree.sync()
        else:
            await self.tree.sync()
        log.info("Slash commands synced for %s", self.profile.display_name)

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

        prompt = message.content.strip()
        if not prompt:
            return

        channel = message.channel
        agent_id = self.profile.id
        sess_key = self.gateway.session_key(agent_id, "discord", thread_key(channel))

        if isinstance(channel, discord.TextChannel) and channel.id == self.binding.agent_channel_id:
            title = title_from_prompt(prompt)
            thread = await channel.create_thread(name=title[:100], auto_archive_duration=10080)
            await thread.send(
                f"**Task from** {message.author.mention}\n\n"
                f"{prompt[:500]}{'…' if len(prompt) > 500 else ''}"
            )
            channel = thread
            sess_key = self.gateway.session_key(agent_id, "discord", thread_key(thread))
            self.gateway.store.set(sess_key, ThreadSession(session_id=None, title=title))
            status_msg = await thread.send("⏳ Agent running…")
            target = thread
        else:
            status_msg = await message.reply("⏳ Agent running…", mention_author=False)
            target = channel

        async def send(text: str) -> None:
            await send_chunks(target, text)

        async def edit_status(text: str) -> None:
            await status_msg.edit(content=text)

        asyncio.create_task(
            self.gateway.run_job(
                agent_id=agent_id,
                session_key=sess_key,
                user_prompt=prompt,
                send=send,
                edit_status=edit_status,
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
