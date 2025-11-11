"""
Message handlers module.
Handlers for incoming messages, callbacks, and background jobs.
"""
import logging
import sqlite3

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from config import SUPPORT_CHAT_ID, OWNER_ID, ARCHIVE_AFTER_HOURS
from database import (
    db_get_thread_id,
    db_get_user_id,
    db_save_rating,
    db_upsert_thread_state,
    db_get_thread_state,
    db_touch_activity,
    _db_connect,
)
from utils import (
    build_thread_keyboard,
    send_rating_message,
    notify_admin_about_rating,
    ensure_forum_topic_for_user,
    format_user_header,
    support_msg_id_to_origin,
    owner_msg_id_to_origin,
)

logger = logging.getLogger("support-bot")


async def handle_callback_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle callback queries from inline buttons."""
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
    """Background job to archive inactive topics."""
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


async def handle_incoming_from_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle incoming messages from users."""
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

    if OWNER_ID is None:
        logger.warning("OWNER_ID is not configured. Use /id in your owner chat and set OWNER_ID env var.")

    # Forum mode: route into per-user topic
    if SUPPORT_CHAT_ID is not None:
        thread_id = await ensure_forum_topic_for_user(update, context)
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
        header = format_user_header(update)
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
    
    header = format_user_header(update)
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
    """Handle replies from owner/operator to forward to users."""
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


async def handle_pre_checkout_query(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle pre-checkout query for Telegram Stars payments."""
    query = update.pre_checkout_query
    if query is None:
        return
    
    try:
        # Получаем payload (invoice_payload) из запроса
        invoice_payload = query.invoice_payload
        
        if not invoice_payload:
            await query.answer(ok=False, error_message="Invalid invoice payload")
            logger.error("Pre-checkout query without invoice_payload")
            return
        
        # Проверяем, что заказ существует в БД
        from database import db_get_payment_order
        payment_order = db_get_payment_order(invoice_payload)
        
        if not payment_order:
            await query.answer(ok=False, error_message="Order not found")
            logger.error(f"Pre-checkout query for non-existent order: {invoice_payload}")
            return
        
        # Проверяем, что платеж принадлежит этому пользователю
        user_id = query.from_user.id if query.from_user else None
        if user_id and payment_order.get('telegram_id') != user_id:
            await query.answer(ok=False, error_message="This payment does not belong to you")
            logger.error(f"Pre-checkout query: order {invoice_payload} belongs to {payment_order.get('telegram_id')}, but query from {user_id}")
            return
        
        # Проверяем валюту (должна быть XTR для Stars)
        if query.currency != "XTR":
            await query.answer(ok=False, error_message="Invalid currency")
            logger.error(f"Pre-checkout query with invalid currency: {query.currency}")
            return
        
        # Проверяем сумму
        expected_amount = int(payment_order.get('amount', 0) * 100)  # Stars в минимальных единицах
        if query.total_amount != expected_amount:
            await query.answer(ok=False, error_message="Invalid amount")
            logger.error(f"Pre-checkout query: expected {expected_amount}, got {query.total_amount}")
            return
        
        # Все проверки пройдены, подтверждаем платеж
        await query.answer(ok=True)
        logger.info(f"Pre-checkout query approved: payload={invoice_payload}, user_id={user_id}, amount={query.total_amount}")
        
    except Exception as e:
        logger.exception(f"Error handling pre-checkout query: {e}")
        try:
            await query.answer(ok=False, error_message="Internal error")
        except Exception:
            pass


async def handle_successful_payment(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle successful Telegram Stars payment."""
    message = update.effective_message
    if message is None or message.successful_payment is None:
        return
    
    try:
        payment = message.successful_payment
        invoice_payload = payment.invoice_payload
        user_id = message.from_user.id if message.from_user else None
        
        if not invoice_payload:
            logger.error("Successful payment without invoice_payload")
            return
        
        logger.info(f"Processing successful Stars payment: payload={invoice_payload}, user_id={user_id}, amount={payment.total_amount}")
        
        # Получаем заказ из БД
        from database import db_get_payment_order, db_update_payment_order_status, db_mark_subscription_updated
        payment_order = db_get_payment_order(invoice_payload)
        
        if not payment_order:
            logger.error(f"Payment order not found: {invoice_payload}")
            return
        
        # Проверяем, что платеж принадлежит этому пользователю
        if user_id and payment_order.get('telegram_id') != user_id:
            logger.error(f"Payment {invoice_payload} belongs to telegram_id {payment_order.get('telegram_id')}, but payment from {user_id}")
            return
        
        # Проверяем, что подписка еще не обновлена (импотентность)
        if payment_order.get('subscription_updated'):
            logger.info(f"Subscription already updated for order {invoice_payload}, skipping")
            return
        
        # Обновляем статус заказа в БД
        db_update_payment_order_status(
            order_id=invoice_payload,
            status='PAID',
            status_data={
                'telegram_payment_charge_id': payment.telegram_payment_charge_id,
                'provider_payment_charge_id': payment.provider_payment_charge_id,
                'total_amount': payment.total_amount,
                'currency': payment.currency
            }
        )
        
        # Обновляем подписку пользователя
        uuid = payment_order.get('uuid')
        plan_days = payment_order.get('plan_days')
        
        if uuid and plan_days:
            logger.info(f"Updating subscription after Stars payment: uuid={uuid}, plan_days={plan_days}")
            
            # Импортируем функцию обновления подписки из miniapp_server
            import asyncio
            try:
                from miniapp_server import update_user_subscription_after_payment
                # Запускаем обновление подписки
                await update_user_subscription_after_payment(uuid, plan_days)
                
                # Помечаем, что подписка обновлена
                db_mark_subscription_updated(invoice_payload)
                
                logger.info(f"Subscription updated successfully for order {invoice_payload}")
                
                # Отправляем подтверждение пользователю
                try:
                    await message.reply_text(
                        f"✅ Оплата успешно завершена!\n\n"
                        f"Ваша подписка на {plan_days} дней активирована."
                    )
                except Exception as e:
                    logger.warning(f"Failed to send confirmation message: {e}")
                    
            except Exception as e:
                logger.exception(f"Error updating subscription after Stars payment: {e}")
        else:
            logger.error(f"Missing uuid or plan_days in payment order {invoice_payload}")
            
    except Exception as e:
        logger.exception(f"Error handling successful payment: {e}")

