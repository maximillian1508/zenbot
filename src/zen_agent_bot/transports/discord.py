from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

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
from ..gateway.router import Gateway, JobResult, title_from_prompt
from ..handoff import (
    HANDOFF_MAX_MESSAGES,
    HandoffError,
    format_handoff_prompt,
    format_transcript_lines,
)
from ..backend_select import parse_backend_arg
from ..notify import format_close_reply
from ..scheduler import cron_prompt
from ..sessions import ThreadSession
from ..util.rebuild import RebuildNotify, deliver_rebuild_notify_discord

if TYPE_CHECKING:
    from discord.abc import Messageable

log = logging.getLogger(__name__)

DISCORD_CHUNK = 1900
# Discord allows 10 attachments per message.
DISCORD_MAX_FILES = 10


def allowlist_mentions(user_ids: list[int]) -> str | None:
    ids = [int(i) for i in user_ids if i]
    if not ids:
        return None
    return " ".join(f"<@{i}>" for i in ids)


async def add_thread_users(
    thread: discord.Thread, user_ids: list[int] | None = None
) -> None:
    for uid in user_ids or []:
        try:
            await thread.add_user(discord.Object(id=int(uid)))
        except discord.HTTPException as exc:
            log.warning("Could not add user %s to thread %s: %s", uid, thread.id, exc)


async def start_public_thread(
    home: discord.TextChannel,
    *,
    name: str,
    starter: str,
    add_user_ids: list[int] | None = None,
) -> discord.Thread:
    """Public thread from a home-channel message so it shows in the channel list."""
    msg = await home.send(starter[:2000])
    thread = await msg.create_thread(name=name[:100], auto_archive_duration=10080)
    await add_thread_users(thread, add_user_ids)
    return thread


class AskManagerView(discord.ui.View):
    """One-click handoff to the manager from a specialist / General thread."""

    def __init__(self, bot: AgentDiscordBot) -> None:
        super().__init__(timeout=3600)
        self.bot = bot

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user and self.bot.gateway.is_allowed(interaction.user.id):
            return True
        if interaction.response.is_done():
            await interaction.followup.send("Not authorized.", ephemeral=True)
        else:
            await interaction.response.send_message("Not authorized.", ephemeral=True)
        return False

    @discord.ui.button(label="Ask Manager", style=discord.ButtonStyle.primary)
    async def ask_manager(
        self, interaction: discord.Interaction, button: discord.ui.Button[Any]
    ) -> None:
        await interaction.response.defer()
        manager = self.bot.gateway.agents.manager()
        if manager is None:
            await interaction.followup.send("No manager profile configured.", ephemeral=True)
            return
        try:
            thread = await self.bot.handoff_from_channel(
                source=interaction.channel,
                target=manager,
                author=interaction.user,
                note="",
            )
        except HandoffError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return
        try:
            await interaction.message.edit(view=None)
        except discord.HTTPException:
            pass
        self.stop()
        await interaction.followup.send(
            f"Handed off to **{manager.display_name}** → {thread.mention}"
        )


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


