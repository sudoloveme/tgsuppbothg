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

from api_client import get_user_by_email, get_user_by_uuid, update_user_telegram_id, format_user_info


# Load environment variables from .env if present
load_dotenv()


TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
OWNER_ID_ENV = os.getenv("OWNER_ID", "").strip()
# Optional forum mode: ID супергруппы с включёнными темами (forum)
SUPPORT_CHAT_ID_ENV = os.getenv("SUPPORT_CHAT_ID", "").strip()
# Optional DB for persistence
DB_PATH = os.getenv("DB_PATH", "data.db").strip() or "data.db"
ARCHIVE_AFTER_HOURS = int(os.getenv("ARCHIVE_AFTER_HOURS", "72").strip() or 72)
# Thread ID для темы с уведомлениями об оценках (по умолчанию 1 = General тема)
RATINGS_NOTIFICATIONS_THREAD_ID = int(os.getenv("RATINGS_NOTIFICATIONS_THREAD_ID", "1").strip() or "1")

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
    # Ratings table
    conn.execute(
        "CREATE TABLE IF NOT EXISTS ratings (\n"
        "  id INTEGER PRIMARY KEY AUTOINCREMENT,\n"
        "  user_id INTEGER NOT NULL,\n"
        "  thread_id INTEGER,\n"
        "  rating INTEGER NOT NULL CHECK(rating >= 1 AND rating <= 5),\n"
        "  created_at TEXT DEFAULT CURRENT_TIMESTAMP\n"
        ")"
    )
    # User backend data table (UUID, email from API)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS user_backend_data (\n"
        "  user_id INTEGER NOT NULL,\n"
        "  support_chat_id INTEGER,\n"
        "  uuid TEXT,\n"
        "  email TEXT,\n"
        "  updated_at TEXT DEFAULT CURRENT_TIMESTAMP,\n"
        "  PRIMARY KEY (user_id, support_chat_id)\n"
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


# Ratings functions
def db_save_rating(user_id: int, rating: int, thread_id: int | None = None) -> None:
    """Save rating to database."""
    try:
        conn = _db_connect()
        conn.execute(
            "INSERT INTO ratings (user_id, thread_id, rating) VALUES (?, ?, ?)",
            (user_id, thread_id, rating),
        )
        conn.commit()
        conn.close()
        logger.info("Saved rating: user_id=%s thread_id=%s rating=%s", user_id, thread_id, rating)
    except Exception:
        logger.exception("DB write failed (save rating): user_id=%s rating=%s", user_id, rating)


def db_get_ratings_stats() -> dict:
    """Get statistics about ratings."""
    try:
        conn = _db_connect()
        # Total count
        cur = conn.execute("SELECT COUNT(*) FROM ratings")
        total = cur.fetchone()[0]
        
        # Average rating
        cur = conn.execute("SELECT AVG(rating) FROM ratings")
        avg_rating = cur.fetchone()[0]
        avg_rating = round(avg_rating, 2) if avg_rating else 0
        
        # Ratings distribution
        cur = conn.execute(
            "SELECT rating, COUNT(*) FROM ratings GROUP BY rating ORDER BY rating"
        )
        distribution = {row[0]: row[1] for row in cur.fetchall()}
        
        conn.close()
        
        return {
            "total": total,
            "average": avg_rating,
            "distribution": distribution,
        }
    except Exception:
        logger.exception("DB read failed (get ratings stats)")
        return {"total": 0, "average": 0, "distribution": {}}


def db_get_user_ratings(user_id: int) -> list[tuple]:
    """Get all ratings from a specific user."""
    try:
        conn = _db_connect()
        cur = conn.execute(
            "SELECT rating, thread_id, created_at FROM ratings WHERE user_id=? ORDER BY created_at DESC",
            (user_id,),
        )
        rows = cur.fetchall()
        conn.close()
        return rows
    except Exception:
        logger.exception("DB read failed (get user ratings): user_id=%s", user_id)
        return []


