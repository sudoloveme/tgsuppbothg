import asyncio
import logging
import os
import sqlite3
from pathlib import Path
from typing import Dict, Tuple

from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    CallbackQueryHandler,
    filters,
)


# Load environment variables from .env if present
load_dotenv()


TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
OWNER_ID_ENV = os.getenv("OWNER_ID", "").strip()
# Optional forum mode: ID супергруппы с включёнными темами (forum)
SUPPORT_CHAT_ID_ENV = os.getenv("SUPPORT_CHAT_ID", "").strip()
# Optional DB for persistence
DB_PATH = os.getenv("DB_PATH", "data.db").strip() or "data.db"
ARCHIVE_AFTER_HOURS = int(os.getenv("ARCHIVE_AFTER_HOURS", "72").strip() or 72)

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
########################
# SQLite persistence   #
########################

def _db_connect() -> sqlite3.Connection:
    path = Path(DB_PATH)
    # Ensure directory exists if path includes a directory
    if path.parent and not path.parent.exists():
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
    conn = sqlite3.connect(str(path))
    # New normalized table keyed by (support_chat_id, user_id)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS user_topics_v2 (\n"
        "  support_chat_id INTEGER NOT NULL,\n"
        "  user_id INTEGER NOT NULL,\n"
        "  thread_id INTEGER NOT NULL,\n"
        "  created_at TEXT DEFAULT CURRENT_TIMESTAMP,\n"
        "  PRIMARY KEY (support_chat_id, user_id)\n"
        ")"
    )
    # Thread state table
    conn.execute(
        "CREATE TABLE IF NOT EXISTS thread_states (\n"
        "  support_chat_id INTEGER NOT NULL,\n"
        "  thread_id INTEGER NOT NULL,\n"
        "  status TEXT NOT NULL DEFAULT 'active',\n"
        "  archived INTEGER NOT NULL DEFAULT 0,\n"
        "  last_activity TEXT DEFAULT CURRENT_TIMESTAMP,\n"
        "  PRIMARY KEY (support_chat_id, thread_id)\n"
        ")"
    )
    # Migrate from legacy table if present
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS user_topics (user_id INTEGER PRIMARY KEY, thread_id INTEGER NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP)"
        )
        if SUPPORT_CHAT_ID is not None:
            conn.execute(
                "INSERT OR IGNORE INTO user_topics_v2 (support_chat_id, user_id, thread_id)\n"
                "SELECT ?, user_id, thread_id FROM user_topics",
                (SUPPORT_CHAT_ID,),
            )
            conn.commit()
    except Exception:
        # Best-effort migration
        pass
    return conn


def db_get_thread_id(user_id: int) -> int | None:
    try:
        conn = _db_connect()
        if SUPPORT_CHAT_ID is not None:
            cur = conn.execute(
                "SELECT thread_id FROM user_topics_v2 WHERE support_chat_id=? AND user_id=?",
                (SUPPORT_CHAT_ID, user_id),
            )
        else:
            cur = conn.execute("SELECT thread_id FROM user_topics WHERE user_id=?", (user_id,))
        row = cur.fetchone()
        conn.close()
        tid = int(row[0]) if row else None
        logger.info("DB get thread: support_chat_id=%s user_id=%s -> %s", str(SUPPORT_CHAT_ID), user_id, str(tid))
        return tid
    except Exception:
        logger.exception("DB read failed (get thread): user_id=%s", user_id)
        return None


def db_get_user_id(thread_id: int) -> int | None:
    """Get user_id from thread_id."""
    try:
        conn = _db_connect()
        if SUPPORT_CHAT_ID is not None:
            cur = conn.execute(
                "SELECT user_id FROM user_topics_v2 WHERE support_chat_id=? AND thread_id=?",
                (SUPPORT_CHAT_ID, thread_id),
            )
        else:
            cur = conn.execute("SELECT user_id FROM user_topics WHERE thread_id=?", (thread_id,))
        row = cur.fetchone()
        conn.close()
        uid = int(row[0]) if row else None
        logger.info("DB get user: support_chat_id=%s thread_id=%s -> %s", str(SUPPORT_CHAT_ID), thread_id, str(uid))
        return uid
    except Exception:
        logger.exception("DB read failed (get user): thread_id=%s", thread_id)
        return None


