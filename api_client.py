"""
API client for backend integration.
"""
import logging
import os
from typing import Dict, Any, Optional

from dotenv import load_dotenv
import httpx

# Load environment variables from .env if present
load_dotenv()

logger = logging.getLogger("support-bot")

# Backend API URL and API Key from environment
BACKEND_API_URL = os.getenv("BACKEND_API_URL", "").strip()
BACKEND_API_KEY = os.getenv("BACKEND_API_KEY", "").strip()

# Log configuration status (without exposing the key)
if BACKEND_API_URL:
    logger.info(f"Backend API configured: URL={BACKEND_API_URL}, API_KEY={'***' if BACKEND_API_KEY else 'NOT SET'}")
else:
    logger.warning("BACKEND_API_URL is not configured. API integration will not work.")


def _get_headers() -> dict:
    """Get headers for API requests including authorization."""
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    if BACKEND_API_KEY:
        headers["Authorization"] = f"Bearer {BACKEND_API_KEY}"
        # Alternative: if API uses X-API-Key header
        # headers["X-API-Key"] = BACKEND_API_KEY
    return headers


async def get_user_by_uuid(uuid: str) -> Optional[Dict[str, Any]]:
    """
    Get user information by UUID.
    
    Args:
        uuid: User UUID
        
    Returns:
        User data dict or None if not found/error
    """
    if not BACKEND_API_URL:
        logger.error("BACKEND_API_URL is not configured")
        return None
    
    url = f"{BACKEND_API_URL.rstrip('/')}/api/users/uuid/{uuid}"
    
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(url, headers=_get_headers())
            response.raise_for_status()
            data = response.json()
            
            # Extract user from response
            if "response" in data:
                return data["response"]
            return None
    except httpx.HTTPStatusError as e:
        logger.error(f"HTTP error getting user by UUID {uuid}: {e.response.status_code}")
        return None
    except httpx.RequestError as e:
        logger.error(f"Request error getting user by UUID {uuid}: {e}")
        return None
    except Exception as e:
        logger.exception(f"Unexpected error getting user by UUID {uuid}: {e}")
        return None


async def get_user_by_email(email: str) -> Optional[Dict[str, Any]]:
    """
    Get user information by email.
    
    Args:
        email: User email address
        
    Returns:
        User data dict or None if not found/error
    """
    if not BACKEND_API_URL:
        logger.error("BACKEND_API_URL is not configured")
        return None
    
    url = f"{BACKEND_API_URL.rstrip('/')}/api/users/email/{email}"
    
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(url, headers=_get_headers())
            response.raise_for_status()
            data = response.json()
            
            # Extract first user from response array
            if "response" in data and isinstance(data["response"], list) and len(data["response"]) > 0:
                return data["response"][0]
            return None
    except httpx.HTTPStatusError as e:
        logger.error(f"HTTP error getting user by email {email}: {e.response.status_code}")
        return None
    except httpx.RequestError as e:
        logger.error(f"Request error getting user by email {email}: {e}")
        return None
    except Exception as e:
        logger.exception(f"Unexpected error getting user by email {email}: {e}")
        return None


async def update_user_telegram_id(uuid: str, telegram_id: int) -> Optional[Dict[str, Any]]:
    """
    Update user's Telegram ID.
    
    Args:
        uuid: User UUID
        telegram_id: Telegram user ID
        
    Returns:
        Updated user data dict or None if error
    """
    if not BACKEND_API_URL:
        logger.error("BACKEND_API_URL is not configured")
        return None
    
    url = f"{BACKEND_API_URL.rstrip('/')}/api/users/update"
    
    payload = {
        "uuid": uuid,
        "telegramId": telegram_id
    }
    
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(url, json=payload, headers=_get_headers())
            response.raise_for_status()
            data = response.json()
            
            if "response" in data:
                return data["response"]
            return None
    except httpx.HTTPStatusError as e:
        logger.error(f"HTTP error updating user telegram_id: {e.response.status_code}")
        if e.response.status_code == 400:
            try:
                error_data = e.response.json()
                logger.error(f"Error details: {error_data}")
            except:
                pass
        return None
    except httpx.RequestError as e:
        logger.error(f"Request error updating user telegram_id: {e}")
        return None
    except Exception as e:
        logger.exception(f"Unexpected error updating user telegram_id: {e}")
        return None


