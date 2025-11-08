"""
Payment Gateway integration for Berekebank.kz
"""
import logging
import httpx
from typing import Optional, Dict, Any
from config import PAYMENT_GATEWAY_URL, PAYMENT_GATEWAY_USERNAME, PAYMENT_GATEWAY_PASSWORD

logger = logging.getLogger("support-bot")

# Payment Gateway API endpoints
REGISTER_URL = f"{PAYMENT_GATEWAY_URL}/payment/rest/register.do"
GET_ORDER_STATUS_URL = f"{PAYMENT_GATEWAY_URL}/payment/rest/getOrderStatusExtended.do"

# Currency codes
CURRENCY_KZT = 398  # Тенге
CURRENCY_KGZ = 417  # Кыргызский сом
CURRENCY_RUB = 643  # Российский рубль
CURRENCY_CNY = 156  # Китайский юань

# Order status codes
ORDER_STATUS_SUCCESS = 2  # Успешный платеж


async def register_order(
    amount: int,
    currency: int,
    return_url: str,
    description: str,
    language: str = "ru"
) -> Optional[Dict[str, Any]]:
    """
    Регистрация заказа в платежном шлюзе.
    
    Args:
        amount: Сумма платежа в минимальных единицах валюты (тиыны для KZT)
        currency: Код валюты (398 для KZT)
        return_url: URL для возврата после оплаты
        description: Описание заказа
        language: Язык интерфейса (ru, en, kz)
    
    Returns:
        Dict с orderId и formUrl, или None в случае ошибки
    """
    try:
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
                    "language": language
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"}
            )
            
            response.raise_for_status()
            result = response.json()
            
            if "errorCode" in result:
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


async def get_order_status(order_id: str) -> Optional[Dict[str, Any]]:
    """
    Получение статуса заказа из платежного шлюза.
    
    Args:
        order_id: ID заказа, полученный при регистрации
    
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
                    "orderId": order_id
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"}
            )
            
            response.raise_for_status()
            result = response.json()
            
            if "errorCode" in result:
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