def db_set_thread_id(user_id: int, thread_id: int) -> None:
    try:
        conn = _db_connect()
        if SUPPORT_CHAT_ID is not None:
            conn.execute(
                "INSERT INTO user_topics_v2(support_chat_id, user_id, thread_id) VALUES(?, ?, ?)\n"
                "ON CONFLICT(support_chat_id, user_id) DO UPDATE SET thread_id=excluded.thread_id",
                (SUPPORT_CHAT_ID, user_id, thread_id),
            )
        else:
            conn.execute(
                "INSERT INTO user_topics(user_id, thread_id) VALUES(?, ?) ON CONFLICT(user_id) DO UPDATE SET thread_id=excluded.thread_id",
                (user_id, thread_id),
            )
        conn.commit()
        conn.close()
        logger.info("DB set thread: support_chat_id=%s user_id=%s -> %s", str(SUPPORT_CHAT_ID), user_id, thread_id)
    except Exception:
        logger.exception("DB write failed (set thread): user_id=%s thread_id=%s", user_id, thread_id)


# Thread state helpers
def db_upsert_thread_state(thread_id: int, status: str = "active", archived: int = 0) -> None:
    try:
        conn = _db_connect()
        conn.execute(
            "INSERT INTO thread_states (support_chat_id, thread_id, status, archived) VALUES (?, ?, ?, ?)\n"
            "ON CONFLICT(support_chat_id, thread_id) DO UPDATE SET status=excluded.status, archived=excluded.archived, last_activity=CURRENT_TIMESTAMP",
            (SUPPORT_CHAT_ID, thread_id, status, archived),
        )
        conn.commit()
        conn.close()
    except Exception:
        logger.exception("DB write failed (upsert thread state): thread_id=%s", thread_id)


def db_touch_activity(thread_id: int) -> None:
    try:
        conn = _db_connect()
        conn.execute(
            "UPDATE thread_states SET last_activity=CURRENT_TIMESTAMP WHERE support_chat_id=? AND thread_id=?",
            (SUPPORT_CHAT_ID, thread_id),
        )
        conn.commit()
        conn.close()
    except Exception:
        logger.exception("DB write failed (touch activity): thread_id=%s", thread_id)


def db_get_thread_state(thread_id: int) -> tuple[str, int] | None:
    try:
        conn = _db_connect()
        cur = conn.execute(
            "SELECT status, archived FROM thread_states WHERE support_chat_id=? AND thread_id=?",
            (SUPPORT_CHAT_ID, thread_id),
        )
        row = cur.fetchone()
        conn.close()
        return (row[0], int(row[1])) if row else None
    except Exception:
        logger.exception("DB read failed (get thread state): thread_id=%s", thread_id)
        return None


def build_thread_keyboard(thread_id: int) -> InlineKeyboardMarkup:
    state = db_get_thread_state(thread_id)
    show_open = False
    if state is not None:
        status, archived = state
        show_open = archived or status != "active"
    if show_open:
        btn = InlineKeyboardButton(text="Открыть диалог", callback_data=f"open:{thread_id}")
    else:
        btn = InlineKeyboardButton(text="Закрыть диалог", callback_data=f"close:{thread_id}")
    return InlineKeyboardMarkup([[btn]])


def build_rating_keyboard() -> InlineKeyboardMarkup:
    """Build keyboard with rating buttons 1-5."""
    buttons = [
        InlineKeyboardButton(text="1", callback_data="rating:1"),
        InlineKeyboardButton(text="2", callback_data="rating:2"),
        InlineKeyboardButton(text="3", callback_data="rating:3"),
        InlineKeyboardButton(text="4", callback_data="rating:4"),
        InlineKeyboardButton(text="5", callback_data="rating:5"),
    ]
    return InlineKeyboardMarkup([buttons])


