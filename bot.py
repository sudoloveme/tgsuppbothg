import asyncio
import logging
import os
from typing import Dict, Tuple

from dotenv import load_dotenv
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)


# Load environment variables from .env if present
load_dotenv()


TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
OWNER_ID_ENV = os.getenv("OWNER_ID", "").strip()
# Optional forum mode: ID супергруппы с включёнными темами (forum)
SUPPORT_CHAT_ID_ENV = os.getenv("SUPPORT_CHAT_ID", "").strip()

if not TELEGRAM_BOT_TOKEN:
    raise RuntimeError("TELEGRAM_BOT_TOKEN is not set. Put it in your environment or a .env file.")


OWNER_ID: int | None = int(OWNER_ID_ENV) if OWNER_ID_ENV.isdigit() else None
SUPPORT_CHAT_ID: int | None = int(SUPPORT_CHAT_ID_ENV) if SUPPORT_CHAT_ID_ENV.startswith("-") else None


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("support-bot")


# Maps message ids in the owner's chat to (original_chat_id, original_message_id)
# This is in-memory and will reset on restart.
owner_msg_id_to_origin: Dict[int, Tuple[int, int]] = {}

# Forum mode mappings
user_id_to_thread_id: Dict[int, int] = {}
support_msg_id_to_origin: Dict[int, Tuple[int, int]] = {}


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user is None:
        return

    user = update.effective_user

    if SUPPORT_CHAT_ID is not None:
        # Ensure topic exists immediately when user presses /start
        thread_id = await _ensure_forum_topic_for_user(update, context)
        logger.info("/start from user_id=%s → thread_id=%s", user.id, str(thread_id))
        await update.effective_message.reply_text(
            "Это чат поддержки. Ваше сообщение будет направлено в отдельную тему форума операторов."
        )
        # Post a note to operators that user started the dialog
        if thread_id is not None:
            try:
                header = _format_user_header(update)
                sent = await context.bot.send_message(
                    chat_id=SUPPORT_CHAT_ID,
                    message_thread_id=thread_id,
                    text=f"Пользователь начал диалог: {header}",
                )
                support_msg_id_to_origin[sent.message_id] = (
                    update.effective_chat.id if update.effective_chat else 0,
                    update.effective_message.message_id if update.effective_message else 0,
                )
            except Exception:
                logger.exception("Failed to notify operators on /start")
        return

    if OWNER_ID is not None and update.effective_user.id == OWNER_ID:
        await update.effective_message.reply_text(
            "Вы владелец. Сообщения пользователей будут пересылаться сюда.\n"
            "Ответьте на пересланное сообщение реплаем — бот отправит ответ пользователю.\n\n"
            "Команды:\n"
            "/id — показать ваш chat_id"
        )
        return

    await update.effective_message.reply_text(
        "Здравствуйте! Это чат поддержки. Напишите ваше сообщение — оператор ответит здесь."
    )


async def cmd_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat is None:
        return
    await update.effective_message.reply_text(str(update.effective_chat.id))


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if SUPPORT_CHAT_ID is not None:
        await update.effective_message.reply_text(
            "Сообщения пользователей публикуются в отдельные темы форума (по одному топику на пользователя).\n"
            "Чтобы ответить — напишите реплаем в нужной теме."
        )
        return

    if OWNER_ID is not None and update.effective_user and update.effective_user.id == OWNER_ID:
        await update.effective_message.reply_text(
            "Ответьте реплаем на сообщение пользователя — бот перешлёт ответ.\n"
            "Команды: /id — ваш chat_id"
        )
    else:
        await update.effective_message.reply_text(
            "Напишите сообщение — оператор ответит здесь"
        )


