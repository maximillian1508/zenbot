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


RESERVED_SLASH = frozenset(
    {
        "new",
        "status",
        "cancel",
        "close",
        "model",
        "backend",
        "agents",
        "rebuild",
        "run",
    }
)


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


def group_profiles_by_token(
    profiles: list[AgentProfile],
) -> list[tuple[str, list[AgentProfile]]]:
    """One Discord gateway connection per unique bot token."""
    groups: dict[str, list[AgentProfile]] = {}
    order: list[str] = []
    for profile in profiles:
        if profile.discord is None:
            continue
        token = profile.discord.token
        if token not in groups:
            order.append(token)
            groups[token] = []
        groups[token].append(profile)
    return [(token, groups[token]) for token in order]


def channel_profile_map(profiles: list[AgentProfile]) -> dict[int, AgentProfile]:
    mapping: dict[int, AgentProfile] = {}
    for profile in profiles:
        if profile.discord is None:
            continue
        mapping[profile.discord.agent_channel_id] = profile
    return mapping


def resolve_agent_profile(
    by_channel: dict[int, AgentProfile],
    *,
    channel_id: int,
    parent_id: int | None,
) -> AgentProfile | None:
    """Thread → parent home channel; top-level message → that channel."""
    if parent_id is not None:
        return by_channel.get(parent_id)
    return by_channel.get(channel_id)


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


def _relocate_attachments(
    saved: list[SavedAttachment], dest: Path
) -> list[SavedAttachment]:
    dest.mkdir(parents=True, exist_ok=True)
    relocated: list[SavedAttachment] = []
    for item in saved:
        target = dest / item.path.name
        item.path.replace(target)
        relocated.append(
            SavedAttachment(
                path=target.resolve(),
                original_name=item.original_name,
                size=item.size,
                content_type=item.content_type,
            )
        )
    return relocated


