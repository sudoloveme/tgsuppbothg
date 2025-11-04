"""
Helper functions for bot commands - permission checks and common validations.
"""
import logging
from typing import Optional

from telegram import Update
from telegram.ext import ContextTypes

logger = logging.getLogger("support-bot")

# Import config (will be set from bot.py)
SUPPORT_CHAT_ID: Optional[int] = None
OWNER_ID: Optional[int] = None


def set_config(support_chat_id: Optional[int], owner_id: Optional[int]) -> None:
    """Set configuration values from bot.py."""
    global SUPPORT_CHAT_ID, OWNER_ID
    SUPPORT_CHAT_ID = support_chat_id
    OWNER_ID = owner_id


async def check_admin_permission(
    update: Update, context: ContextTypes.DEFAULT_TYPE, allow_owner: bool = True
) -> tuple[bool, Optional[str]]:
    """
    Check if user has admin permissions.
    
    Args:
        update: Update object
        context: Context object
        allow_owner: If True, owner can also access (default: True)
        
    Returns:
        Tuple of (is_allowed, error_message)
        If is_allowed is False, error_message contains the reason
    """
    if update.effective_user is None:
        return False, "Пользователь не найден"
    
    user_id = update.effective_user.id
    
    # Check if user is owner
    if allow_owner and OWNER_ID is not None and user_id == OWNER_ID:
        return True, None
    
    # Check if in support chat and is admin
    if SUPPORT_CHAT_ID is not None:
        if update.effective_chat is None or update.effective_chat.id != SUPPORT_CHAT_ID:
            return False, "Эта команда работает только в группе поддержки"
        
        try:
            member = await context.bot.get_chat_member(SUPPORT_CHAT_ID, user_id)
            if member.status not in ("administrator", "creator"):
                return False, "Доступ запрещен. Только администраторы могут использовать эту команду."
            return True, None
        except Exception as e:
            logger.exception("Error checking admin permission")
            return False, "Ошибка проверки прав доступа."
    
    return False, "Доступ запрещен."


async def check_forum_mode(
    update: Update, require_thread: bool = False
) -> tuple[bool, Optional[str], Optional[int]]:
    """
    Check if bot is in forum mode and optionally if command is in a thread.
    
    Args:
        update: Update object
        require_thread: If True, requires message to be in a forum thread
        
    Returns:
        Tuple of (is_valid, error_message, thread_id)
        thread_id is None if not required or not available
    """
    if SUPPORT_CHAT_ID is None:
        return False, "Эта команда работает только в режиме форума", None
    
    if update.effective_chat is None or update.effective_chat.id != SUPPORT_CHAT_ID:
        return False, "Эта команда работает только в группе поддержки", None
    
    if require_thread:
        msg = update.effective_message
        if msg is None or msg.message_thread_id is None:
            return False, "Эта команда работает внутри темы форума", None
        return True, None, msg.message_thread_id
    
    return True, None, None


async def check_admin_and_forum(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    require_thread: bool = False,
    allow_owner: bool = True,
) -> tuple[bool, Optional[str], Optional[int]]:
    """
    Combined check for admin permission and forum mode.
    
    Args:
        update: Update object
        context: Context object
        require_thread: If True, requires message to be in a forum thread
        allow_owner: If True, owner can also access
        
    Returns:
        Tuple of (is_allowed, error_message, thread_id)
    """
    # Check admin permission
    is_admin, admin_error = await check_admin_permission(update, context, allow_owner)
    if not is_admin:
        return False, admin_error, None
    
    # Check forum mode
    is_forum, forum_error, thread_id = await check_forum_mode(update, require_thread)
    if not is_forum:
        return False, forum_error, None
    
    return True, None, thread_id

