from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from ..agents.profile import AgentProfile
from ..attachments import (
    DEFAULT_MAX_BYTES,
    DEFAULT_MAX_FILES,
    SavedAttachment,
    attachments_dir,
    merge_prompt_with_attachments,
    write_attachment,
)
from ..gateway.router import Gateway

log = logging.getLogger(__name__)

TELEGRAM_CHUNK = 4000

# Text and/or common file types (caption provides text for media).
_TG_CONTENT = (
    filters.TEXT
    | filters.CAPTION
    | filters.Document.ALL
    | filters.PHOTO
    | filters.VIDEO
    | filters.AUDIO
    | filters.VOICE
    | filters.ANIMATION
) & ~filters.COMMAND


async def send_chunks(reply_fn, text: str) -> None:  # noqa: ANN001
    if len(text) <= TELEGRAM_CHUNK:
        await reply_fn(text)
        return
    start = 0
    while start < len(text):
        await reply_fn(text[start : start + TELEGRAM_CHUNK])
        start += TELEGRAM_CHUNK


async def _download_tg_file(
    bot,  # noqa: ANN001
    *,
    file_id: str,
    dest_dir: Path,
    filename: str,
    content_type: str | None,
) -> SavedAttachment | None:
    try:
        tg_file = await bot.get_file(file_id)
        data = bytes(await tg_file.download_as_bytearray())
    except Exception:
        log.exception("Failed to download Telegram file %s", file_id)
        return None
    return await write_attachment(
        dest_dir,
        filename=filename,
        data=data,
        content_type=content_type,
        original_name=filename,
    )


async def save_telegram_attachments(
    message,  # noqa: ANN001
    bot,  # noqa: ANN001
    dest_dir: Path,
) -> list[SavedAttachment]:
    saved: list[SavedAttachment] = []

    async def add(
        file_id: str,
        filename: str,
        content_type: str | None,
        size: int | None = None,
    ) -> None:
        if len(saved) >= DEFAULT_MAX_FILES:
            return
        if size is not None and size > DEFAULT_MAX_BYTES:
            log.warning("Skip Telegram file %s — too large (%s)", filename, size)
            return
        item = await _download_tg_file(
            bot,
            file_id=file_id,
            dest_dir=dest_dir,
            filename=filename,
            content_type=content_type,
        )
        if item:
            saved.append(item)

    if message.document:
        doc = message.document
        await add(
            doc.file_id,
            doc.file_name or f"document-{doc.file_unique_id}",
            doc.mime_type,
            doc.file_size,
        )
    if message.photo:
        # Largest size last
        photo = message.photo[-1]
        await add(
            photo.file_id,
            f"photo-{photo.file_unique_id}.jpg",
            "image/jpeg",
            photo.file_size,
        )
    if message.video:
        vid = message.video
        await add(
            vid.file_id,
            vid.file_name or f"video-{vid.file_unique_id}.mp4",
            vid.mime_type or "video/mp4",
            vid.file_size,
        )
    if message.audio:
        audio = message.audio
        await add(
            audio.file_id,
            audio.file_name or f"audio-{audio.file_unique_id}",
            audio.mime_type,
            audio.file_size,
        )
    if message.voice:
        voice = message.voice
        await add(
            voice.file_id,
            f"voice-{voice.file_unique_id}.ogg",
            voice.mime_type or "audio/ogg",
            voice.file_size,
        )
    if message.animation:
        anim = message.animation
        await add(
            anim.file_id,
            anim.file_name or f"animation-{anim.file_unique_id}.mp4",
            anim.mime_type,
            anim.file_size,
        )
    return saved