# User backend data functions
def db_save_user_backend_data(user_id: int, uuid: str, email: str) -> None:
    """Save user backend data (UUID, email) to database."""
    try:
        conn = _db_connect()
        if SUPPORT_CHAT_ID is not None:
            conn.execute(
                "INSERT INTO user_backend_data (user_id, support_chat_id, uuid, email) VALUES (?, ?, ?, ?)\n"
                "ON CONFLICT(user_id, support_chat_id) DO UPDATE SET uuid=excluded.uuid, email=excluded.email, updated_at=CURRENT_TIMESTAMP",
                (user_id, SUPPORT_CHAT_ID, uuid, email),
            )
        else:
            conn.execute(
                "INSERT INTO user_backend_data (user_id, support_chat_id, uuid, email) VALUES (?, ?, ?, ?)\n"
                "ON CONFLICT(user_id, support_chat_id) DO UPDATE SET uuid=excluded.uuid, email=excluded.email, updated_at=CURRENT_TIMESTAMP",
                (user_id, None, uuid, email),
            )
        conn.commit()
        conn.close()
        logger.info("Saved backend data: user_id=%s uuid=%s email=%s", user_id, uuid, email)
    except Exception:
        logger.exception("DB write failed (save user backend data): user_id=%s uuid=%s", user_id, uuid)


def db_get_user_backend_data(user_id: int) -> tuple[str, str] | None:
    """Get user backend data (UUID, email) from database."""
    try:
        conn = _db_connect()
        if SUPPORT_CHAT_ID is not None:
            cur = conn.execute(
                "SELECT uuid, email FROM user_backend_data WHERE user_id=? AND support_chat_id=?",
                (user_id, SUPPORT_CHAT_ID),
            )
        else:
            cur = conn.execute(
                "SELECT uuid, email FROM user_backend_data WHERE user_id=? AND support_chat_id IS NULL",
                (user_id,),
            )
        row = cur.fetchone()
        conn.close()
        return (row[0], row[1]) if row else None
    except Exception:
        logger.exception("DB read failed (get user backend data): user_id=%s", user_id)
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


async def notify_admin_about_rating(context: ContextTypes.DEFAULT_TYPE, user_id: int, rating: int, thread_id: int | None = None, user_obj=None) -> None:
    """Send notification to admins about user rating."""
    if SUPPORT_CHAT_ID is None:
        return
    
    try:
        # Get user information
        user = user_obj
        if user is None:
            # Try to get user info from chat
            try:
                chat_member = await context.bot.get_chat_member(SUPPORT_CHAT_ID, user_id)
                user = chat_member.user if hasattr(chat_member, 'user') else None
            except Exception:
                # If user is not in support chat, try to get info from private chat
                try:
                    user = await context.bot.get_chat(user_id)
                except Exception:
                    user = None
        
        # Format user info
        user_parts = []
        if user:
            if hasattr(user, 'full_name') and user.full_name:
                user_parts.append(user.full_name)
            elif hasattr(user, 'first_name') and user.first_name:
                user_parts.append(user.first_name)
            if hasattr(user, 'username') and user.username:
                user_parts.append(f"@{user.username}")
        user_parts.append(f"id:{user_id}")
        user_info = " | ".join(user_parts)
        
        # Create rating message
        rating_emoji = "⭐" * rating
        message_parts = [f"📊 Новая оценка: {rating} {rating_emoji}"]
        message_parts.append(f"Пользователь: {user_info}")
        if thread_id:
            message_parts.append(f"Тема диалога: {thread_id}")
        
        message_text = "\n".join(message_parts)
        
        # Send to notifications thread
        await context.bot.send_message(
            chat_id=SUPPORT_CHAT_ID,
            message_thread_id=RATINGS_NOTIFICATIONS_THREAD_ID,
            text=message_text,
            parse_mode=ParseMode.HTML,
        )
        logger.info("Sent rating notification for user_id=%s rating=%s", user_id, rating)
    except Exception:
        logger.exception("Failed to send rating notification for user_id=%s rating=%s", user_id, rating)



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