def format_user_info(user_data: Dict[str, Any]) -> str:
    """
    Format user information for display.
    
    Args:
        user_data: User data dictionary
        
    Returns:
        Formatted string with user information
    """
    lines = []
    lines.append("👤 Информация о пользователе\n")
    
    # Basic info
    if "username" in user_data and user_data["username"]:
        lines.append(f"Username: <code>{user_data['username']}</code>")
    if "email" in user_data and user_data["email"]:
        lines.append(f"Email: <code>{user_data['email']}</code>")
    if "status" in user_data:
        status_emoji = "✅" if user_data["status"] == "ACTIVE" else "❌"
        lines.append(f"Status: {status_emoji} <code>{user_data['status']}</code>")
    
    # UUIDs
    if "uuid" in user_data:
        lines.append(f"\nUUID: <code>{user_data['uuid']}</code>")
    if "shortUuid" in user_data and user_data["shortUuid"]:
        lines.append(f"Short UUID: <code>{user_data['shortUuid']}</code>")
    if "subscriptionUuid" in user_data and user_data["subscriptionUuid"]:
        lines.append(f"Subscription UUID: <code>{user_data['subscriptionUuid']}</code>")
    
    # Telegram ID
    if "telegramId" in user_data:
        tg_id = user_data["telegramId"]
        if tg_id and tg_id != 0:
            lines.append(f"\nTelegram ID: <code>{tg_id}</code>")
        else:
            lines.append(f"\nTelegram ID: <code>Не привязан</code>")
    
    # Traffic info
    if "usedTrafficBytes" in user_data or "trafficLimitBytes" in user_data:
        lines.append("\n📊 Трафик:")
        if "usedTrafficBytes" in user_data:
            used = user_data["usedTrafficBytes"]
            used_gb = used / (1024 ** 3) if used else 0
            lines.append(f"Использовано: <code>{used_gb:.2f} GB</code> ({used:,} bytes)")
        
        if "lifetimeUsedTrafficBytes" in user_data:
            lifetime = user_data["lifetimeUsedTrafficBytes"]
            lifetime_gb = lifetime / (1024 ** 3) if lifetime else 0
            lines.append(f"Всего использовано: <code>{lifetime_gb:.2f} GB</code> ({lifetime:,} bytes)")
        
        if "trafficLimitBytes" in user_data and user_data["trafficLimitBytes"]:
            limit = user_data["trafficLimitBytes"]
            limit_gb = limit / (1024 ** 3)
            lines.append(f"Лимит: <code>{limit_gb:.2f} GB</code> ({limit:,} bytes)")
        
        if "trafficLimitStrategy" in user_data:
            lines.append(f"Стратегия лимита: <code>{user_data['trafficLimitStrategy']}</code>")
    
    # Dates
    if "expireAt" in user_data and user_data["expireAt"]:
        lines.append(f"\n📅 Истекает: <code>{user_data['expireAt']}</code>")
    if "createdAt" in user_data and user_data["createdAt"]:
        lines.append(f"Создан: <code>{user_data['createdAt']}</code>")
    if "updatedAt" in user_data and user_data["updatedAt"]:
        lines.append(f"Обновлен: <code>{user_data['updatedAt']}</code>")
    
    # Connection info
    if "onlineAt" in user_data and user_data["onlineAt"]:
        lines.append(f"\n🌐 Был онлайн: <code>{user_data['onlineAt']}</code>")
    if "lastConnectedNode" in user_data and user_data["lastConnectedNode"]:
        node = user_data["lastConnectedNode"]
        if "nodeName" in node:
            lines.append(f"Последний узел: <code>{node['nodeName']}</code>")
        if "connectedAt" in node:
            lines.append(f"Подключен: <code>{node['connectedAt']}</code>")
    
    # Subscription URL
    if "subscriptionUrl" in user_data and user_data["subscriptionUrl"]:
        lines.append(f"\n🔗 Subscription URL: <code>{user_data['subscriptionUrl']}</code>")
    
    # Passwords and UUIDs
    if "trojanPassword" in user_data and user_data["trojanPassword"]:
        lines.append(f"\n🔑 Trojan Password: <code>{user_data['trojanPassword']}</code>")
    if "vlessUuid" in user_data and user_data["vlessUuid"]:
        lines.append(f"VLESS UUID: <code>{user_data['vlessUuid']}</code>")
    if "ssPassword" in user_data and user_data["ssPassword"]:
        lines.append(f"SS Password: <code>{user_data['ssPassword']}</code>")
    
    # Subscription info
    if "subLastUserAgent" in user_data and user_data["subLastUserAgent"]:
        lines.append(f"\n📱 Last User Agent: <code>{user_data['subLastUserAgent']}</code>")
    if "subLastOpenedAt" in user_data and user_data["subLastOpenedAt"]:
        lines.append(f"Подписка открыта: <code>{user_data['subLastOpenedAt']}</code>")
    if "subRevokedAt" in user_data and user_data["subRevokedAt"]:
        lines.append(f"Подписка отозвана: <code>{user_data['subRevokedAt']}</code>")
    if "lastTrafficResetAt" in user_data and user_data["lastTrafficResetAt"]:
        lines.append(f"Последний сброс трафика: <code>{user_data['lastTrafficResetAt']}</code>")
    
    # Active User Inbounds
    if "activeUserInbounds" in user_data and user_data["activeUserInbounds"]:
        inbounds = user_data["activeUserInbounds"]
        if isinstance(inbounds, list) and len(inbounds) > 0:
            lines.append(f"\n🔌 Активные инбаунды ({len(inbounds)}):")
            for i, inbound in enumerate(inbounds, 1):
                if isinstance(inbound, dict):
                    inbound_lines = []
                    if "uuid" in inbound:
                        inbound_lines.append(f"UUID: <code>{inbound['uuid']}</code>")
                    if "tag" in inbound:
                        inbound_lines.append(f"Tag: <code>{inbound['tag']}</code>")
                    if "type" in inbound:
                        inbound_lines.append(f"Type: <code>{inbound['type']}</code>")
                    if "network" in inbound:
                        inbound_lines.append(f"Network: <code>{inbound['network']}</code>")
                    if "security" in inbound:
                        inbound_lines.append(f"Security: <code>{inbound['security']}</code>")
                    if inbound_lines:
                        lines.append(f"  {i}. {' | '.join(inbound_lines)}")
    
    # Description
    if "description" in user_data and user_data["description"]:
        lines.append(f"\n📝 Описание: <code>{user_data['description']}</code>")
    
    return "\n".join(lines)


