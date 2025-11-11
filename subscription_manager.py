"""
Subscription management module.
Handles subscription updates after successful payments.
"""
import logging
from datetime import datetime, timedelta
from typing import Optional

from api_client import get_user_by_uuid, update_user_subscription

logger = logging.getLogger("support-bot")

# Try to import dateutil parser, fallback to datetime.fromisoformat
try:
    from dateutil import parser
except ImportError:
    parser = None


async def update_user_subscription_after_payment(uuid: str, plan_days: int) -> None:
    """
    Update user subscription after successful payment.
    Handles three scenarios:
    1. New user (DISABLED) - add days to current date
    2. Active subscription renewal (ACTIVE) - add days to existing expireAt
    3. Expired subscription renewal (EXPIRED) - add days to current date
    
    Args:
        uuid: User UUID
        plan_days: Number of days to add to subscription
    """
    try:
        logger.info(f"Starting subscription update for UUID: {uuid}, plan_days: {plan_days}")
        
        # Получаем информацию о пользователе
        user_data = await get_user_by_uuid(uuid)
        if not user_data:
            logger.error(f"User not found for UUID: {uuid}")
            return
        
        user_status = user_data.get('status', '').upper()
        current_expire_at = user_data.get('expireAt')
        logger.info(f"User status: {user_status}, current_expire_at: {current_expire_at}")
        
        # Вычисляем новую дату окончания
        if user_status == 'ACTIVE' and current_expire_at:
            # Сценарий 2: Продление активной подписки
            # Добавляем дни к существующей дате окончания
            try:
                if parser:
                    expire_date = parser.isoparse(current_expire_at)
                else:
                    # Fallback: use datetime.fromisoformat (Python 3.7+)
                    expire_date_str = current_expire_at.replace('Z', '+00:00')
                    expire_date = datetime.fromisoformat(expire_date_str)
                    # Remove timezone info for calculation
                    if expire_date.tzinfo:
                        expire_date = expire_date.replace(tzinfo=None)
                new_expire_at = expire_date + timedelta(days=plan_days)
            except Exception as e:
                logger.warning(f"Error parsing expireAt {current_expire_at}, using current date: {e}")
                new_expire_at = datetime.now() + timedelta(days=plan_days)
        else:
            # Сценарий 1 (DISABLED) или 3 (EXPIRED): Добавляем дни к текущей дате
            new_expire_at = datetime.now() + timedelta(days=plan_days)
        
        # Форматируем дату в ISO формат
        expire_at_iso = new_expire_at.strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'
        logger.info(f"Calculated new expire_at: {expire_at_iso}")
        
        # Обновляем подписку через API
        logger.info(f"Sending update request to backend for UUID {uuid}")
        result = await update_user_subscription(
            uuid=uuid,
            status='ACTIVE',
            traffic_limit_bytes=214748364800,  # 200 GB
            traffic_limit_strategy='MONTH',
            expire_at=expire_at_iso,
            used_traffic_bytes=0
        )
        
        if result:
            logger.info(f"Successfully updated subscription for UUID {uuid}: {plan_days} days, expires at {expire_at_iso}")
        else:
            logger.error(f"Failed to update subscription for UUID {uuid} - API returned None")
            
    except Exception as e:
        logger.exception(f"Error in update_user_subscription_after_payment for UUID {uuid}: {e}")