async def cmd_linkmail(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Link user email to Telegram ID via backend API."""
    if SUPPORT_CHAT_ID is None:
        await update.effective_message.reply_text("Эта команда работает только в режиме форума")
        return
    
    if update.effective_chat is None or update.effective_chat.id != SUPPORT_CHAT_ID:
        await update.effective_message.reply_text("Эта команда работает только в группе поддержки")
        return
    
    # Check if user is admin
    if update.effective_user is None:
        return
    
    try:
        member = await context.bot.get_chat_member(SUPPORT_CHAT_ID, update.effective_user.id)
        if member.status not in ("administrator", "creator"):
            await update.effective_message.reply_text("Доступ запрещен. Только администраторы могут использовать эту команду.")
            return
    except Exception:
        await update.effective_message.reply_text("Ошибка проверки прав доступа.")
        return
    
    # Check if command is called from a forum topic
    msg = update.effective_message
    if msg is None or msg.message_thread_id is None:
        await update.effective_message.reply_text("Эта команда работает внутри темы форума")
        return
    
    thread_id = msg.message_thread_id
    
    # Get email from command arguments
    if not context.args or len(context.args) == 0:
        await update.effective_message.reply_text(
            "Использование: /linkmail example@gmail.com\n\n"
            "Укажите email пользователя для привязки к Telegram ID."
        )
        return
    
    email = context.args[0].strip()
    
    # Basic email validation
    if "@" not in email or "." not in email.split("@")[1]:
        await update.effective_message.reply_text("❌ Некорректный формат email адреса.")
        return
    
    # Get user_id from thread_id
    user_id = db_get_user_id(thread_id)
    if user_id is None:
        await update.effective_message.reply_text(
            "❌ Не удалось найти пользователя для этой темы.\n"
            "Убедитесь, что пользователь уже писал в эту тему."
        )
        return
    
    # Send processing message
    processing_msg = await update.effective_message.reply_text("⏳ Обработка запроса...")
    
    try:
        # Step 1: Get user by email
        user_data = await get_user_by_email(email)
        
        if user_data is None:
            await processing_msg.edit_text(
                f"❌ Пользователь с email <code>{email}</code> не найден в системе.",
                parse_mode=ParseMode.HTML
            )
            return
        
        # Step 2: Update user's Telegram ID
        uuid = user_data.get("uuid")
        if not uuid:
            await processing_msg.edit_text("❌ Не удалось получить UUID пользователя.")
            return
        
        updated_user = await update_user_telegram_id(uuid, user_id)
        
        if updated_user is None:
            await processing_msg.edit_text(
                f"❌ Не удалось обновить Telegram ID для пользователя.\n"
                f"Email: <code>{email}</code>\n"
                f"UUID: <code>{uuid}</code>",
                parse_mode=ParseMode.HTML
            )
            return
        
        # Step 3: Save UUID and email to local database
        if "uuid" in updated_user and "email" in updated_user:
            db_save_user_backend_data(user_id, updated_user["uuid"], updated_user["email"])
        
        # Step 4: Format and send user information
        user_info = format_user_info(updated_user)
        
        await processing_msg.edit_text(
            user_info,
            parse_mode=ParseMode.HTML
        )
        
        logger.info(f"Linked email {email} to Telegram ID {user_id} for UUID {uuid}")
        
    except Exception as e:
        logger.exception(f"Error in linkmail command: {e}")
        await processing_msg.edit_text(
            f"❌ Произошла ошибка при обработке запроса:\n<code>{str(e)}</code>",
            parse_mode=ParseMode.HTML
        )


async def cmd_info(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Get user information by UUID from backend API."""
    if SUPPORT_CHAT_ID is None:
        await update.effective_message.reply_text("Эта команда работает только в режиме форума")
        return
    
    if update.effective_chat is None or update.effective_chat.id != SUPPORT_CHAT_ID:
        await update.effective_message.reply_text("Эта команда работает только в группе поддержки")
        return
    
    # Check if user is admin
    if update.effective_user is None:
        return
    
    try:
        member = await context.bot.get_chat_member(SUPPORT_CHAT_ID, update.effective_user.id)
        if member.status not in ("administrator", "creator"):
            await update.effective_message.reply_text("Доступ запрещен. Только администраторы могут использовать эту команду.")
            return
    except Exception:
        await update.effective_message.reply_text("Ошибка проверки прав доступа.")
        return
    
    # Check if command is called from a forum topic
    msg = update.effective_message
    if msg is None or msg.message_thread_id is None:
        await update.effective_message.reply_text("Эта команда работает внутри темы форума")
        return
    
    thread_id = msg.message_thread_id
    
    # Get UUID from command arguments or from database
    uuid = None
    if context.args and len(context.args) > 0:
        uuid = context.args[0].strip()
    else:
        # Try to get UUID from database for current user in this thread
        user_id = db_get_user_id(thread_id)
        if user_id:
            backend_data = db_get_user_backend_data(user_id)
            if backend_data:
                uuid = backend_data[0]  # UUID is first element of tuple
    
    if not uuid:
        await update.effective_message.reply_text(
            "Использование: /info [uuid]\n\n"
            "Укажите UUID пользователя или используйте команду в теме, где пользователь уже был привязан через /linkmail."
        )
        return
    
    # Send processing message
    processing_msg = await update.effective_message.reply_text("⏳ Получение информации о пользователе...")
    
    try:
        # Get user by UUID
        user_data = await get_user_by_uuid(uuid)
        
        if user_data is None:
            await processing_msg.edit_text(
                f"❌ Пользователь с UUID <code>{uuid}</code> не найден в системе.",
                parse_mode=ParseMode.HTML
            )
            return
        
        # Format and send user information
        user_info = format_user_info(user_data)
        
        await processing_msg.edit_text(
            user_info,
            parse_mode=ParseMode.HTML
        )
        
        logger.info(f"Retrieved user info for UUID {uuid}")
        
    except Exception as e:
        logger.exception(f"Error in info command: {e}")
        await processing_msg.edit_text(
            f"❌ Произошла ошибка при получении информации:\n<code>{str(e)}</code>",
            parse_mode=ParseMode.HTML
        )


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
                user_id = cq.from_user.id if cq.from_user else None
                thread_id = None
                if user_id:
                    # Get thread_id for this user to link rating with thread
                    thread_id = db_get_thread_id(user_id)
                    # Save rating to database
                    db_save_rating(user_id, rating, thread_id)
                    # Notify admins about the rating (pass user object from callback)
                    await notify_admin_about_rating(context, user_id, rating, thread_id, user_obj=cq.from_user)
                
                await cq.answer(f"Спасибо за оценку: {rating} ⭐")
                # Optionally edit the message to show rating was received
                try:
                    await cq.message.edit_text("Спасибо за вашу оценку!")
                except Exception:
                    pass
                logger.info("User %s gave rating %s", user_id, rating)
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


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show ratings statistics. Only available for owner or support chat admins."""
    if update.effective_user is None:
        return
    
    # Check if user is owner
    if OWNER_ID is not None and update.effective_user.id == OWNER_ID:
        pass  # Owner can always see stats
    # Check if user is in support chat and has admin rights
    elif SUPPORT_CHAT_ID is not None and update.effective_chat and update.effective_chat.id == SUPPORT_CHAT_ID:
        try:
            member = await context.bot.get_chat_member(SUPPORT_CHAT_ID, update.effective_user.id)
            if member.status not in ("administrator", "creator"):
                await update.effective_message.reply_text("Доступ запрещен. Только администраторы могут просматривать статистику.")
                return
        except Exception:
            await update.effective_message.reply_text("Ошибка проверки прав доступа.")
            return
    else:
        await update.effective_message.reply_text("Доступ запрещен.")
        return
    
    stats = db_get_ratings_stats()
    
    lines = ["📊 Статистика оценок поддержки\n"]
    lines.append(f"Всего оценок: {stats['total']}")
    lines.append(f"Средняя оценка: {stats['average']:.2f} ⭐")
    
    if stats['distribution']:
        lines.append("\nРаспределение оценок:")
        for rating in sorted(stats['distribution'].keys(), reverse=True):
            count = stats['distribution'][rating]
            bar = "█" * count if count <= 20 else "█" * 20
            lines.append(f"{rating} ⭐: {count} {bar}")
    else:
        lines.append("\nОценок пока нет.")
    
    await update.effective_message.reply_text("\n".join(lines))


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
        lines.append(f"ratings_notifications_thread_id: {RATINGS_NOTIFICATIONS_THREAD_ID}")
        
        # Show current thread_id if command is called from a forum topic
        if update.effective_message and update.effective_message.message_thread_id:
            lines.append(f"current_thread_id: {update.effective_message.message_thread_id}")
        
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
    # Statistics command
    application.add_handler(CommandHandler("stats", cmd_stats))
    if SUPPORT_CHAT_ID is not None:
        application.add_handler(CommandHandler("panel", cmd_panel))
        application.add_handler(CommandHandler("linkmail", cmd_linkmail))
        application.add_handler(CommandHandler("info", cmd_info))

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