class TelegramAgentApp:
    def __init__(self, *, gateway: Gateway, profile: AgentProfile) -> None:
        self.gateway = gateway
        self.profile = profile
        assert profile.telegram is not None
        self.binding = profile.telegram
        self.agent_id = profile.id
        self._status_msg_id: dict[int, int] = {}

    def build(self) -> Application:
        app = Application.builder().token(self.binding.token).build()
        app.add_handler(CommandHandler("new", self.cmd_new))
        app.add_handler(CommandHandler("status", self.cmd_status))
        app.add_handler(CommandHandler("cancel", self.cmd_cancel))
        app.add_handler(CommandHandler("model", self.cmd_model))
        app.add_handler(CommandHandler("backend", self.cmd_backend))
        if self.profile.is_manager:
            app.add_handler(CommandHandler("agents", self.cmd_agents))
            app.add_handler(CommandHandler("rebuild", self.cmd_rebuild))
        app.add_handler(MessageHandler(_TG_CONTENT, self.on_message))
        return app

    def _chat_allowed(self, chat_id: int) -> bool:
        if self.binding.agent_chat_id is None:
            return True
        return chat_id == self.binding.agent_chat_id

    def _session_key(self, chat_id: int, thread_id: int | None) -> str:
        channel = f"{chat_id}:{thread_id}" if thread_id else str(chat_id)
        return self.gateway.session_key(self.agent_id, "telegram", channel)

    async def cmd_new(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.effective_user or not update.effective_chat:
            return
        if not self.gateway.is_allowed(update.effective_user.id):
            await update.message.reply_text("Not authorized.")  # type: ignore[union-attr]
            return
        key = self._session_key(update.effective_chat.id, update.message.message_thread_id if update.message else None)  # type: ignore[union-attr]
        self.gateway.reset_session_resume(key)
        await update.message.reply_text(  # type: ignore[union-attr]
            "New session. Next message starts fresh. /model and /backend overrides are kept."
        )

    async def cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.effective_user or not update.effective_chat or not update.message:
            return
        if not self.gateway.is_allowed(update.effective_user.id):
            await update.message.reply_text("Not authorized.")
            return
        key = self._session_key(update.effective_chat.id, update.message.message_thread_id)
        sess = self.gateway.get_session(key)
        if sess.session_id:
            msg = f"Session: `{sess.session_id}`"
            if sess.title:
                msg += f"\nTitle: {sess.title}"
        else:
            msg = "No session yet."
        msg += "\n\n" + await self.gateway.apply_model_command(
            session_key=key, agent_id=self.agent_id, raw=None
        )
        await update.message.reply_text(msg, parse_mode="Markdown")

    async def cmd_cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.effective_user or not update.effective_chat or not update.message:
            return
        if not self.gateway.is_allowed(update.effective_user.id):
            await update.message.reply_text("Not authorized.")
            return
        key = self._session_key(update.effective_chat.id, update.message.message_thread_id)
        if await self.gateway.cancel_session(key):
            await update.message.reply_text("Cancelling the running job…")
        else:
            await update.message.reply_text("Nothing running in this thread.")

    async def cmd_model(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.effective_user or not update.effective_chat or not update.message:
            return
        if not self.gateway.is_allowed(update.effective_user.id):
            await update.message.reply_text("Not authorized.")
            return
        key = self._session_key(update.effective_chat.id, update.message.message_thread_id)
        raw = " ".join(context.args) if context.args else None
        include_catalog = not raw or raw.strip().lower() in {"list", "ls"}
        msg = await self.gateway.apply_model_command(
            session_key=key,
            agent_id=self.agent_id,
            raw=raw,
            include_catalog=include_catalog,
            catalog_max_chars=2800,
        )
        await update.message.reply_text(msg, parse_mode="Markdown")

    async def cmd_backend(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.effective_user or not update.effective_chat or not update.message:
            return
        if not self.gateway.is_allowed(update.effective_user.id):
            await update.message.reply_text("Not authorized.")
            return
        key = self._session_key(update.effective_chat.id, update.message.message_thread_id)
        raw = " ".join(context.args) if context.args else None
        include_catalog = not raw or raw.strip().lower() in {"list", "ls"}
        msg = self.gateway.apply_backend_command(
            session_key=key,
            agent_id=self.agent_id,
            raw=raw,
            include_catalog=include_catalog,
        )
        await update.message.reply_text(msg, parse_mode="Markdown")

    async def cmd_agents(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.effective_user or not update.message:
            return
        if not self.gateway.is_allowed(update.effective_user.id):
            await update.message.reply_text("Not authorized.")
            return
        await update.message.reply_text(self.gateway.list_agents_markdown())

    async def cmd_rebuild(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.effective_user or not update.message:
            return
        if not self.gateway.is_allowed(update.effective_user.id):
            await update.message.reply_text("Not authorized.")
            return
        if self.gateway.rebuild_pending():
            await update.message.reply_text("Rebuild already requested — waiting for host.")
            return
        self.gateway.request_rebuild(
            reason=f"telegram:/rebuild by {update.effective_user.id}"
        )
        await update.message.reply_text(
            "Restart requested. Host will systemctl restart in ~15s. Ping after /health is OK."
        )

    async def on_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.effective_user or not update.effective_chat or not update.message:
            return
        if not self.gateway.is_allowed(update.effective_user.id):
            return
        if not self._chat_allowed(update.effective_chat.id):
            return

        text = (update.message.text or update.message.caption or "").strip()
        chat_id = update.effective_chat.id
        thread_id = update.message.message_thread_id
        sess_key = self._session_key(chat_id, thread_id)

        dest = attachments_dir(
            self.gateway.config.data_dir,
            transport="telegram",
            thread_key=f"{chat_id}:{thread_id}" if thread_id else str(chat_id),
        )
        saved = await save_telegram_attachments(update.message, context.bot, dest)
        prompt = merge_prompt_with_attachments(text, saved)
        if not prompt:
            return

        status = await update.message.reply_text("⏳ Agent running…")
        status_id = status.message_id

        async def send(
            out: str,
            *,
            _chat_id: int = chat_id,
            _thread_id: int | None = thread_id,
            _reply_to: int = status_id,
        ) -> None:
            await send_chunks(
                lambda chunk: context.bot.send_message(
                    chat_id=_chat_id,
                    text=chunk,
                    message_thread_id=_thread_id,
                    reply_to_message_id=_reply_to,
                ),
                out,
            )

        async def edit_status(
            out: str,
            *,
            _chat_id: int = chat_id,
            _thread_id: int | None = thread_id,
            _status_id: int = status_id,
            **_: object,
        ) -> None:
            await context.bot.edit_message_text(
                chat_id=_chat_id,
                message_id=_status_id,
                text=out[:4096],
                message_thread_id=_thread_id,
            )

        asyncio.create_task(
            self.gateway.run_job(
                agent_id=self.agent_id,
                session_key=sess_key,
                user_prompt=prompt,
                send=send,
                edit_status=edit_status,
            ),
            name=f"telegram-{sess_key}",
        )


async def run_telegram_bots(
    gateway: Gateway,
    *,
    apps_out: list[Application] | None = None,
) -> None:
    profiles = gateway.agents.telegram_agents()
    if not profiles:
        log.info("No Telegram agents configured")
        return

    apps: list[Application] = []
    for profile in profiles:
        runner = TelegramAgentApp(gateway=gateway, profile=profile)
        app = runner.build()
        await app.initialize()
        await app.start()
        await app.updater.start_polling(drop_pending_updates=True)  # type: ignore[union-attr]
        apps.append(app)
        if apps_out is not None:
            apps_out.append(app)
        log.info("Telegram polling started for %s", profile.display_name)

    try:
        while True:
            await asyncio.sleep(3600)
    finally:
        for app in apps:
            await app.updater.stop()  # type: ignore[union-attr]
            await app.stop()
            await app.shutdown()