async def send_rating_message(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> None:
    """Send rating message to user."""
    try:
        await context.bot.send_message(
            chat_id=user_id,
            text="Оцените работу поддержки",
            reply_markup=build_rating_keyboard(),
        )
        logger.info("Sent rating message to user_id=%s", user_id)
    except Exception:
        logger.exception("Failed to send rating message to user_id=%s", user_id)



async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user is None:
        return

    user = update.effective_user

    if SUPPORT_CHAT_ID is not None:
        # Ensure topic exists immediately when user presses /start
        thread_id = await _ensure_forum_topic_for_user(update, context)
        logger.info("/start from user_id=%s → thread_id=%s", user.id, str(thread_id))
        await update.effective_message.reply_text(
            "Здравствуйте! Опишите вашу проблему или вопрос. Для ускорения оказания помощи, укажите сразу ваш email, а также скриншоты проблемы если возможно. Мы ответим вам в течение 24 часов."
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


## cmd_help removed


async def cmd_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if SUPPORT_CHAT_ID is None:
        return
    if update.effective_chat is None or update.effective_chat.id != SUPPORT_CHAT_ID:
        return
    msg = update.effective_message
    if msg is None or msg.message_thread_id is None:
        await update.effective_message.reply_text("Эта команда работает внутри темы форума")
        return
    thread_id = msg.message_thread_id
    try:
        await context.bot.send_message(
            chat_id=SUPPORT_CHAT_ID,
            message_thread_id=thread_id,
            text="Панель управления темой",
            reply_markup=build_thread_keyboard(thread_id),
        )
    except Exception:
        logger.exception("Failed to send panel in thread %s", thread_id)


async def handle_callback_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cq = update.callback_query
    if cq is None:
        return
    data = cq.data or ""
    
    # Handle rating callbacks
    if data.startswith("rating:"):
        try:
            _, rating_str = data.split(":", 1)
            rating = int(rating_str)
            if 1 <= rating <= 5:
                await cq.answer(f"Спасибо за оценку: {rating} ⭐")
                # Optionally edit the message to show rating was received
                try:
                    await cq.message.edit_text("Спасибо за вашу оценку!")
                except Exception:
                    pass
                logger.info("User %s gave rating %s", cq.from_user.id if cq.from_user else "unknown", rating)
            else:
                await cq.answer("Некорректная оценка", show_alert=True)
        except Exception:
            await cq.answer("Ошибка обработки оценки", show_alert=True)
        return
    
    # Handle close/open callbacks (forum mode only)
    if SUPPORT_CHAT_ID is None:
        await cq.answer()
        return
    if not (data.startswith("close:") or data.startswith("open:")):
        await cq.answer()
        return
    try:
        _, thread_str = data.split(":", 1)
        thread_id = int(thread_str)
    except Exception:
        await cq.answer("Некорректные данные", show_alert=False)
        return

    if data.startswith("close:"):
        try:
            await context.bot.close_forum_topic(chat_id=SUPPORT_CHAT_ID, message_thread_id=thread_id)
            await cq.answer("Диалог закрыт")
        except Exception as e:
            # Тема может быть уже закрыта или другая ошибка, но все равно обновим состояние
            logger.warning("Failed to close forum topic %s: %s", thread_id, str(e))
            await cq.answer("Диалог помечен как закрыт", show_alert=False)
        
        # Обновляем состояние в БД и отправляем оценку независимо от результата закрытия
        try:
            db_upsert_thread_state(thread_id, status="closed", archived=1)
            # Update buttons to show Open
            try:
                await cq.message.edit_reply_markup(
                    reply_markup=InlineKeyboardMarkup(
                        [[InlineKeyboardButton(text="Открыть диалог", callback_data=f"open:{thread_id}")]]
                    )
                )
            except Exception:
                pass
            # Send rating message to user
            user_id = db_get_user_id(thread_id)
            if user_id is not None:
                await send_rating_message(context, user_id)
        except Exception as e:
            logger.exception("Failed to update state or send rating for thread %s", thread_id)
    else:
        try:
            await context.bot.reopen_forum_topic(chat_id=SUPPORT_CHAT_ID, message_thread_id=thread_id)
            db_upsert_thread_state(thread_id, status="active", archived=0)
            await cq.answer("Диалог открыт")
            try:
                await cq.message.edit_reply_markup(
                    reply_markup=InlineKeyboardMarkup(
                        [[InlineKeyboardButton(text="Закрыть диалог", callback_data=f"close:{thread_id}")]]
                    )
                )
            except Exception:
                pass
        except Exception:
            await cq.answer("Не удалось открыть", show_alert=True)


async def archive_inactive_topics_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    if SUPPORT_CHAT_ID is None or ARCHIVE_AFTER_HOURS <= 0:
        return
    try:
        conn = _db_connect()
        cur = conn.execute(
            "SELECT thread_id FROM thread_states WHERE support_chat_id=? AND archived=0 AND status='active' AND last_activity <= datetime('now', ?)",
            (SUPPORT_CHAT_ID, f"-{ARCHIVE_AFTER_HOURS} hours"),
        )
        rows = cur.fetchall()
        conn.close()
    except Exception:
        logger.exception("DB read failed (archive scan)")
        return

    for (thread_id,) in rows:
        try:
            try:
                await context.bot.close_forum_topic(chat_id=SUPPORT_CHAT_ID, message_thread_id=thread_id)
                logger.info("Auto-archived thread %s due to inactivity", thread_id)
            except Exception as e:
                # Тема может быть уже закрыта, но все равно обновим состояние
                logger.warning("Failed to close forum topic %s during auto-archive: %s", thread_id, str(e))
            
            # Обновляем состояние в БД и отправляем оценку независимо от результата закрытия
            db_upsert_thread_state(thread_id, status="closed", archived=1)
            # Send rating message to user
            user_id = db_get_user_id(thread_id)
            if user_id is not None:
                await send_rating_message(context, user_id)
        except Exception:
            logger.exception("Failed to auto-archive thread %s", thread_id)


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
    # Try DB
    cached = db_get_thread_id(user.id)
    if cached is not None:
        user_id_to_thread_id[user.id] = cached
        return cached
    name = _display_name(update)[:128]
    try:
        topic = await context.bot.create_forum_topic(chat_id=SUPPORT_CHAT_ID, name=name)
        thread_id = topic.message_thread_id
        user_id_to_thread_id[user.id] = thread_id
        db_set_thread_id(user.id, thread_id)
        db_upsert_thread_state(thread_id, status="active", archived=0)
        header = _format_user_header(update)
        # Buttons
        keyboard = build_thread_keyboard(thread_id)
        sent = await context.bot.send_message(
            chat_id=SUPPORT_CHAT_ID,
            message_thread_id=thread_id,
            text=f"Открыт диалог: {header}\nОтветьте реплаем в этой теме.",
            reply_markup=keyboard,
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

    # Accept only private messages from real users (avoid bot/self or group events)
    if getattr(chat, "type", "") != "private":
        return
    if update.effective_user is None or getattr(update.effective_user, "is_bot", False):
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
        # Reopen if was archived/closed
        state = db_get_thread_state(thread_id)
        if state is not None:
            status, archived = state
            if archived or status != "active":
                try:
                    await context.bot.reopen_forum_topic(chat_id=SUPPORT_CHAT_ID, message_thread_id=thread_id)
                except Exception:
                    logger.exception("Failed to reopen topic %s", thread_id)
                db_upsert_thread_state(thread_id, status="active", archived=0)
        db_touch_activity(thread_id)
        header = _format_user_header(update)
        sent_header = await context.bot.send_message(
            chat_id=SUPPORT_CHAT_ID,
            message_thread_id=thread_id,
            text=f"Новое сообщение от: {header}",
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
            reply_markup=build_thread_keyboard(thread_id),
        )
        support_msg_id_to_origin[sent_header.message_id] = (chat.id, message.message_id)

        # Try to copy message into the topic; fallback to forward if copy fails
        try:
            copied = await context.bot.copy_message(
                chat_id=SUPPORT_CHAT_ID,
                message_thread_id=thread_id,
                from_chat_id=chat.id,
                message_id=message.message_id,
            )
            support_msg_id_to_origin[copied.message_id] = (chat.id, message.message_id)
            logger.info("copied msg to topic: u=%s -> thread=%s mid=%s", update.effective_user.id if update.effective_user else None, thread_id, copied.message_id)
        except Exception as e:
            logger.exception("copy_message failed, trying forward_message: %s", e)
            try:
                fwd = await context.bot.forward_message(
                    chat_id=SUPPORT_CHAT_ID,
                    message_thread_id=thread_id,
                    from_chat_id=chat.id,
                    message_id=message.message_id,
                )
                support_msg_id_to_origin[fwd.message_id] = (chat.id, message.message_id)
                logger.info("forwarded msg to topic: u=%s -> thread=%s mid=%s", update.effective_user.id if update.effective_user else None, thread_id, fwd.message_id)
            except Exception as e2:
                logger.exception("forward_message also failed: %s", e2)
                # As a last resort, echo text content if available
                if message.text:
                    note = await context.bot.send_message(
                        chat_id=SUPPORT_CHAT_ID,
                        message_thread_id=thread_id,
                        text=f"[Текст клиента]\n{message.text}",
                    )
                    support_msg_id_to_origin[note.message_id] = (chat.id, message.message_id)
                else:
                    await context.bot.send_message(
                        chat_id=SUPPORT_CHAT_ID,
                        message_thread_id=thread_id,
                        text="Не удалось отобразить сообщение клиента (неизвестный тип/ошибка API)",
                    )
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
    # Same fallback logic in DM-owner mode
    try:
        copied = await context.bot.copy_message(
            chat_id=OWNER_ID,
            from_chat_id=chat.id,
            message_id=message.message_id,
        )
        owner_msg_id_to_origin[copied.message_id] = (chat.id, message.message_id)
        logger.info("copied msg to owner: u=%s mid=%s", update.effective_user.id if update.effective_user else None, copied.message_id)
    except Exception as e:
        logger.exception("copy_message to owner failed, trying forward_message: %s", e)
        try:
            fwd = await context.bot.forward_message(
                chat_id=OWNER_ID,
                from_chat_id=chat.id,
                message_id=message.message_id,
            )
            owner_msg_id_to_origin[fwd.message_id] = (chat.id, message.message_id)
            logger.info("forwarded msg to owner: u=%s mid=%s", update.effective_user.id if update.effective_user else None, fwd.message_id)
        except Exception as e2:
            logger.exception("forward_message to owner also failed: %s", e2)
            if message.text:
                note = await context.bot.send_message(
                    chat_id=OWNER_ID,
                    text=f"[Текст клиента]\n{message.text}",
                )
                owner_msg_id_to_origin[note.message_id] = (chat.id, message.message_id)
            else:
                await context.bot.send_message(
                    chat_id=OWNER_ID,
                    text="Не удалось отобразить сообщение клиента (неизвестный тип/ошибка API)",
                )


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
        # Touch activity
        if message.message_thread_id is not None:
            db_touch_activity(message.message_thread_id)
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
    if SUPPORT_CHAT_ID is not None:
        application.add_handler(CommandHandler("panel", cmd_panel))

    # Reply handlers: restrict to specific chats to avoid intercepting all messages
    if SUPPORT_CHAT_ID is not None:
        application.add_handler(
            MessageHandler(
                filters.Chat(SUPPORT_CHAT_ID) & filters.REPLY,
                handle_owner_reply,
            )
        )
    if OWNER_ID is not None:
        application.add_handler(
            MessageHandler(
                filters.Chat(OWNER_ID) & filters.REPLY,
                handle_owner_reply,
            )
        )

    # Generic incoming messages from users
    application.add_handler(
        MessageHandler(
            filters.ChatType.PRIVATE & ~filters.COMMAND,
            handle_incoming_from_user,
        )
    )

    # Buttons handler
    application.add_handler(CallbackQueryHandler(handle_callback_buttons))

    # Auto-archive job (only if JobQueue available)
    if SUPPORT_CHAT_ID is not None and ARCHIVE_AFTER_HOURS > 0 and getattr(application, "job_queue", None) is not None:
        application.job_queue.run_repeating(archive_inactive_topics_job, interval=3600, first=60)
    elif SUPPORT_CHAT_ID is not None and ARCHIVE_AFTER_HOURS > 0:
        logger.warning("JobQueue not available. Install PTB with job-queue extras: pip install 'python-telegram-bot[job-queue]==21.5'")

    application.run_polling(close_loop=False)


if __name__ == "__main__":
    main()