async def cmd_diag(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Diagnostics: show current mode, chat settings, and bot permissions."""
    lines: list[str] = []
    try:
        me = await context.bot.get_me()
        lines.append(f"bot: @{me.username} id:{me.id}")
    except Exception:
        lines.append("bot: <unknown>")

    if SUPPORT_CHAT_ID is not None:
        lines.append(f"mode: forum")
        lines.append(f"support_chat_id: {SUPPORT_CHAT_ID}")
        try:
            chat = await context.bot.get_chat(SUPPORT_CHAT_ID)
            lines.append(f"chat.title: {getattr(chat, 'title', '')}")
            lines.append(f"chat.is_forum: {getattr(chat, 'is_forum', False)}")
            # Check bot membership and permissions
            try:
                member = await context.bot.get_chat_member(SUPPORT_CHAT_ID, me.id)
                can_topics = getattr(member, 'can_manage_topics', False) or getattr(getattr(member, 'privileges', None), 'can_manage_topics', False)
                is_admin = str(getattr(member, 'status', '')) in {"administrator", "creator"}
                lines.append(f"bot_is_admin: {is_admin}")
                lines.append(f"can_manage_topics: {can_topics}")
            except Exception:
                lines.append("get_chat_member: failed")
        except Exception:
            lines.append("get_chat: failed")
    else:
        lines.append("mode: owner-dm")
        lines.append(f"owner_id: {OWNER_ID}")

    await update.effective_message.reply_text("\n".join(lines))


def _format_user_header(update: Update) -> str:
    user = update.effective_user
    if not user:
        return "Новое сообщение"
    parts = []
    if user.full_name:
        parts.append(f"{user.full_name}")
    if user.username:
        parts.append(f"@{user.username}")
    parts.append(f"id:{user.id}")
    return " | ".join(parts)


def _display_name(update: Update) -> str:
    user = update.effective_user
    if not user:
        return "Пользователь"
    if user.full_name:
        return user.full_name
    if user.username:
        return f"@{user.username}"
    return f"id:{user.id}"


async def _ensure_forum_topic_for_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int | None:
    if SUPPORT_CHAT_ID is None:
        return None
    user = update.effective_user
    if user is None:
        return None
    if user.id in user_id_to_thread_id:
        return user_id_to_thread_id[user.id]
    name = _display_name(update)[:128]
    try:
        topic = await context.bot.create_forum_topic(chat_id=SUPPORT_CHAT_ID, name=name)
        thread_id = topic.message_thread_id
        user_id_to_thread_id[user.id] = thread_id
        header = _format_user_header(update)
        sent = await context.bot.send_message(
            chat_id=SUPPORT_CHAT_ID,
            message_thread_id=thread_id,
            text=f"Открыт диалог: {header}\nОтветьте реплаем в этой теме.",
        )
        support_msg_id_to_origin[sent.message_id] = (
            update.effective_chat.id if update.effective_chat else 0,
            update.effective_message.message_id if update.effective_message else 0,
        )
        return thread_id
    except Exception as e:
        logger.exception("Failed to create forum topic (chat may not be forum or no permission): %s", e)
        # Try to notify operators in support chat if possible
        try:
            await context.bot.send_message(
                chat_id=SUPPORT_CHAT_ID,
                text=(
                    f"Не удалось создать тему для пользователя {name}.\n"
                    f"Проверьте: чат является форумом и у бота есть право 'Управлять темами'."
                ),
            )
        except Exception:
            pass
        return None


async def handle_incoming_from_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    global OWNER_ID
    message = update.effective_message
    chat = update.effective_chat
    if message is None or chat is None:
        return

    try:
        logger.info(
            "incoming msg: chat_id=%s user_id=%s text_present=%s media=%s",
            getattr(chat, "id", None),
            getattr(update.effective_user, "id", None),
            bool(getattr(message, "text", "")),
            message.effective_attachment is not None if hasattr(message, "effective_attachment") else False,
        )
    except Exception:
        pass

    # If not configured yet, try to detect owner from the first /start in a private chat with the owner
    if OWNER_ID is None and update.effective_user and update.effective_user.id == chat.id and chat.type == "private":
        # No-op; owner still unknown until set via env. They can use /id.
        pass

    if OWNER_ID is None:
        # Allow basic usage: if the first person to send /start says they're owner, they can set OWNER_ID in env later
        logger.warning("OWNER_ID is not configured. Use /id in your owner chat and set OWNER_ID env var.")

    # Forum mode: route into per-user topic
    if SUPPORT_CHAT_ID is not None:
        thread_id = await _ensure_forum_topic_for_user(update, context)
        if thread_id is None:
            return
        header = _format_user_header(update)
        sent_header = await context.bot.send_message(
            chat_id=SUPPORT_CHAT_ID,
            message_thread_id=thread_id,
            text=f"Новое сообщение от: {header}",
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
        support_msg_id_to_origin[sent_header.message_id] = (chat.id, message.message_id)

        copied = await context.bot.copy_message(
            chat_id=SUPPORT_CHAT_ID,
            message_thread_id=thread_id,
            from_chat_id=chat.id,
            message_id=message.message_id,
        )
        support_msg_id_to_origin[copied.message_id] = (chat.id, message.message_id)
        return

    # DM owner mode
    # Do not forward the owner's own messages here
    if OWNER_ID is not None and chat.id == OWNER_ID:
        return
    if OWNER_ID is None:
        return  # cannot deliver anywhere yet
    header = _format_user_header(update)
    sent_header = await context.bot.send_message(
        chat_id=OWNER_ID,
        text=f"Новое сообщение от: {header}",
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )
    owner_msg_id_to_origin[sent_header.message_id] = (chat.id, message.message_id)
    copied = await context.bot.copy_message(
        chat_id=OWNER_ID,
        from_chat_id=chat.id,
        message_id=message.message_id,
    )
    owner_msg_id_to_origin[copied.message_id] = (chat.id, message.message_id)


async def handle_owner_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    chat = update.effective_chat
    if message is None or chat is None:
        return

    # Forum mode
    if SUPPORT_CHAT_ID is not None:
        if chat.id != SUPPORT_CHAT_ID or message.reply_to_message is None:
            return
        replied_id = message.reply_to_message.message_id
        origin = support_msg_id_to_origin.get(replied_id)
        if not origin:
            return
        original_chat_id, original_message_id = origin
        await context.bot.copy_message(
            chat_id=original_chat_id,
            from_chat_id=chat.id,
            message_id=message.message_id,
            reply_to_message_id=original_message_id,
            protect_content=False,
            allow_sending_without_reply=True,
        )
        return

    # DM owner mode
    if OWNER_ID is None:
        return
    if chat.id != OWNER_ID or message.reply_to_message is None:
        return
    replied_id = message.reply_to_message.message_id
    origin = owner_msg_id_to_origin.get(replied_id)
    if not origin:
        return
    original_chat_id, original_message_id = origin
    await context.bot.copy_message(
        chat_id=original_chat_id,
        from_chat_id=OWNER_ID,
        message_id=message.message_id,
        reply_to_message_id=original_message_id,
        protect_content=False,
        allow_sending_without_reply=True,
    )


def main() -> None:
    # Log startup mode
    if SUPPORT_CHAT_ID is not None:
        logger.info("Starting in FORUM mode. SUPPORT_CHAT_ID=%s", str(SUPPORT_CHAT_ID))
    else:
        logger.info("Starting in OWNER DM mode. OWNER_ID=%s", str(OWNER_ID))

    application = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

    # Commands
    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CommandHandler("id", cmd_id))
    # Diagnostics command
    application.add_handler(CommandHandler("diag", cmd_diag))
    application.add_handler(CommandHandler("help", cmd_help))

    # Owner replies handler must run before generic messages to avoid duplicate processing
    application.add_handler(MessageHandler(filters.ALL, handle_owner_reply))

    # Generic incoming messages from users
    application.add_handler(
        MessageHandler(
            filters.ALL & ~filters.COMMAND,
            handle_incoming_from_user,
        )
    )

    application.run_polling(close_loop=False)


if __name__ == "__main__":
    main()



