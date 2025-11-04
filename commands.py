"""
Bot commands module.
All command handlers for the bot.
"""
import logging

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from config import SUPPORT_CHAT_ID, OWNER_ID, RATINGS_NOTIFICATIONS_THREAD_ID
from database import (
    db_get_user_id,
    db_get_user_backend_data,
    db_save_user_backend_data,
    db_get_ratings_stats,
)
from api_client import get_user_by_email, get_user_by_uuid, update_user_telegram_id, format_user_info
from helpers import check_admin_and_forum, check_admin_permission
from utils import (
    build_thread_keyboard,
    ensure_forum_topic_for_user,
    format_user_header,
    support_msg_id_to_origin,
)

logger = logging.getLogger("support-bot")


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start command."""
    if update.effective_user is None:
        return

    user = update.effective_user

    if SUPPORT_CHAT_ID is not None:
        # Ensure topic exists immediately when user presses /start
        thread_id = await ensure_forum_topic_for_user(update, context)
        logger.info("/start from user_id=%s → thread_id=%s", user.id, str(thread_id))
        
        # Create keyboard with mini-app button if user has UUID
        from database import db_get_user_backend_data
        from config import MINIAPP_URL
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        
        backend_data = db_get_user_backend_data(user.id)
        keyboard = None
        if backend_data and backend_data[0]:  # UUID exists
            uuid = backend_data[0]
            mini_app_url = f"{MINIAPP_URL}?uuid={uuid}" if MINIAPP_URL else None
            if mini_app_url:
                keyboard = InlineKeyboardMarkup([[
                    InlineKeyboardButton(
                        text="📊 Моя подписка",
                        web_app={"url": mini_app_url}
                    )
                ]])
        
        await update.effective_message.reply_text(
            "Здравствуйте! Опишите вашу проблему или вопрос. Для ускорения оказания помощи, укажите сразу ваш email, а также скриншоты проблемы если возможно. Мы ответим вам в течение 24 часов.",
            reply_markup=keyboard
        )
        # Post a note to operators that user started the dialog
        if thread_id is not None:
            try:
                header = format_user_header(update)
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
    """Handle /id command."""
    if update.effective_chat is None:
        return
    await update.effective_message.reply_text(str(update.effective_chat.id))


async def cmd_subscription(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /subscription command - open mini-app with subscription info."""
    if update.effective_user is None:
        return
    
    from database import db_get_user_backend_data
    from config import MINIAPP_URL
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    
    user_id = update.effective_user.id
    backend_data = db_get_user_backend_data(user_id)
    
    if not backend_data or not backend_data[0]:
        await update.effective_message.reply_text(
            "❌ Ваш аккаунт не привязан к системе.\n\n"
            "Обратитесь в поддержку и попросите администратора привязать ваш email через команду /linkmail."
        )
        return
    
    uuid = backend_data[0]
    mini_app_url = f"{MINIAPP_URL}?uuid={uuid}" if MINIAPP_URL else None
    
    if not mini_app_url:
        await update.effective_message.reply_text(
            "❌ Mini-app не настроен. Обратитесь к администратору."
        )
        return
    
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton(
            text="📊 Открыть мою подписку",
            web_app={"url": mini_app_url}
        )
    ]])
    
    await update.effective_message.reply_text(
        "📊 Нажмите кнопку ниже, чтобы посмотреть информацию о вашей подписке:",
        reply_markup=keyboard
    )


async def cmd_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /panel command."""
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
    # Check admin permission and forum mode with thread requirement
    is_allowed, error_msg, thread_id = await check_admin_and_forum(
        update, context, require_thread=True, allow_owner=False
    )
    if not is_allowed:
        await update.effective_message.reply_text(error_msg)
        return
    
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
    # Check admin permission and forum mode with thread requirement
    is_allowed, error_msg, thread_id = await check_admin_and_forum(
        update, context, require_thread=True, allow_owner=False
    )
    if not is_allowed:
        await update.effective_message.reply_text(error_msg)
        return
    
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


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show ratings statistics. Only available for owner or support chat admins."""
    # Check admin permission (owner allowed)
    is_allowed, error_msg = await check_admin_permission(update, context, allow_owner=True)
    if not is_allowed:
        await update.effective_message.reply_text(error_msg or "Доступ запрещен.")
        return
    
    stats = db_get_ratings_stats()
    
    lines = ["📊 Статистика оценок поддержки\n"]
    lines.append(f"Всего оценок: {stats['total']}")
    lines.append(f"Средняя оценка: {stats['average']:.2f} ⭐")
    
    if stats['distribution']:
        lines.append("\nРаспределение оценок:")
        for rating in sorted(stats['distribution'].keys(), reverse=True):
            count = stats['distribution'][rating]
            bar = "🔥" * count if count <= 20 else "⚠️" * 20
            lines.append(f"{rating} ⭐: {count} {bar}")
    else:
        lines.append("\nОценок пока нет.")
    
    await update.effective_message.reply_text("\n".join(lines))


async def cmd_diag(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Diagnostics: show current mode, chat settings, and bot permissions."""
    # Check admin permission (owner allowed)
    is_allowed, error_msg = await check_admin_permission(update, context, allow_owner=True)
    if not is_allowed:
        await update.effective_message.reply_text(error_msg or "Доступ запрещен.")
        return
    
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

