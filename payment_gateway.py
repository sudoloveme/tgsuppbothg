"""
Payment Gateway integration for Berekebank.kz
"""
import logging
import httpx
import uuid
from typing import Optional, Dict, Any
from config import PAYMENT_GATEWAY_URL, PAYMENT_GATEWAY_USERNAME, PAYMENT_GATEWAY_PASSWORD

logger = logging.getLogger("support-bot")

# Payment Gateway API endpoints
REGISTER_URL = f"{PAYMENT_GATEWAY_URL}/payment/rest/register.do"
GET_ORDER_STATUS_URL = f"{PAYMENT_GATEWAY_URL}/payment/rest/getOrderStatusExtended.do"
DEPOSIT_URL = f"{PAYMENT_GATEWAY_URL}/payment/rest/deposit.do"

# Currency codes
CURRENCY_KZT = 398  # Тенге
CURRENCY_KGZ = 417  # Кыргызский сом
CURRENCY_RUB = 643  # Российский рубль
CURRENCY_CNY = 156  # Китайский юань

# Order status codes
ORDER_STATUS_PRE_AUTH = 1  # Заказ зарегистрирован, но не оплачен (требуется завершение)
ORDER_STATUS_SUCCESS = 2  # Успешный платеж (оплачен и завершен)


async def register_order(
    amount: int,
    currency: int,
    return_url: str,
    description: str,
    order_number: Optional[str] = None,
    language: str = "ru"
) -> Optional[Dict[str, Any]]:
    """
    Регистрация заказа в платежном шлюзе.
    
    Args:
        amount: Сумма платежа в минимальных единицах валюты (тиыны для KZT)
        currency: Код валюты (398 для KZT)
        return_url: URL для возврата после оплаты
        description: Описание заказа
        order_number: Номер заказа (если не указан, генерируется автоматически)
        language: Язык интерфейса (ru, en, kz)
    
    Returns:
        Dict с orderId и formUrl, или None в случае ошибки
    """
    try:
        # Генерируем orderNumber, если не указан
        if not order_number:
            # Используем UUID без дефисов и ограничиваем длину до 32 символов
            order_number = uuid.uuid4().hex[:32]
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                REGISTER_URL,
                data={
                    "amount": amount,
                    "currency": currency,
                    "userName": PAYMENT_GATEWAY_USERNAME,
                    "password": PAYMENT_GATEWAY_PASSWORD,
                    "returnUrl": return_url,
                    "description": description,
                    "orderNumber": order_number,
                    "language": language
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"}
            )
            
            response.raise_for_status()
            result = response.json()
            
            # Проверяем errorCode: "0" означает успех, другие значения - ошибки
            if "errorCode" in result and result.get("errorCode") != "0":
                logger.error(f"Payment gateway error: {result.get('errorMessage', 'Unknown error')}")
                return None
            
            logger.info(f"Order registered successfully: {result.get('orderId')}")
            return result
            
    except httpx.HTTPError as e:
        logger.exception(f"HTTP error while registering order: {e}")
        return None
    except Exception as e:
        logger.exception(f"Error registering order: {e}")
        return None


async def get_order_status(order_id: str, language: str = "ru") -> Optional[Dict[str, Any]]:
    """
    Получение статуса заказа из платежного шлюза.
    
    Args:
        order_id: ID заказа, полученный при регистрации
        language: Язык интерфейса (ru, en, kz)
    
    Returns:
        Dict с информацией о статусе заказа, или None в случае ошибки
    """
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                GET_ORDER_STATUS_URL,
                data={
                    "userName": PAYMENT_GATEWAY_USERNAME,
                    "password": PAYMENT_GATEWAY_PASSWORD,
                    "orderId": order_id,
                    "language": language
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"}
            )
            
            response.raise_for_status()
            result = response.json()
            
            # Проверяем errorCode: "0" означает успех, другие значения - ошибки
            if "errorCode" in result and result.get("errorCode") != "0":
                logger.error(f"Payment gateway error: {result.get('errorMessage', 'Unknown error')}")
                return None
            
            logger.info(f"Order status retrieved: orderId={order_id}, status={result.get('orderStatus')}")
            return result
            
    except httpx.HTTPError as e:
        logger.exception(f"HTTP error while getting order status: {e}")
        return None
    except Exception as e:
        logger.exception(f"Error getting order status: {e}")
        return None


async def deposit_order(order_id: str, amount: Optional[int] = None) -> Optional[Dict[str, Any]]:
    """
    Завершение заказа (списание средств) для заказов со статусом 1 (PRE_AUTH).
    
    Args:
        order_id: ID заказа
        amount: Сумма для списания (если не указана, списывается полная сумма)
    
    Returns:
        Dict с результатом операции, или None в случае ошибки
    """
    try:
        data = {
            "userName": PAYMENT_GATEWAY_USERNAME,
            "password": PAYMENT_GATEWAY_PASSWORD,
            "orderId": order_id
        }
        
        if amount is not None:
            data["amount"] = amount
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                DEPOSIT_URL,
                data=data,
                headers={"Content-Type": "application/x-www-form-urlencoded"}
            )
            
            response.raise_for_status()
            result = response.json()
            
            # Проверяем errorCode: "0" означает успех, другие значения - ошибки
            if "errorCode" in result and result.get("errorCode") != "0":
                logger.error(f"Payment gateway deposit error: {result.get('errorMessage', 'Unknown error')}")
                return None
            
            logger.info(f"Order deposited successfully: orderId={order_id}")
            return result
            
    except httpx.HTTPError as e:
        logger.exception(f"HTTP error while depositing order: {e}")
        return None
    except Exception as e:
        logger.exception(f"Error depositing order: {e}")
        return None


def is_order_paid(order_status: int) -> bool:
    """
    Проверка, является ли заказ оплаченным.
    
    Args:
        order_status: Статус заказа из платежного шлюза
    
    Returns:
        True если заказ оплачен (статус 1 или 2), False в противном случае
    """
    return order_status in [ORDER_STATUS_PRE_AUTH, ORDER_STATUS_SUCCESS]


def convert_amount_to_minor_units(amount: float, currency: int) -> int:
    """
    Конвертация суммы в минимальные единицы валюты.
    
    Args:
        amount: Сумма в основных единицах валюты
        currency: Код валюты
    
    Returns:
        Сумма в минимальных единицах (тиыны, копейки и т.д.)
    """
    # Для большинства валют минимальная единица = 0.01 основной единицы
    # Для KZT: 1 тенге = 100 тиынов
    if currency == CURRENCY_KZT:
        return int(amount * 100)
    elif currency in [CURRENCY_KGZ, CURRENCY_RUB]:
        return int(amount * 100)
    elif currency == CURRENCY_CNY:
        return int(amount * 100)
    else:
        # По умолчанию умножаем на 100
        return int(amount * 100)

