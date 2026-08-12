from __future__ import annotations

import asyncio
import logging
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from ..agents.profile import AgentProfile
from ..gateway.router import Gateway, title_from_prompt
from ..sessions import ThreadSession

log = logging.getLogger(__name__)

TELEGRAM_CHUNK = 4000


async def send_chunks(reply_fn, text: str) -> None:  # noqa: ANN001
    if len(text) <= TELEGRAM_CHUNK:
        await reply_fn(text)
        return
    start = 0
    while start < len(text):
        await reply_fn(text[start : start + TELEGRAM_CHUNK])
        start += TELEGRAM_CHUNK


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
        if self.profile.is_manager:
            app.add_handler(CommandHandler("agents", self.cmd_agents))
            app.add_handler(CommandHandler("rebuild", self.cmd_rebuild))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.on_message))
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
        self.gateway.clear_session(key)
        await update.message.reply_text("New session. Next message starts fresh.")  # type: ignore[union-attr]

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
            "Rebuild requested. Host will recreate in ~15s. Ping after /health is OK."
        )

    async def on_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.effective_user or not update.effective_chat or not update.message:
            return
        if not self.gateway.is_allowed(update.effective_user.id):
            return
        if not self._chat_allowed(update.effective_chat.id):
            return

        prompt = update.message.text.strip()
        if not prompt:
            return

        chat_id = update.effective_chat.id
        thread_id = update.message.message_thread_id
        sess_key = self._session_key(chat_id, thread_id)

        status = await update.message.reply_text("⏳ Agent running…")
        status_id = status.message_id

        async def send(text: str) -> None:
            await send_chunks(
                lambda chunk: context.bot.send_message(
                    chat_id=chat_id,
                    text=chunk,
                    message_thread_id=thread_id,
                ),
                text,
            )

        async def edit_status(text: str) -> None:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=status_id,
                text=text[:4096],
                message_thread_id=thread_id,
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
