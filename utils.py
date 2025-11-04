"""
Utility functions and UI helpers.
"""
import logging
from typing import Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from config import SUPPORT_CHAT_ID, RATINGS_NOTIFICATIONS_THREAD_ID
from database import db_get_thread_state, db_set_thread_id, db_upsert_thread_state

logger = logging.getLogger("support-bot")

# Global mappings (will be initialized from bot.py)
user_id_to_thread_id: dict[int, int] = {}
support_msg_id_to_origin: dict[int, tuple[int, int]] = {}
owner_msg_id_to_origin: dict[int, tuple[int, int]] = {}


def init_mappings(
    support_msg_map: dict,
    owner_msg_map: dict,
    user_to_thread: dict
) -> None:
    """Initialize global mappings from bot.py."""
    global support_msg_id_to_origin, owner_msg_id_to_origin, user_id_to_thread_id
    support_msg_id_to_origin = support_msg_map
    owner_msg_id_to_origin = owner_msg_map
    user_id_to_thread_id = user_to_thread


def build_thread_keyboard(thread_id: int) -> InlineKeyboardMarkup:
    """Build keyboard for thread management (open/close)."""
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


async def notify_admin_about_rating(
    context: ContextTypes.DEFAULT_TYPE,
    user_id: int,
    rating: int,
    thread_id: Optional[int] = None,
    user_obj=None,
) -> None:
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


def format_user_header(update) -> str:
    """Format user header for display."""
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


def display_name(update) -> str:
    """Get display name for user."""
    user = update.effective_user
    if not user:
        return "Пользователь"
    if user.full_name:
        return user.full_name
    if user.username:
        return f"@{user.username}"
    return f"id:{user.id}"


async def ensure_forum_topic_for_user(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> Optional[int]:
    """Ensure forum topic exists for user, create if needed."""
    if SUPPORT_CHAT_ID is None:
        return None
    user = update.effective_user
    if user is None:
        return None
    if user.id in user_id_to_thread_id:
        return user_id_to_thread_id[user.id]
    # Try DB
    from database import db_get_thread_id
    cached = db_get_thread_id(user.id)
    if cached is not None:
        user_id_to_thread_id[user.id] = cached
        return cached
    name = display_name(update)[:128]
    try:
        topic = await context.bot.create_forum_topic(chat_id=SUPPORT_CHAT_ID, name=name)
        thread_id = topic.message_thread_id
        user_id_to_thread_id[user.id] = thread_id
        db_set_thread_id(user.id, thread_id)
        db_upsert_thread_state(thread_id, status="active", archived=0)
        header = format_user_header(update)
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