class CancelJobView(discord.ui.View):
    """Stop the in-flight job from the running status bubble."""

    def __init__(self, gateway: Gateway, session_key: str) -> None:
        super().__init__(timeout=None)
        self.gateway = gateway
        self.session_key = session_key

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user and self.gateway.is_allowed(interaction.user.id):
            return True
        if interaction.response.is_done():
            await interaction.followup.send("Not authorized.", ephemeral=True)
        else:
            await interaction.response.send_message("Not authorized.", ephemeral=True)
        return False

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.danger)
    async def cancel(
        self, interaction: discord.Interaction, button: discord.ui.Button[Any]
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        if await self.gateway.cancel_session(
            self.session_key, reason="stopped by Cancel"
        ):
            await interaction.followup.send(
                "Cancelling the running job…", ephemeral=True
            )
        else:
            await interaction.followup.send(
                "Nothing running in this thread.", ephemeral=True
            )
            try:
                await interaction.message.edit(view=None)
            except discord.HTTPException:
                pass
        self.stop()


class ApproveDenyView(discord.ui.View):
    """Accept / Deny a pending cursor-sdk tool approval."""

    def __init__(self, gateway: Gateway, session_key: str, approval_id: str) -> None:
        super().__init__(timeout=None)
        self.gateway = gateway
        self.session_key = session_key
        self.approval_id = approval_id
        self._decided = False

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user and self.gateway.is_allowed(interaction.user.id):
            return True
        if interaction.response.is_done():
            await interaction.followup.send("Not authorized.", ephemeral=True)
        else:
            await interaction.response.send_message("Not authorized.", ephemeral=True)
        return False

    async def _decide(
        self, interaction: discord.Interaction, *, allow: bool
    ) -> None:
        if self._decided:
            if interaction.response.is_done():
                await interaction.followup.send(
                    "Already resolved.", ephemeral=True
                )
            else:
                await interaction.response.send_message(
                    "Already resolved.", ephemeral=True
                )
            return
        self._decided = True
        await interaction.response.defer()
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = True
        try:
            await interaction.message.edit(view=self)
        except discord.HTTPException:
            pass
        ok = self.gateway.approvals.resolve(
            self.approval_id,
            allow=allow,
            reason=f"discord:{interaction.user.id if interaction.user else '?'}",
        )
        label = "Accepted" if allow else "Denied"
        if ok:
            await interaction.followup.send(f"{label}.", ephemeral=True)
        else:
            await interaction.followup.send(
                "Already resolved or expired.", ephemeral=True
            )
        try:
            await interaction.message.edit(view=None)
        except discord.HTTPException:
            pass
        self.stop()

    @discord.ui.button(label="Accept", style=discord.ButtonStyle.success)
    async def accept(
        self, interaction: discord.Interaction, button: discord.ui.Button[Any]
    ) -> None:
        await self._decide(interaction, allow=True)

    @discord.ui.button(label="Deny", style=discord.ButtonStyle.danger)
    async def deny(
        self, interaction: discord.Interaction, button: discord.ui.Button[Any]
    ) -> None:
        await self._decide(interaction, allow=False)


class SudoPasswordModal(discord.ui.Modal, title="sudo password"):
    """Ephemeral password entry for a blocked sudo command.

    The value goes straight to the waiting askpass helper — it is never
    stored, logged, or posted to the channel.
    """

    password: discord.ui.TextInput[Any] = discord.ui.TextInput(
        label="Password (sent privately to sudo)",
        style=discord.TextStyle.short,
        required=True,
        max_length=256,
    )

    def __init__(self, gateway: Gateway, sudo_id: str) -> None:
        super().__init__(timeout=170)
        self.gateway = gateway
        self.sudo_id = sudo_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        ok = self.gateway.approvals.resolve_sudo(
            self.sudo_id,
            password=str(self.password.value),
            reason=f"discord:{interaction.user.id}",
        )
        if ok:
            await interaction.response.send_message(
                "🔐 Password sent to sudo — continuing.", ephemeral=True
            )
        else:
            await interaction.response.send_message(
                "Prompt already resolved or expired.", ephemeral=True
            )


class SudoPromptView(discord.ui.View):
    """Enter password / Deny for a pending sudo askpass request."""

    def __init__(self, gateway: Gateway, session_key: str, sudo_id: str) -> None:
        super().__init__(timeout=None)
        self.gateway = gateway
        self.session_key = session_key
        self.sudo_id = sudo_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user and self.gateway.is_allowed(interaction.user.id):
            return True
        if interaction.response.is_done():
            await interaction.followup.send("Not authorized.", ephemeral=True)
        else:
            await interaction.response.send_message("Not authorized.", ephemeral=True)
        return False

    @discord.ui.button(label="Enter password", style=discord.ButtonStyle.primary)
    async def enter(
        self, interaction: discord.Interaction, button: discord.ui.Button[Any]
    ) -> None:
        await interaction.response.send_modal(
            SudoPasswordModal(self.gateway, self.sudo_id)
        )

    @discord.ui.button(label="Deny", style=discord.ButtonStyle.danger)
    async def deny(
        self, interaction: discord.Interaction, button: discord.ui.Button[Any]
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        self.gateway.approvals.resolve_sudo(
            self.sudo_id, password=None, reason=f"discord:{interaction.user.id}"
        )
        await interaction.followup.send("Denied — sudo will fail.", ephemeral=True)
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


async def send_files_reply(
    status_msg: discord.Message,
    paths: list[Path],
    *,
    per_message: int = DISCORD_MAX_FILES,
) -> None:
    """Upload files as replies to the job's status message.

    Batched because Discord caps attachments per message. Files are opened
    lazily per batch so a long list does not hold every descriptor at once.
    """
    for start in range(0, len(paths), per_message):
        batch = paths[start : start + per_message]
        handles = [discord.File(str(path), filename=path.name) for path in batch]
        try:
            await status_msg.reply(files=handles, mention_author=False)
        finally:
            for handle in handles:
                handle.close()


_AGENT_PAREN_ID = re.compile(r"\(([^)]+)\)\s*$")


def resolve_agent_name(raw: str, profiles: list[AgentProfile]) -> AgentProfile | None:
    """Match id, display name, or Discord autocomplete label (`Name (id)`)."""
    key = (raw or "").strip()
    if not key:
        return None
    lowered = key.lower()
    by_id = {p.id.lower(): p for p in profiles}
    if lowered in by_id:
        return by_id[lowered]
    m = _AGENT_PAREN_ID.search(key)
    if m:
        inner = m.group(1).strip().lower()
        if inner in by_id:
            return by_id[inner]
    for sep in (" — ", " · ", " - "):
        if sep in key:
            head = key.split(sep, 1)[0].strip().lower()
            if head in by_id:
                return by_id[head]
            break
    for profile in profiles:
        if profile.display_name.strip().lower() == lowered:
            return profile
    return None


RESERVED_SLASH = frozenset(
    {
        "new",
        "status",
        "cancel",
        "close",
        "model",
        "backend",
        "trust",
        "agents",
        "rebuild",
        "run",
        "schedule",
        "handoff",
    }
)


def thread_key(channel: discord.abc.GuildChannel | discord.Thread) -> str:
    return str(channel.id)


async def archive_agent_thread(channel: object) -> bool:
    """Archive a Discord thread. Returns True if archived (or already)."""
    if not isinstance(channel, discord.Thread):
        return False
    if channel.archived:
        return True
    try:
        await channel.edit(archived=True, reason="/close")
        return True
    except discord.HTTPException:
        log.warning("Failed to archive thread %s", getattr(channel, "id", "?"))
        return False


async def unarchive_agent_thread(channel: object) -> bool:
    """Unarchive so we can reply. Returns True if it was archived and is now open."""
    if not isinstance(channel, discord.Thread) or not channel.archived:
        return False
    try:
        await channel.edit(archived=False, reason="/close reply")
        return True
    except discord.HTTPException:
        log.warning("Failed to unarchive thread %s", getattr(channel, "id", "?"))
        return False


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


@dataclass(frozen=True)
class RouteMatch:
    profile: AgentProfile
    binding: dict[str, Any] | None = None


def resolve_channel_route(
    *,
    by_channel: dict[int, AgentProfile],
    agents_by_id: dict[str, AgentProfile],
    binding_lookup: Callable[[str, int], dict[str, Any] | None],
    channel_id: int,
    parent_id: int | None,
    transport: str = "discord",
) -> RouteMatch | None:
    """Home channels win; else match an enabled route binding on channel/parent."""
    home = resolve_agent_profile(
        by_channel, channel_id=channel_id, parent_id=parent_id
    )
    if home is not None:
        return RouteMatch(profile=home, binding=None)
    lookup_id = parent_id if parent_id is not None else channel_id
    binding = binding_lookup(transport, lookup_id)
    if binding is None:
        return None
    profile = agents_by_id.get(str(binding["agent_id"]))
    if profile is None:
        return None
    return RouteMatch(profile=profile, binding=binding)


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

    def _channel_ids(
        self, channel: discord.abc.Messageable | None
    ) -> tuple[int | None, int | None]:
        if channel is None:
            return None, None
        channel_id = getattr(channel, "id", None)
        if not isinstance(channel_id, int):
            return None, None
        parent_id: int | None = None
        if isinstance(channel, discord.Thread):
            parent = channel.parent
            if parent is not None:
                parent_id = parent.id
            elif channel.parent_id:
                parent_id = int(channel.parent_id)
        return channel_id, parent_id

    def _binding_lookup(self, transport: str, channel_id: int) -> dict[str, Any] | None:
        return self.gateway.config.db.binding_for_channel(transport, channel_id)

    def _resolve_route(
        self, channel: discord.abc.Messageable | None
    ) -> RouteMatch | None:
        channel_id, parent_id = self._channel_ids(channel)
        if channel_id is None:
            return None
        fleet = {p.id: p for p in self.gateway.agents.all()}
        fleet.update(self.by_id)
        return resolve_channel_route(
            by_channel=self.by_channel,
            agents_by_id=fleet,
            binding_lookup=self._binding_lookup,
            channel_id=channel_id,
            parent_id=parent_id,
        )

    def _profile_for_channel(
        self,
        channel: discord.abc.Messageable | None,
    ) -> AgentProfile | None:
        route = self._resolve_route(channel)
        return route.profile if route else None

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
                "post in that agent's home channel, or a bound route channel.",
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

    def _resolve_agent(self, raw: str) -> AgentProfile | None:
        found = resolve_agent_name(raw, self.profiles)
        if found is not None:
            return found
        return resolve_agent_name(raw, self.gateway.agents.all())

    def _agent_choices(self, current: str) -> list[app_commands.Choice[str]]:
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

    async def _thread_transcript(self, thread: discord.Thread) -> str:
        rows: list[tuple[str, str]] = []
        async for msg in thread.history(limit=HANDOFF_MAX_MESSAGES, oldest_first=False):
            content = (msg.content or "").strip()
            if not content or content.startswith("⏳ Agent running"):
                continue
            name = msg.author.display_name if msg.author else "unknown"
            if msg.author and msg.author.bot:
                name = f"{name} [bot]"
            rows.append((name, content))
        rows.reverse()
        return format_transcript_lines(rows)

    async def handoff_from_channel(
        self,
        *,
        source: object,
        target: AgentProfile,
        author: discord.abc.User,
        note: str,
        backend: str | None = None,
        model: str | None = None,
    ) -> discord.Thread:
        if not isinstance(source, discord.Thread):
            raise HandoffError("Use `/handoff` inside a thread (the one you want to pass on).")
        if target.discord is None:
            raise HandoffError(f"**{target.display_name}** has no Discord channel.")
        await unarchive_agent_thread(source)
        home = await self._home_channel(target)
        if home is None:
            raise HandoffError(
                f"Can't open **{target.display_name}**'s home channel "
                f"(id `{target.discord.agent_channel_id}`)."
            )
        source_profile = self._profile_for_channel(source)
        source_agent = source_profile.id if source_profile else "unknown"
        source_title = source.name or "(untitled)"
        source_url = str(getattr(source, "jump_url", "") or "")
        transcript = await self._thread_transcript(source)
        prompt = format_handoff_prompt(
            source_agent=source_agent,
            source_title=source_title,
            source_url=source_url,
            target_display=target.display_name,
            note=note,
            transcript=transcript,
        )
        title_src = note.strip() or source_title
        title = title_from_prompt(f"handoff · {title_src}")
        starter = (
            f"↪️ **Handoff** from {source.mention} → **{target.display_name}** "
            f"{author.mention}"
        )
        ids = [author.id]
        thread = await start_public_thread(
            home, name=title, starter=starter, add_user_ids=ids
        )
        extra = "…" if len(prompt) > 500 else ""
        await thread.send(f"{prompt[:500]}{extra}")
        sess_key = self.gateway.session_key(target.id, "discord", thread_key(thread))
        self.gateway.store.set(sess_key, ThreadSession(session_id=None, title=title))
        if backend:
            self.gateway.store.set_backend(sess_key, backend)
        if model:
            self.gateway.store.set_model(sess_key, model)
        status_msg = await thread.send("⏳ Agent running…")
        self._launch_job(
            profile=target,
            sess_key=sess_key,
            prompt=prompt,
            status_msg=status_msg,
            mention=getattr(author, "mention", None),
        )
        return thread

    async def handoff_to_existing(
        self,
        *,
        source: object,
        dest: discord.Thread,
        author: discord.abc.User,
        note: str,
    ) -> discord.Thread:
        """Transfer this thread's transcript into an existing agent thread.

        The destination keeps its own agent, session (`--resume`), backend, and
        model — the transcript just arrives as the next message in it.
        """
        if not isinstance(source, discord.Thread):
            raise HandoffError("Use `/handoff` inside a thread (the one you want to pass on).")
        if dest.id == source.id:
            raise HandoffError("Target thread is this same thread.")
        target = self._profile_for_channel(dest)
        if target is None:
            raise HandoffError(
                f"{dest.mention} isn't an agent thread (no agent owns its channel)."
            )
        await unarchive_agent_thread(source)
        await unarchive_agent_thread(dest)
        source_profile = self._profile_for_channel(source)
        transcript = await self._thread_transcript(source)
        prompt = format_handoff_prompt(
            source_agent=source_profile.id if source_profile else "unknown",
            source_title=source.name or "(untitled)",
            source_url=str(getattr(source, "jump_url", "") or ""),
            target_display=target.display_name,
            note=note,
            transcript=transcript,
        )
        sess_key = self.gateway.session_key(target.id, "discord", thread_key(dest))
        await dest.send(
            f"↪️ **Context transfer** from {source.mention} {author.mention}"
        )
        status_msg = await dest.send("⏳ Agent running…")
        self._launch_job(
            profile=target,
            sess_key=sess_key,
            prompt=prompt,
            status_msg=status_msg,
            mention=getattr(author, "mention", None),
        )
        return dest

    def _launch_job(
        self,
        *,
        profile: AgentProfile,
        sess_key: str,
        prompt: str,
        status_msg: discord.Message,
        mention: str | None,
        schedule_id: str | None = None,
        on_done: Any = None,
        binding: dict[str, Any] | None = None,
    ) -> None:
        workspace_override: Path | None = None
        if binding and binding.get("workspace"):
            workspace_override = Path(str(binding["workspace"])).expanduser()
        if binding and binding.get("backend"):
            self.gateway.store.set_backend(sess_key, str(binding["backend"]))
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

        async def send_files(
            paths: list[Path],
            _session_key: str,
            *,
            _status: discord.Message = status_msg,
        ) -> None:
            await send_files_reply(_status, paths)

        async def on_queued(job_id: str, *, _status: discord.Message = status_msg) -> None:
            qview = QueueJobView(self.gateway, sess_key, job_id)
            await _status.edit(view=qview)

        done_view = None
        if not profile.is_manager and self.has_manager:
            done_view = AskManagerView(self)

        asyncio.create_task(
            self.gateway.run_job(
                agent_id=profile.id,
                session_key=sess_key,
                user_prompt=prompt,
                send=send,
                edit_status=edit_status,
                send_files=send_files,
                on_queued=on_queued,
                notify_mention=mention,
                schedule_id=schedule_id,
                on_done=on_done,
                done_view=done_view,
                running_view=CancelJobView(self.gateway, sess_key),
                workspace_override=workspace_override,
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
        binding: dict[str, Any] | None = None,
        backend: str | None = None,
        model: str | None = None,
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
        if backend:
            self.gateway.store.set_backend(sess_key, backend)
        if model:
            self.gateway.store.set_model(sess_key, model)
        status_msg = await thread.send("⏳ Agent running…")
        self._launch_job(
            profile=profile,
            sess_key=sess_key,
            prompt=prompt,
            status_msg=status_msg,
            mention=getattr(author, "mention", None),
            binding=binding,
        )
        return thread

    def _is_route_wake(
        self,
        channel: discord.abc.Messageable,
        route: RouteMatch,
    ) -> bool:
        if not isinstance(channel, discord.TextChannel):
            return False
        if route.binding is not None:
            return str(channel.id) == str(route.binding["channel_id"])
        if route.profile.discord is None:
            return False
        return channel.id == route.profile.discord.agent_channel_id

    async def launch_cron_run(
        self,
        *,
        agent_id: str,
        schedule_id: str,
        name: str,
        prompt: str,
        cron_expr: str,
        last_thread_id: str | None = None,
    ) -> dict[str, str | None]:
        if not self.is_ready():
            raise RuntimeError("Discord client not ready")
        profile = self.by_id.get(agent_id)
        if profile is None or profile.discord is None:
            raise RuntimeError(f"Agent {agent_id} has no Discord binding on this bot")
        home = await self._home_channel(profile)
        if home is None:
            raise RuntimeError(f"Home channel missing for {agent_id}")
        ids = self.gateway.config.db.allowlist()
        mention = allowlist_mentions(ids)
        title = f"cron · {name}"[:100]
        thread = await self._reuse_cron_thread(home, last_thread_id)
        reused = thread is not None
        if thread is None:
            starter = f"⏰ **Scheduled** `{name}` · `{cron_expr}`"
            if mention:
                starter = f"{starter} {mention}"
            thread = await start_public_thread(
                home, name=title, starter=starter, add_user_ids=ids
            )
        else:
            if thread.name != title:
                try:
                    await thread.edit(name=title, reason="cron schedule name")
                except discord.HTTPException:
                    pass
            await add_thread_users(thread, ids)
        body = prompt.strip()
        extra = "…" if len(body) > 500 else ""
        header = f"⏰ **Scheduled** `{name}` · `{schedule_id}` · `{cron_expr}`"
        if mention:
            header = f"{header} {mention}"
        await thread.send(f"{header}\n\n{body[:500]}{extra}")
        sess_key = self.gateway.session_key(profile.id, "discord", thread_key(thread))
        if not reused:
            self.gateway.store.set(sess_key, ThreadSession(session_id=None, title=title))
        status_msg = await thread.send("⏳ Agent running…")

        async def _on_done(result: JobResult, sid: str = schedule_id) -> None:
            sched = self.gateway.scheduler
            if sched is not None:
                sched.on_job_done(sid, result)

        self._launch_job(
            profile=profile,
            sess_key=sess_key,
            prompt=cron_prompt(
                name=name, cron_expr=cron_expr, prompt=prompt, reused=reused
            ),
            status_msg=status_msg,
            mention=mention,
            schedule_id=schedule_id,
            on_done=_on_done,
        )
        url = getattr(thread, "jump_url", None)
        return {
            "thread_id": str(thread.id),
            "session_key": sess_key,
            "thread_url": str(url) if url else None,
        }

    async def _reuse_cron_thread(
        self,
        home: discord.TextChannel,
        last_thread_id: str | None,
    ) -> discord.Thread | None:
        """Return the schedule's existing thread if it still lives in this home channel."""
        if not last_thread_id:
            return None
        try:
            tid = int(last_thread_id)
        except (TypeError, ValueError):
            return None
        ch = self.get_channel(tid)
        if ch is None:
            try:
                ch = await self.fetch_channel(tid)
            except discord.HTTPException:
                log.info("Cron thread %s missing; will create a new one", last_thread_id)
                return None
        if not isinstance(ch, discord.Thread) or ch.parent_id != home.id:
            return None
        if ch.archived:
            try:
                await ch.edit(archived=False, locked=False, reason="cron")
            except discord.HTTPException:
                log.warning(
                    "Could not unarchive cron thread %s; creating a new one", tid
                )
                return None
        return ch

    def _parse_backend_opt(self, raw: str | None) -> str | None:
        """Canonical backend id from a slash option, or None. Raises ValueError."""
        if not raw or not raw.strip():
            return None
        action, value = parse_backend_arg(raw, known=self.gateway.known_backends())
        if action != "set" or not value:
            raise ValueError(f"Invalid backend `{raw}`.")
        return value

    def _backend_choices(self, current: str) -> list[app_commands.Choice[str]]:
        q = current.lower().strip()
        return [
            app_commands.Choice(name=name, value=name)
            for name in sorted(self.gateway.known_backends())
            if not q or q in name
        ][:25]

    async def _model_choices_for_backend(
        self, backend: str, current: str
    ) -> list[app_commands.Choice[str]]:
        q = current.lower().strip()
        try:
            models = await self.gateway.models_for_backend(backend)
        except Exception:
            models = []
        choices: list[app_commands.Choice[str]] = []
        for mid, label in models:
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

    def _start_backend_for(
        self, interaction: discord.Interaction, profile: AgentProfile
    ) -> str:
        """Backend a new slash-started thread would use: typed option or profile default."""
        raw = getattr(interaction.namespace, "backend", None)
        try:
            picked = self._parse_backend_opt(raw)
        except ValueError:
            picked = None
        return picked or profile.default_backend or "cursor-cli"

    async def _slash_start(
        self,
        interaction: discord.Interaction,
        profile: AgentProfile,
        prompt: str,
        backend: str | None = None,
        model: str | None = None,
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
        try:
            backend = self._parse_backend_opt(backend)
        except ValueError as exc:
            await interaction.response.send_message(f"⚠️ {exc}", ephemeral=True)
            return
        model = (model or "").strip() or None
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
            backend=backend,
            model=model,
        )
        overrides = " · ".join(
            f"`{v}`" for v in (backend, model) if v
        )
        note = f" ({overrides})" if overrides else ""
        await interaction.edit_original_response(
            content=f"Started **{profile.display_name}** in {thread.mention}{note}"
        )

    async def setup_hook(self) -> None:
        for profile in self.profiles:
            self.gateway.register_cron_launcher(profile.id, self.launch_cron_run)

        @self.tree.command(name="new", description="Start a fresh agent session in this thread")
        async def cmd_new(interaction: discord.Interaction) -> None:
            ctx = await self._require_ctx(interaction)
            if ctx is None:
                return
            profile, channel = ctx
            key = self.gateway.session_key(profile.id, "discord", thread_key(channel))
            self.gateway.reset_session_resume(key)
            await interaction.response.send_message(
                "New session. Your next message here starts fresh "
                "(no `--resume` / OpenRouter chat history). "
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
            msg += "\n" + self.gateway.apply_trust_command(
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
            description="Archive this thread; keep Cursor --resume",
        )
        async def cmd_close(interaction: discord.Interaction) -> None:
            ctx = await self._require_ctx(interaction)
            if ctx is None:
                return
            profile, channel = ctx
            # Discord 50083: cannot respond to an archived thread. Unarchive
            # first, reply, then archive — never archive before the ack.
            await unarchive_agent_thread(channel)
            try:
                await interaction.response.defer()
            except discord.HTTPException:
                log.exception("Failed to defer /close (thread archived?)")
                return
            key = self.gateway.session_key(profile.id, "discord", thread_key(channel))
            info = await self.gateway.close_session(key)
            will_archive = isinstance(channel, discord.Thread)
            await interaction.followup.send(
                format_close_reply(
                    cancelled=bool(info["cancelled"]),
                    dropped=int(info["dropped"]),
                    archived=will_archive,
                )
            )
            if will_archive:
                await archive_agent_thread(channel)

        @self.tree.command(
            name="model",
            description="Show models for this thread's backend or set one",
        )
        @app_commands.describe(
            name="Model id for the thread's backend, or clear/default"
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
                backend = "cursor-cli"
                channel = interaction.channel
                profile = self._profile_for_channel(channel) if channel else None
                if profile is not None and channel is not None:
                    key = self.gateway.session_key(
                        profile.id, "discord", thread_key(channel)
                    )
                    backend = self.gateway.resolved_backend(profile.id, key).backend
                models = await self.gateway.models_for_backend(backend)
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
            name="cursor-cli, cursor-sdk, claude-cli, openrouter, or clear/default"
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
            name="trust",
            description="Show or set trust mode for cursor-sdk in this thread",
        )
        @app_commands.describe(name="force, approve, or clear/default")
        async def cmd_trust(
            interaction: discord.Interaction, name: str | None = None
        ) -> None:
            ctx = await self._require_ctx(interaction)
            if ctx is None:
                return
            profile, channel = ctx
            key = self.gateway.session_key(profile.id, "discord", thread_key(channel))
            msg = self.gateway.apply_trust_command(
                session_key=key,
                agent_id=profile.id,
                raw=name,
            )
            await interaction.response.send_message(msg, ephemeral=True)

        @cmd_trust.autocomplete("name")
        async def trust_autocomplete(
            interaction: discord.Interaction, current: str
        ) -> list[app_commands.Choice[str]]:
            if not interaction.user or not self.gateway.is_allowed(interaction.user.id):
                return []
            q = current.lower().strip()
            rows = [
                ("force", "tools auto-run (default)"),
                ("approve", "Discord Accept/Deny via hooks"),
                ("clear", "use backend default"),
                ("default", "use backend default"),
            ]
            choices: list[app_commands.Choice[str]] = []
            for mode, label in rows:
                hay = f"{mode} {label}".lower()
                if q and q not in hay:
                    continue
                choices.append(
                    app_commands.Choice(name=f"{label} · {mode}"[:100], value=mode[:100])
                )
                if len(choices) >= 25:
                    break
            return choices

        @self.tree.command(
            name="run",
            description="Start a job as a specialist (opens a thread in that agent's channel)",
        )
        @app_commands.describe(
            agent="Profile id",
            prompt="What the agent should do",
            backend="Backend for the new thread (optional)",
            model="Model for the new thread (optional)",
        )
        async def cmd_run(
            interaction: discord.Interaction,
            agent: str,
            prompt: str,
            backend: str | None = None,
            model: str | None = None,
        ) -> None:
            target = self._resolve_agent(agent)
            if target is None:
                await interaction.response.send_message(
                    f"Unknown agent `{agent}`. Try `/agents`.",
                    ephemeral=True,
                )
                return
            await self._slash_start(interaction, target, prompt, backend, model)

        @cmd_run.autocomplete("agent")
        async def run_autocomplete(
            interaction: discord.Interaction, current: str
        ) -> list[app_commands.Choice[str]]:
            if not interaction.user or not self.gateway.is_allowed(interaction.user.id):
                return []
            return self._agent_choices(current)

        @cmd_run.autocomplete("backend")
        async def run_backend_autocomplete(
            interaction: discord.Interaction, current: str
        ) -> list[app_commands.Choice[str]]:
            if not interaction.user or not self.gateway.is_allowed(interaction.user.id):
                return []
            return self._backend_choices(current)

        @cmd_run.autocomplete("model")
        async def run_model_autocomplete(
            interaction: discord.Interaction, current: str
        ) -> list[app_commands.Choice[str]]:
            if not interaction.user or not self.gateway.is_allowed(interaction.user.id):
                return []
            target = self._resolve_agent(
                str(getattr(interaction.namespace, "agent", "") or "")
            )
            if target is None:
                return []
            backend = self._start_backend_for(interaction, target)
            return await self._model_choices_for_backend(backend, current)

        @self.tree.command(
            name="handoff",
            description="Send this thread's context to another agent or an existing thread",
        )
        @app_commands.describe(
            agent="Who continues in a NEW thread (manager, general, music, …)",
            to_thread="…or an EXISTING agent thread to receive the context",
            note="Optional extra instruction for them",
            backend="Backend for the new thread (optional; new-thread handoff only)",
            model="Model for the new thread (optional; new-thread handoff only)",
        )
        async def cmd_handoff(
            interaction: discord.Interaction,
            agent: str | None = None,
            to_thread: discord.Thread | None = None,
            note: str | None = None,
            backend: str | None = None,
            model: str | None = None,
        ) -> None:
            if not interaction.user or not self.gateway.is_allowed(interaction.user.id):
                await interaction.response.send_message("Not authorized.", ephemeral=True)
                return
            if (agent is None) == (to_thread is None):
                await interaction.response.send_message(
                    "Pick exactly one destination: `agent:` (new thread) or "
                    "`to_thread:` (existing thread).",
                    ephemeral=True,
                )
                return
            if to_thread is not None:
                if backend or model:
                    await interaction.response.send_message(
                        "`backend:`/`model:` only apply to new-thread handoffs — "
                        "the existing thread keeps its own settings "
                        "(use `/backend` / `/model` there).",
                        ephemeral=True,
                    )
                    return
                await interaction.response.defer()
                try:
                    dest = await self.handoff_to_existing(
                        source=interaction.channel,
                        dest=to_thread,
                        author=interaction.user,
                        note=note or "",
                    )
                except HandoffError as exc:
                    await interaction.followup.send(str(exc), ephemeral=True)
                    return
                await interaction.followup.send(
                    f"Context sent to {dest.mention}"
                )
                return
            target = self._resolve_agent(agent or "")
            if target is None:
                await interaction.response.send_message(
                    f"Unknown agent `{agent}`. Try `/agents`.",
                    ephemeral=True,
                )
                return
            try:
                picked_backend = self._parse_backend_opt(backend)
            except ValueError as exc:
                await interaction.response.send_message(f"⚠️ {exc}", ephemeral=True)
                return
            await interaction.response.defer()
            try:
                thread = await self.handoff_from_channel(
                    source=interaction.channel,
                    target=target,
                    author=interaction.user,
                    note=note or "",
                    backend=picked_backend,
                    model=(model or "").strip() or None,
                )
            except HandoffError as exc:
                await interaction.followup.send(str(exc), ephemeral=True)
                return
            overrides = " · ".join(
                f"`{v}`" for v in (picked_backend, (model or "").strip()) if v
            )
            suffix = f" ({overrides})" if overrides else ""
            await interaction.followup.send(
                f"Handed off to **{target.display_name}** → {thread.mention}{suffix}"
            )

        @cmd_handoff.autocomplete("backend")
        async def handoff_backend_autocomplete(
            interaction: discord.Interaction, current: str
        ) -> list[app_commands.Choice[str]]:
            if not interaction.user or not self.gateway.is_allowed(interaction.user.id):
                return []
            return self._backend_choices(current)

        @cmd_handoff.autocomplete("model")
        async def handoff_model_autocomplete(
            interaction: discord.Interaction, current: str
        ) -> list[app_commands.Choice[str]]:
            if not interaction.user or not self.gateway.is_allowed(interaction.user.id):
                return []
            target = self._resolve_agent(
                str(getattr(interaction.namespace, "agent", "") or "")
            )
            if target is None:
                return []
            backend = self._start_backend_for(interaction, target)
            return await self._model_choices_for_backend(backend, current)

        @cmd_handoff.autocomplete("agent")
        async def handoff_autocomplete(
            interaction: discord.Interaction, current: str
        ) -> list[app_commands.Choice[str]]:
            if not interaction.user or not self.gateway.is_allowed(interaction.user.id):
                return []
            return self._agent_choices(current)

        for profile in self.profiles:
            slug = profile.id.strip().lower()
            if slug in RESERVED_SLASH or not slug.replace("-", "").isalnum():
                continue

            def _make_alias(target: AgentProfile) -> Any:
                @app_commands.describe(
                    prompt="What this agent should do",
                    backend="Backend for the new thread (optional)",
                    model="Model for the new thread (optional)",
                )
                async def _alias(
                    interaction: discord.Interaction,
                    prompt: str,
                    backend: str | None = None,
                    model: str | None = None,
                ) -> None:
                    await self._slash_start(interaction, target, prompt, backend, model)

                _alias.__name__ = f"cmd_{target.id}"
                return _alias

            def _make_alias_autocompletes(target: AgentProfile, command: Any) -> None:
                @command.autocomplete("backend")
                async def _alias_backend_ac(
                    interaction: discord.Interaction, current: str
                ) -> list[app_commands.Choice[str]]:
                    if not interaction.user or not self.gateway.is_allowed(
                        interaction.user.id
                    ):
                        return []
                    return self._backend_choices(current)

                @command.autocomplete("model")
                async def _alias_model_ac(
                    interaction: discord.Interaction, current: str
                ) -> list[app_commands.Choice[str]]:
                    if not interaction.user or not self.gateway.is_allowed(
                        interaction.user.id
                    ):
                        return []
                    backend = self._start_backend_for(interaction, target)
                    return await self._model_choices_for_backend(backend, current)

            alias_cmd = self.tree.command(
                name=slug,
                description=f"Start {profile.display_name} (thread in its home channel)",
            )(_make_alias(profile))
            _make_alias_autocompletes(profile, alias_cmd)

        if self.has_manager:

            @self.tree.command(
                name="schedule",
                description="List cron schedules, or run one now",
            )
            @app_commands.describe(
                action="list (default) or run",
                id="Schedule id (for run)",
            )
            async def cmd_schedule(
                interaction: discord.Interaction,
                action: str | None = None,
                id: str | None = None,
            ) -> None:
                if not interaction.user or not self.gateway.is_allowed(interaction.user.id):
                    await interaction.response.send_message("Not authorized.", ephemeral=True)
                    return
                verb = (action or "list").strip().lower()
                if verb in ("", "list", "ls"):
                    await interaction.response.send_message(
                        self.gateway.schedules_markdown(),
                        ephemeral=True,
                    )
                    return
                if verb != "run":
                    await interaction.response.send_message(
                        "Use `/schedule` or `/schedule action:run id:<id>`.",
                        ephemeral=True,
                    )
                    return
                sid = (id or "").strip()
                if not sid:
                    await interaction.response.send_message(
                        "Pass `id` of the schedule to run.",
                        ephemeral=True,
                    )
                    return
                sched = self.gateway.scheduler
                if sched is None:
                    await interaction.response.send_message(
                        "Scheduler is not running.", ephemeral=True
                    )
                    return
                await interaction.response.defer(ephemeral=True)
                result = await sched.fire(sid, force=True)
                await interaction.followup.send(f"Schedule `{sid}`: **{result}**")

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
                channel = interaction.channel
                path = self.gateway.request_rebuild(
                    reason=f"discord:/rebuild by {interaction.user.id}",
                    notify=RebuildNotify(
                        transport="discord",
                        channel_id=str(channel.id if channel else interaction.channel_id),
                        user_id=str(interaction.user.id),
                        mention=interaction.user.mention,
                        agent_id="manager",
                    ),
                )
                await interaction.response.send_message(
                    "Restart requested. Host will `systemctl restart` in ~15s "
                    f"(flag `{path.name}`). I'll ping you here when `/health` is OK."
                )

        @self.tree.error
        async def on_app_command_error(
            interaction: discord.Interaction,
            error: app_commands.AppCommandError,
        ) -> None:
            log.exception(
                "Slash command %s failed",
                getattr(interaction.command, "name", "?"),
            )
            hint = "Command failed — check gateway logs."
            err = str(error).lower()
            if "50083" in err or "archived" in err:
                hint = (
                    "This thread is archived. Send a message here to unarchive, "
                    "then retry the command."
                )
            try:
                if interaction.response.is_done():
                    await interaction.followup.send(hint, ephemeral=True)
                else:
                    await interaction.response.send_message(hint, ephemeral=True)
            except Exception:
                log.exception("Failed to report slash error to Discord")

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
        await deliver_rebuild_notify_discord(self, self.gateway.config.data_dir)

    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot:
            return
        if not self.gateway.is_allowed(message.author.id):
            return

        route = self._resolve_route(message.channel)
        if route is None:
            return
        profile = route.profile
        if route.binding is None and profile.discord is None:
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

        if self._is_route_wake(channel, route):
            await self._open_thread_and_run(
                profile=profile,
                home=channel,  # type: ignore[arg-type]
                author=message.author,
                prompt=prompt,
                saved=saved,
                title_src=title_src,
                preview_text=text,
                binding=route.binding,
            )
            return

        status_msg = await message.reply("⏳ Agent running…", mention_author=False)
        self._launch_job(
            profile=profile,
            sess_key=sess_key,
            prompt=prompt,
            status_msg=status_msg,
            mention=message.author.mention,
            binding=route.binding,
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

    gateway.set_approval_view_factory(
        lambda session_key, approval_id: ApproveDenyView(
            gateway, session_key, approval_id
        )
    )
    gateway.set_sudo_view_factory(
        lambda session_key, sudo_id: SudoPromptView(gateway, session_key, sudo_id)
    )

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
