"""
Cryptomus payment gateway integration
"""
import logging
import httpx
import hashlib
import json
import base64
from typing import Optional, Dict, Any
from config import CRYPTOMUS_MERCHANT, CRYPTOMUS_API_KEY, CRYPTOMUS_API_URL

logger = logging.getLogger("support-bot")

# Payment statuses
STATUS_PAID = "paid"
STATUS_PAID_OVER = "paid_over"
STATUS_FAIL = "fail"
STATUS_CANCEL = "cancel"
STATUS_PROCESS = "process"
STATUS_CONFIRM_CHECK = "confirm_check"
STATUS_CHECK = "check"

# Final statuses (payment completed or failed)
FINAL_SUCCESS_STATUSES = [STATUS_PAID, STATUS_PAID_OVER]
FINAL_FAIL_STATUSES = [STATUS_FAIL, STATUS_CANCEL]


def generate_sign(data: dict) -> str:
    """
    Generate sign for Cryptomus API request.
    
    According to Cryptomus documentation:
    sign = md5(base64_encode(json_encode(data)) . API_KEY)
    
    Args:
        data: Request payload as dictionary
    
    Returns:
        MD5 hash of base64-encoded JSON payload + API key
    """
    # 1. json_encode(data) - convert dict to JSON string
    payload_str = json.dumps(data, separators=(',', ':'))
    
    # 2. base64_encode(json_encode(data))
    payload_base64 = base64.b64encode(payload_str.encode('utf-8')).decode('utf-8')
    
    # 3. md5(base64_encode(...) . API_KEY)
    sign_string = payload_base64 + CRYPTOMUS_API_KEY
    return hashlib.md5(sign_string.encode('utf-8')).hexdigest()


async def create_payment(
    amount: str,
    currency: str = "USD",
    order_id: str = None
) -> Optional[Dict[str, Any]]:
    """
    Create payment order in Cryptomus.
    
    Args:
        amount: Payment amount as string (e.g., "15.00")
        currency: Currency code (default: "USD")
        order_id: Order ID (if not provided, will be generated)
    
    Returns:
        Dict with payment data including uuid and url, or None if error
    """
    if not CRYPTOMUS_MERCHANT or not CRYPTOMUS_API_KEY:
        logger.error("CRYPTOMUS_MERCHANT or CRYPTOMUS_API_KEY is not configured")
        return None
    
    if not CRYPTOMUS_API_URL:
        logger.error("CRYPTOMUS_API_URL is not configured")
        return None
    
    url = f"{CRYPTOMUS_API_URL.rstrip('/')}/v1/payment"
    
    payload = {
        "amount": str(amount),
        "currency": currency,
        "order_id": order_id or ""
    }
    
    sign = generate_sign(payload)
    
    headers = {
        "merchant": CRYPTOMUS_MERCHANT,
        "sign": sign,
        "Content-Type": "application/json"
    }
    
    try:
        logger.info(f"Creating Cryptomus payment: amount={amount}, currency={currency}, order_id={order_id}")
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(url, json=payload, headers=headers)
            logger.info(f"Cryptomus API response status: {response.status_code}")
            response.raise_for_status()
            data = response.json()
            
            if data.get("state") != 0:
                error_msg = data.get("message", "Unknown error")
                logger.error(f"Cryptomus API error: {error_msg}")
                return None
            
            result = data.get("result")
            if result:
                logger.info(f"Payment created successfully: uuid={result.get('uuid')}, url={result.get('url')}")
                return result
            
            logger.warning("No result in Cryptomus API response")
            return None
            
    except httpx.HTTPStatusError as e:
        logger.error(f"HTTP error creating Cryptomus payment: {e.response.status_code}")
        try:
            error_data = e.response.json()
            logger.error(f"Error details: {error_data}")
        except:
            logger.warning(f"Error response text: {e.response.text}")
        return None
    except httpx.RequestError as e:
        logger.error(f"Request error creating Cryptomus payment: {e}")
        return None
    except Exception as e:
        logger.exception(f"Unexpected error creating Cryptomus payment: {e}")
        return None


async def get_payment_info(uuid: str) -> Optional[Dict[str, Any]]:
    """
    Get payment information by UUID.
    
    Args:
        uuid: Payment UUID from create_payment response
    
    Returns:
        Dict with payment information, or None if error
    """
    if not CRYPTOMUS_MERCHANT or not CRYPTOMUS_API_KEY:
        logger.error("CRYPTOMUS_MERCHANT or CRYPTOMUS_API_KEY is not configured")
        return None
    
    if not CRYPTOMUS_API_URL:
        logger.error("CRYPTOMUS_API_URL is not configured")
        return None
    
    url = f"{CRYPTOMUS_API_URL.rstrip('/')}/v1/payment/info"
    
    payload = {
        "uuid": uuid
    }
    
    sign = generate_sign(payload)
    
    headers = {
        "merchant": CRYPTOMUS_MERCHANT,
        "sign": sign,
        "Content-Type": "application/json"
    }
    
    try:
        logger.info(f"Getting Cryptomus payment info: uuid={uuid}")
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(url, json=payload, headers=headers)
            logger.info(f"Cryptomus API response status: {response.status_code}")
            response.raise_for_status()
            data = response.json()
            
            if data.get("state") != 0:
                error_msg = data.get("message", "Unknown error")
                logger.error(f"Cryptomus API error: {error_msg}")
                return None
            
            result = data.get("result")
            if result:
                logger.info(f"Payment info retrieved: uuid={result.get('uuid')}, status={result.get('status')}")
                return result
            
            logger.warning("No result in Cryptomus API response")
            return None
            
    except httpx.HTTPStatusError as e:
        logger.error(f"HTTP error getting Cryptomus payment info: {e.response.status_code}")
        try:
            error_data = e.response.json()
            logger.error(f"Error details: {error_data}")
        except:
            logger.warning(f"Error response text: {e.response.text}")
        return None
    except httpx.RequestError as e:
        logger.error(f"Request error getting Cryptomus payment info: {e}")
        return None
    except Exception as e:
        logger.exception(f"Unexpected error getting Cryptomus payment info: {e}")
        return None


def is_payment_successful(status: str) -> bool:
    """
    Check if payment status indicates successful payment.
    
    Args:
        status: Payment status string
    
    Returns:
        True if payment is successful (paid or paid_over)
    """
    return status in FINAL_SUCCESS_STATUSES


def is_payment_failed(status: str) -> bool:
    """
    Check if payment status indicates failed payment.
    
    Args:
        status: Payment status string
    
    Returns:
        True if payment failed (fail or cancel)
    """
    return status in FINAL_FAIL_STATUSES


def is_payment_final(status: str) -> bool:
    """
    Check if payment status is final (completed or failed).
    
    Args:
        status: Payment status string
    
    Returns:
        True if payment is in final state
    """
    return is_payment_successful(status) or is_payment_failed(status)

