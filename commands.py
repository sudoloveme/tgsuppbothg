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
    db_add_promo_banner,
    db_get_all_promo_banners,
    db_delete_promo_banner,
    db_update_promo_banner,
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
        
        # Create keyboard with mini-app button if mini-app is configured
        from config import MINIAPP_URL
        
        keyboard = None
        if MINIAPP_URL:
            keyboard = InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    text="📊 Личный кабинет",
                    web_app={"url": MINIAPP_URL}
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


async def cmd_addbanner(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Add a promo banner. Reply to a photo message with /addbanner <link_url> [order]."""
    # Check admin permission
    is_allowed, error_msg = await check_admin_permission(update, context, allow_owner=True)
    if not is_allowed:
        await update.effective_message.reply_text(error_msg or "Доступ запрещен.")
        return
    
    # Check if message is a reply to a photo
    if not update.effective_message.reply_to_message:
        await update.effective_message.reply_text(
            "Использование: ответьте на фото командой /addbanner <ссылка> [порядок]\n\n"
            "Пример: /addbanner https://example.com 1"
        )
        return
    
    replied_message = update.effective_message.reply_to_message
    
    # Get photo
    photo = None
    if replied_message.photo:
        photo = replied_message.photo[-1]  # Get largest photo
    elif replied_message.document and replied_message.document.mime_type and replied_message.document.mime_type.startswith('image/'):
        photo = replied_message.document
    
    if not photo:
        await update.effective_message.reply_text("❌ Пожалуйста, ответьте на сообщение с изображением.")
        return
    
    # Get link URL and order from command args
    link_url = None
    display_order = 0
    
    if context.args and len(context.args) > 0:
        link_url = context.args[0].strip()
        if len(context.args) > 1:
            try:
                display_order = int(context.args[1])
            except ValueError:
                pass
    
    # Download photo
    try:
        processing_msg = await update.effective_message.reply_text("⏳ Загрузка изображения...")
        
        file = await context.bot.get_file(photo.file_id)
        file_ext = file.file_path.split('.')[-1] if '.' in file.file_path else 'jpg'
        
        # Save to banners directory
        from pathlib import Path
        banners_dir = Path(__file__).parent / "miniapp" / "banners"
        banners_dir.mkdir(parents=True, exist_ok=True)
        
        # Generate unique filename
        import time
        filename = f"banner_{int(time.time())}.{file_ext}"
        file_path = banners_dir / filename
        
        await file.download_to_drive(file_path)
        
        # Add to database
        banner_id = db_add_promo_banner(filename, link_url, display_order)
        
        if banner_id:
            await processing_msg.edit_text(
                f"✅ Баннер добавлен!\n\n"
                f"ID: {banner_id}\n"
                f"Файл: {filename}\n"
                f"Ссылка: {link_url or 'не указана'}\n"
                f"Порядок: {display_order}"
            )
        else:
            await processing_msg.edit_text("❌ Ошибка при добавлении баннера в базу данных.")
            file_path.unlink()  # Delete file if DB insert failed
            
    except Exception as e:
        logger.exception(f"Error adding banner: {e}")
        await update.effective_message.reply_text(
            f"❌ Ошибка при добавлении баннера:\n<code>{str(e)}</code>",
            parse_mode=ParseMode.HTML
        )


async def cmd_listbanners(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """List all promo banners."""
    # Check admin permission
    is_allowed, error_msg = await check_admin_permission(update, context, allow_owner=True)
    if not is_allowed:
        await update.effective_message.reply_text(error_msg or "Доступ запрещен.")
        return
    
    banners = db_get_all_promo_banners()
    
    if not banners:
        await update.effective_message.reply_text("📋 Баннеров пока нет.")
        return
    
    lines = ["📋 Список баннеров:\n"]
    for banner in banners:
        status = "✅ Активен" if banner['is_active'] else "❌ Неактивен"
        lines.append(
            f"ID: {banner['id']} | {status}\n"
            f"Файл: {banner['image_filename']}\n"
            f"Ссылка: {banner['link_url'] or 'не указана'}\n"
            f"Порядок: {banner['display_order']}\n"
        )
    
    await update.effective_message.reply_text("\n".join(lines))


async def cmd_delbanner(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Delete a promo banner. Usage: /delbanner <id>"""
    # Check admin permission
    is_allowed, error_msg = await check_admin_permission(update, context, allow_owner=True)
    if not is_allowed:
        await update.effective_message.reply_text(error_msg or "Доступ запрещен.")
        return
    
    if not context.args or len(context.args) == 0:
        await update.effective_message.reply_text("Использование: /delbanner <id>")
        return
    
    try:
        banner_id = int(context.args[0])
        
        # Get banner info before deleting
        banners = db_get_all_promo_banners()
        banner = next((b for b in banners if b['id'] == banner_id), None)
        
        if not banner:
            await update.effective_message.reply_text(f"❌ Баннер с ID {banner_id} не найден.")
            return
        
        # Delete file
        from pathlib import Path
        banners_dir = Path(__file__).parent / "miniapp" / "banners"
        file_path = banners_dir / banner['image_filename']
        if file_path.exists():
            file_path.unlink()
        
        # Delete from database
        if db_delete_promo_banner(banner_id):
            await update.effective_message.reply_text(f"✅ Баннер {banner_id} удален.")
        else:
            await update.effective_message.reply_text(f"❌ Ошибка при удалении баннера.")
            
    except ValueError:
        await update.effective_message.reply_text("❌ Неверный формат ID. Используйте число.")
    except Exception as e:
        logger.exception(f"Error deleting banner: {e}")
        await update.effective_message.reply_text(
            f"❌ Ошибка при удалении баннера:\n<code>{str(e)}</code>",
            parse_mode=ParseMode.HTML
        )


async def cmd_togglebanner(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Toggle banner active status. Usage: /togglebanner <id>"""
    # Check admin permission
    is_allowed, error_msg = await check_admin_permission(update, context, allow_owner=True)
    if not is_allowed:
        await update.effective_message.reply_text(error_msg or "Доступ запрещен.")
        return
    
    if not context.args or len(context.args) == 0:
        await update.effective_message.reply_text("Использование: /togglebanner <id>")
        return
    
    try:
        banner_id = int(context.args[0])
        
        # Get current status
        banners = db_get_all_promo_banners()
        banner = next((b for b in banners if b['id'] == banner_id), None)
        
        if not banner:
            await update.effective_message.reply_text(f"❌ Баннер с ID {banner_id} не найден.")
            return
        
        new_status = 0 if banner['is_active'] else 1
        if db_update_promo_banner(banner_id, is_active=new_status):
            status_text = "активирован" if new_status else "деактивирован"
            await update.effective_message.reply_text(f"✅ Баннер {banner_id} {status_text}.")
        else:
            await update.effective_message.reply_text(f"❌ Ошибка при обновлении баннера.")
            
    except ValueError:
        await update.effective_message.reply_text("❌ Неверный формат ID. Используйте число.")
    except Exception as e:
        logger.exception(f"Error toggling banner: {e}")
        await update.effective_message.reply_text(
            f"❌ Ошибка при обновлении баннера:\n<code>{str(e)}</code>",
            parse_mode=ParseMode.HTML
        )


async def cmd_bannerlink(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Set link URL for a banner. Usage: /bannerlink <id> <url>"""
    # Check admin permission
    is_allowed, error_msg = await check_admin_permission(update, context, allow_owner=True)
    if not is_allowed:
        await update.effective_message.reply_text(error_msg or "Доступ запрещен.")
        return
    
    if not context.args or len(context.args) < 2:
        await update.effective_message.reply_text("Использование: /bannerlink <id> <url>")
        return
    
    try:
        banner_id = int(context.args[0])
        link_url = context.args[1].strip()
        
        if db_update_promo_banner(banner_id, link_url=link_url):
            await update.effective_message.reply_text(
                f"✅ Ссылка для баннера {banner_id} обновлена:\n{link_url}"
            )
        else:
            await update.effective_message.reply_text(f"❌ Ошибка при обновлении ссылки.")
            
    except ValueError:
        await update.effective_message.reply_text("❌ Неверный формат ID. Используйте число.")
    except Exception as e:
        logger.exception(f"Error updating banner link: {e}")
        await update.effective_message.reply_text(
            f"❌ Ошибка при обновлении ссылки:\n<code>{str(e)}</code>",
            parse_mode=ParseMode.HTML
        )