async def get_traffic_usage_range(uuid: str, start_date: str, end_date: str) -> Optional[list]:
    """
    Get traffic usage statistics for a date range.
    
    Args:
        uuid: User UUID
        start_date: Start date in ISO format (e.g., "2025-11-01T00:00:00.000Z")
        end_date: End date in ISO format (e.g., "2025-11-05T23:59:59.999Z")
        
    Returns:
        List of usage statistics or None if error
    """
    if not BACKEND_API_URL:
        logger.warning("BACKEND_API_URL not configured. Cannot get traffic usage.")
        return None
    
    if not uuid:
        logger.warning("UUID is required for traffic usage query")
        return None
    
    try:
        url = f"{BACKEND_API_URL}/api/users/stats/usage/range/{uuid}"
        params = {
            "start": start_date,
            "end": end_date
        }
        
        logger.info(f"Fetching traffic usage for UUID {uuid} from {start_date} to {end_date}")
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                url,
                headers=_get_headers(),
                params=params
            )
            
            if response.status_code == 200:
                data = response.json()
                # API returns {"response": [...]}
                if isinstance(data, dict) and "response" in data:
                    return data["response"]
                elif isinstance(data, list):
                    return data
                else:
                    logger.warning(f"Unexpected response format: {data}")
                    return None
            else:
                logger.warning(f"Failed to get traffic usage: HTTP {response.status_code}")
                try:
                    error_data = response.json()
                    logger.warning(f"Error response: {error_data}")
                except:
                    logger.warning(f"Error response text: {response.text}")
                return None
                
    except httpx.TimeoutException:
        logger.error(f"Timeout getting traffic usage for UUID {uuid}")
        return None
    except Exception as e:
        logger.exception(f"Error getting traffic usage for UUID {uuid}: {e}")
        return None


async def create_user(email: str, telegram_id: int) -> Optional[Dict[str, Any]]:
    """
    Create a new user on the backend.
    
    Args:
        email: User's email address
        telegram_id: User's Telegram ID
        
    Returns:
        Created user data dict or None if error
    """
    if not BACKEND_API_URL:
        logger.error("BACKEND_API_URL is not configured")
        return None
    
    url = f"{BACKEND_API_URL.rstrip('/')}/api/users"
    
    # Generate username from email (replace special chars with underscore)
    import re
    username = re.sub(r'[^a-zA-Z0-9]', '_', email.lower())
    
    # Calculate expireAt (current date + 10 seconds)
    from datetime import datetime, timedelta
    expire_at = (datetime.now() + timedelta(seconds=10)).isoformat() + 'Z'
    
    payload = {
        "username": username,
        "status": "DISABLED",
        "trafficLimitBytes": 1,
        "trafficLimitStrategy": "MONTH",
        "expireAt": expire_at,
        "telegramId": telegram_id,
        "email": email,
        "activateAllInbounds": True
    }
    
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(url, json=payload, headers=_get_headers())
            response.raise_for_status()
            data = response.json()
            
            if "response" in data:
                return data["response"]
            return None
    except httpx.HTTPStatusError as e:
        logger.error(f"HTTP error creating user: {e.response.status_code}")
        if e.response.status_code == 400:
            try:
                error_data = e.response.json()
                logger.error(f"Error details: {error_data}")
            except:
                logger.warning(f"Error response text: {e.response.text}")
        return None
    except httpx.RequestError as e:
        logger.error(f"Request error creating user: {e}")
        return None
    except Exception as e:
        logger.exception(f"Unexpected error creating user: {e}")
        return None