class AgentDiscordBot(discord.Client):
    def __init__(
        self,
        *,
        gateway: Gateway,
        profiles: list[AgentProfile],
        guild_id: int | None,
    ) -> None:
        if not profiles:
            raise ValueError("AgentDiscordBot needs at least one profile")
        intents = discord.Intents.default()
        intents.message_content = True
        intents.guilds = True
        super().__init__(intents=intents)
        self.gateway = gateway
        self.profiles = profiles
        self.profile = next((p for p in profiles if p.is_manager), profiles[0])
        self.guild_id = guild_id
        self.tree = app_commands.CommandTree(self)
        self.by_channel = channel_profile_map(profiles)
        self.by_id = {p.id: p for p in profiles}
        self.has_manager = any(p.is_manager for p in profiles)

    def _profile_for_channel(
        self,
        channel: discord.abc.Messageable | None,
    ) -> AgentProfile | None:
        if channel is None:
            return None
        channel_id = getattr(channel, "id", None)
        if not isinstance(channel_id, int):
            return None
        parent_id: int | None = None
        if isinstance(channel, discord.Thread):
            parent = channel.parent
            if parent is not None:
                parent_id = parent.id
            elif channel.parent_id:
                parent_id = int(channel.parent_id)
        return resolve_agent_profile(
            self.by_channel, channel_id=channel_id, parent_id=parent_id
        )

    async def _require_ctx(
        self, interaction: discord.Interaction
    ) -> tuple[AgentProfile, Any] | None:
        if not interaction.user or not self.gateway.is_allowed(interaction.user.id):
            await interaction.response.send_message("Not authorized.", ephemeral=True)
            return None
        channel = interaction.channel
        if channel is None:
            await interaction.response.send_message("No channel.", ephemeral=True)
            return None
        profile = self._profile_for_channel(channel)
        if profile is None:
            await interaction.response.send_message(
                "Not an agent channel or thread. Use `/music`, `/general`, `/manager`, "
                "or post in that agent's home channel.",
                ephemeral=True,
            )
            return None
        return profile, channel

    async def _home_channel(self, profile: AgentProfile) -> discord.TextChannel | None:
        assert profile.discord is not None
        chan_id = profile.discord.agent_channel_id
        cached = self.get_channel(chan_id)
        if isinstance(cached, discord.TextChannel):
            return cached
        try:
            fetched = await self.fetch_channel(chan_id)
        except discord.HTTPException:
            return None
        return fetched if isinstance(fetched, discord.TextChannel) else None

    def _launch_job(
        self,
        *,
        profile: AgentProfile,
        sess_key: str,
        prompt: str,
        status_msg: discord.Message,
        mention: str | None,
    ) -> None:
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
                agent_id=profile.id,
                session_key=sess_key,
                user_prompt=prompt,
                send=send,
                edit_status=edit_status,
                on_queued=on_queued,
                notify_mention=mention,
            ),
            name=f"agent-{sess_key}",
        )

    async def _open_thread_and_run(
        self,
        *,
        profile: AgentProfile,
        home: discord.TextChannel,
        author: discord.abc.User,
        prompt: str,
        saved: list[SavedAttachment],
        title_src: str,
        preview_text: str,
    ) -> discord.Thread:
        title = title_from_prompt(title_src)
        thread = await home.create_thread(name=title[:100], auto_archive_duration=10080)
        extra = "…" if len(preview_text) > 500 else ""
        body = preview_text[:500] if preview_text else (
            f"({len(saved)} attachment(s))" if saved else ""
        )
        await thread.send(
            f"**{profile.display_name}** · task from {author.mention}\n\n"
            f"{body}{extra}"
        )
        sess_key = self.gateway.session_key(profile.id, "discord", thread_key(thread))
        if saved:
            saved = _relocate_attachments(
                saved,
                attachments_dir(
                    self.gateway.config.data_dir,
                    transport="discord",
                    thread_key=thread_key(thread),
                ),
            )
            prompt = merge_prompt_with_attachments(preview_text or title_src, saved)
        self.gateway.store.set(sess_key, ThreadSession(session_id=None, title=title))
        status_msg = await thread.send("⏳ Agent running…")
        self._launch_job(
            profile=profile,
            sess_key=sess_key,
            prompt=prompt,
            status_msg=status_msg,
            mention=getattr(author, "mention", None),
        )
        return thread

    async def _slash_start(
        self,
        interaction: discord.Interaction,
        profile: AgentProfile,
        prompt: str,
    ) -> None:
        if not interaction.user or not self.gateway.is_allowed(interaction.user.id):
            await interaction.response.send_message("Not authorized.", ephemeral=True)
            return
        prompt = prompt.strip()
        if not prompt:
            await interaction.response.send_message("Prompt is empty.", ephemeral=True)
            return
        if profile.discord is None:
            await interaction.response.send_message(
                f"**{profile.display_name}** has no Discord channel.",
                ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=False)
        home = await self._home_channel(profile)
        if home is None:
            await interaction.edit_original_response(
                content=(
                    f"Can't open **{profile.display_name}**'s home channel "
                    f"(id `{profile.discord.agent_channel_id}`). Is the bot in it?"
                )
            )
            return
        thread = await self._open_thread_and_run(
            profile=profile,
            home=home,
            author=interaction.user,
            prompt=prompt,
            saved=[],
            title_src=prompt,
            preview_text=prompt,
        )
        await interaction.edit_original_response(
            content=f"Started **{profile.display_name}** in {thread.mention}"
        )

    async def setup_hook(self) -> None:
        @self.tree.command(name="new", description="Start a fresh agent session in this thread")
        async def cmd_new(interaction: discord.Interaction) -> None:
            ctx = await self._require_ctx(interaction)
            if ctx is None:
                return
            profile, channel = ctx
            key = self.gateway.session_key(profile.id, "discord", thread_key(channel))
            self.gateway.reset_session_resume(key)
            await interaction.response.send_message(
                "New session. Your next message here starts fresh (no `--resume`). "
                "`/model` and `/backend` overrides are kept."
            )

        @self.tree.command(name="status", description="Show agent session for this thread")
        async def cmd_status(interaction: discord.Interaction) -> None:
            ctx = await self._require_ctx(interaction)
            if ctx is None:
                return
            profile, channel = ctx
            key = self.gateway.session_key(profile.id, "discord", thread_key(channel))
            sess = self.gateway.get_session(key)
            if sess.session_id:
                msg = f"**{profile.display_name}** · session `{sess.session_id}`"
                if sess.title:
                    msg += f"\n**Title:** {sess.title}"
            else:
                msg = f"**{profile.display_name}** — no session yet."
            msg += "\n\n" + await self.gateway.apply_model_command(
                session_key=key, agent_id=profile.id, raw=None
            )
            await interaction.response.send_message(msg, ephemeral=True)

        @self.tree.command(
            name="cancel",
            description="Cancel the in-flight agent job in this thread",
        )
        async def cmd_cancel(interaction: discord.Interaction) -> None:
            ctx = await self._require_ctx(interaction)
            if ctx is None:
                return
            profile, channel = ctx
            key = self.gateway.session_key(profile.id, "discord", thread_key(channel))
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
            ctx = await self._require_ctx(interaction)
            if ctx is None:
                return
            profile, channel = ctx
            key = self.gateway.session_key(profile.id, "discord", thread_key(channel))
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
            ctx = await self._require_ctx(interaction)
            if ctx is None:
                return
            profile, channel = ctx
            key = self.gateway.session_key(profile.id, "discord", thread_key(channel))
            include_catalog = name is None or name.strip().lower() in {"", "list", "ls"}
            msg = await self.gateway.apply_model_command(
                session_key=key,
                agent_id=profile.id,
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
            ctx = await self._require_ctx(interaction)
            if ctx is None:
                return
            profile, channel = ctx
            key = self.gateway.session_key(profile.id, "discord", thread_key(channel))
            include_catalog = name is None or name.strip().lower() in {"", "list", "ls"}
            msg = self.gateway.apply_backend_command(
                session_key=key,
                agent_id=profile.id,
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

        @self.tree.command(
            name="run",
            description="Start a job as a specialist (opens a thread in that agent's channel)",
        )
        @app_commands.describe(agent="Profile id", prompt="What the agent should do")
        async def cmd_run(
            interaction: discord.Interaction, agent: str, prompt: str
        ) -> None:
            target = self.by_id.get(agent.strip().lower())
            if target is None:
                try:
                    target = self.gateway.agents.get(agent.strip().lower())
                except KeyError:
                    await interaction.response.send_message(
                        f"Unknown agent `{agent}`. Try `/agents`.",
                        ephemeral=True,
                    )
                    return
            await self._slash_start(interaction, target, prompt)

        @cmd_run.autocomplete("agent")
        async def run_autocomplete(
            interaction: discord.Interaction, current: str
        ) -> list[app_commands.Choice[str]]:
            if not interaction.user or not self.gateway.is_allowed(interaction.user.id):
                return []
            q = current.lower().strip()
            out: list[app_commands.Choice[str]] = []
            for profile in self.profiles:
                hay = f"{profile.id} {profile.display_name}".lower()
                if q and q not in hay:
                    continue
                out.append(
                    app_commands.Choice(
                        name=f"{profile.display_name} ({profile.id})"[:100],
                        value=profile.id[:100],
                    )
                )
                if len(out) >= 25:
                    break
            return out

        for profile in self.profiles:
            slug = profile.id.strip().lower()
            if slug in RESERVED_SLASH or not slug.replace("-", "").isalnum():
                continue

            def _make_alias(target: AgentProfile) -> Any:
                @app_commands.describe(prompt="What this agent should do")
                async def _alias(interaction: discord.Interaction, prompt: str) -> None:
                    await self._slash_start(interaction, target, prompt)

                _alias.__name__ = f"cmd_{target.id}"
                return _alias

            self.tree.command(
                name=slug,
                description=f"Start {profile.display_name} (thread in its home channel)",
            )(_make_alias(profile))

        if self.has_manager:

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
        label = "+".join(p.id for p in self.profiles)
        guild = discord.Object(id=self.guild_id) if self.guild_id else None
        if guild:
            try:
                self.tree.copy_global_to(guild=guild)
                synced = await self.tree.sync(guild=guild)
                log.info(
                    "Slash commands guild-synced for %s (%s): %s",
                    label,
                    self.guild_id,
                    ", ".join(sorted(c.name for c in synced)) or "(none)",
                )
            except discord.Forbidden:
                log.warning(
                    "Guild sync failed for %s (bot not in server %s?) — using global sync",
                    label,
                    self.guild_id,
                )
                synced = await self.tree.sync()
                log.info(
                    "Slash commands global-synced for %s: %s",
                    label,
                    ", ".join(sorted(c.name for c in synced)) or "(none)",
                )
        else:
            synced = await self.tree.sync()
            log.info(
                "Slash commands global-synced for %s: %s",
                label,
                ", ".join(sorted(c.name for c in synced)) or "(none)",
            )

    async def on_ready(self) -> None:
        homes = ", ".join(
            f"{p.id}:{p.discord.agent_channel_id}"
            for p in self.profiles
            if p.discord is not None
        )
        log.info(
            "Discord ready: %s (%s) profiles=%s homes=%s",
            self.user,
            self.user.id if self.user else "?",
            ",".join(p.id for p in self.profiles),
            homes,
        )

    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot:
            return
        if not self.gateway.is_allowed(message.author.id):
            return

        profile = self._profile_for_channel(message.channel)
        if profile is None or profile.discord is None:
            return

        text = message.content.strip()
        if not text and not message.attachments:
            return

        channel = message.channel
        sess_key = self.gateway.session_key(profile.id, "discord", thread_key(channel))

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

        if (
            isinstance(channel, discord.TextChannel)
            and channel.id == profile.discord.agent_channel_id
        ):
            await self._open_thread_and_run(
                profile=profile,
                home=channel,
                author=message.author,
                prompt=prompt,
                saved=saved,
                title_src=title_src,
                preview_text=text,
            )
            return

        status_msg = await message.reply("⏳ Agent running…", mention_author=False)
        self._launch_job(
            profile=profile,
            sess_key=sess_key,
            prompt=prompt,
            status_msg=status_msg,
            mention=message.author.mention,
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
    for token, group in group_profiles_by_token(profiles):
        guild_id = next(
            (p.discord.guild_id for p in group if p.discord and p.discord.guild_id),
            gateway.config.discord_guild_id,
        )
        bot = AgentDiscordBot(gateway=gateway, profiles=group, guild_id=guild_id)
        if clients_out is not None:
            clients_out.append(bot)
        ids = "+".join(p.id for p in group)

        async def _run(
            client: AgentDiscordBot = bot, bot_token: str = token
        ) -> None:
            await client.start(bot_token)

        task = asyncio.create_task(_run(), name=f"discord-{ids}")
        tasks.append(task)
        log.info(
            "Discord client for %s (%d profile%s)",
            ids,
            len(group),
            "s" if len(group) != 1 else "",
        )

    await asyncio.gather(*tasks)
