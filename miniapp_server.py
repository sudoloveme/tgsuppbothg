"""
HTTP server for serving mini-app static files and API endpoints.
"""
import logging
import asyncio
from pathlib import Path
from typing import Optional
from datetime import datetime, timedelta
try:
    from dateutil import parser
except ImportError:
    # Fallback: use datetime.fromisoformat if dateutil is not available
    parser = None

from aiohttp import web
from aiohttp.web_request import Request
from aiohttp.web_response import Response

from api_client import get_user_by_uuid, get_traffic_usage_range, get_user_by_email, create_user, update_user_telegram_id, update_user_subscription
import database
import otp_manager
import smtp_client
import payment_gateway
import cryptomus_client
import httpx
import uuid as uuid_module
from config import TELEGRAM_BOT_TOKEN

logger = logging.getLogger("support-bot")

# Path to mini-app directory
MINIAPP_DIR = Path(__file__).parent / "miniapp"
# Path to banners directory
BANNERS_DIR = Path(__file__).parent / "miniapp" / "banners"
BANNERS_DIR.mkdir(parents=True, exist_ok=True)
# Path to icons directory
ICONS_DIR = Path(__file__).parent / "miniapp" / "icons"
ICONS_DIR.mkdir(parents=True, exist_ok=True)
# Path to root directory (for logo.png)
ROOT_DIR = Path(__file__).parent


async def get_subscription_by_telegram_id(request: Request) -> Response:
    """
    API endpoint to get subscription data by Telegram ID.
    GET /api/subscription/telegram/{telegram_id}
    
    Logic:
    1. Get Telegram ID from URL
    2. Find UUID in local SQLite DB by Telegram ID
    3. Get user data from backend API by UUID
    4. Return subscription data
    """
    telegram_id_str = request.match_info.get('telegram_id')
    logger.info(f"Received request for Telegram ID: {telegram_id_str}")
    
    if not telegram_id_str:
        logger.warning("Telegram ID not provided in request")
        return web.json_response(
            {"error": "Telegram ID is required"},
            status=400,
            headers={
                'Access-Control-Allow-Origin': '*',
                'Access-Control-Allow-Methods': 'GET, OPTIONS',
                'Access-Control-Allow-Headers': 'Content-Type',
            }
        )

    try:
        telegram_id = int(telegram_id_str)
        logger.info(f"Processing request for Telegram ID: {telegram_id}")
        
        # Get UUID from database by Telegram ID
        from database import db_get_user_backend_data
        backend_data = db_get_user_backend_data(telegram_id)
        logger.info(f"Backend data for user {telegram_id}: {backend_data}")
        
        if not backend_data or not backend_data[0]:
            logger.warning(f"User {telegram_id} not found in database or not linked")
            return web.json_response(
                {"error": "User not found or not linked. Please contact support to link your account."},
                status=404,
                headers={
                    'Access-Control-Allow-Origin': '*',
                    'Access-Control-Allow-Methods': 'GET, OPTIONS',
                    'Access-Control-Allow-Headers': 'Content-Type',
                }
            )
        
        uuid = backend_data[0]
        logger.info(f"Found UUID for user {telegram_id}: {uuid}")
        
        # Get user data from backend API
        user_data = await get_user_by_uuid(uuid)
        logger.info(f"Backend API response for UUID {uuid}: {user_data is not None}")
        
        if not user_data:
            logger.warning(f"Subscription data not found for UUID: {uuid}")
            return web.json_response(
                {"error": "Subscription data not found"},
                status=404,
                headers={
                    'Access-Control-Allow-Origin': '*',
                    'Access-Control-Allow-Methods': 'GET, OPTIONS',
                    'Access-Control-Allow-Headers': 'Content-Type',
                }
            )

        # Return subscription data
        logger.info(f"Successfully returning subscription data for user {telegram_id}")
        return web.json_response(
            user_data,
            headers={
                'Access-Control-Allow-Origin': '*',
                'Access-Control-Allow-Methods': 'GET, OPTIONS',
                'Access-Control-Allow-Headers': 'Content-Type',
            }
        )
        
    except ValueError as e:
        logger.error(f"Invalid Telegram ID format: {telegram_id_str}, error: {e}")
        return web.json_response(
            {"error": "Invalid Telegram ID"},
            status=400,
            headers={
                'Access-Control-Allow-Origin': '*',
                'Access-Control-Allow-Methods': 'GET, OPTIONS',
                'Access-Control-Allow-Headers': 'Content-Type',
            }
        )
    except Exception as e:
        logger.exception(f"Error getting subscription data for Telegram ID {telegram_id_str}: {e}")
        return web.json_response(
            {"error": "Internal server error"},
            status=500,
            headers={
                'Access-Control-Allow-Origin': '*',
                'Access-Control-Allow-Methods': 'GET, OPTIONS',
                'Access-Control-Allow-Headers': 'Content-Type',
            }
        )


async def serve_static(request: Request) -> Response:
    """Serve static files from miniapp directory."""
    path = request.match_info.get('path', 'index.html')
    
    logger.debug(f"Serving static file: {path}")
    
    # Security: prevent directory traversal
    if '..' in path or path.startswith('/'):
        logger.warning(f"Blocked directory traversal attempt: {path}")
        return web.Response(status=403, text="Forbidden")
    
    # Don't serve API routes as static files
    if path.startswith('api/'):
        logger.warning(f"Attempted to serve API route as static: {path}")
        return web.Response(status=404, text="Not Found")
    
    file_path = MINIAPP_DIR / path
    
    # Default to index.html if path is a directory or empty
    if file_path.is_dir() or not path or path == '':
        file_path = MINIAPP_DIR / 'index.html'
    
    if not file_path.exists():
        logger.warning(f"Static file not found: {file_path}")
        return web.Response(status=404, text="Not Found")
    
    # Determine content type
    content_type = 'text/html'
    if path.endswith('.js'):
        content_type = 'application/javascript'
    elif path.endswith('.css'):
        content_type = 'text/css'
    elif path.endswith('.json'):
        content_type = 'application/json'
    
    logger.debug(f"Serving file: {file_path}, content-type: {content_type}")
    return web.Response(
        body=file_path.read_bytes(),
        content_type=content_type,
        headers={
            'Cache-Control': 'no-cache, no-store, must-revalidate',
            'Pragma': 'no-cache',
            'Expires': '0'
        }
    )


def create_app() -> web.Application:
    """Create aiohttp application."""
    app = web.Application()
    
    # CORS middleware for all routes
    @web.middleware
    async def cors_middleware(request, handler):
        logger.info(f"Incoming request: {request.method} {request.path_qs}")
        
        # Handle preflight requests
        if request.method == 'OPTIONS':
            logger.info(f"Handling OPTIONS preflight for {request.path_qs}")
            return web.Response(
                headers={
                    'Access-Control-Allow-Origin': '*',
                    'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
                    'Access-Control-Allow-Headers': 'Content-Type',
                }
            )
        try:
            response = await handler(request)
        except Exception as e:
            logger.exception(f"Error in handler: {e}")
            response = web.json_response(
                {"error": "Internal server error"},
                status=500
            )
        response.headers['Access-Control-Allow-Origin'] = '*'
        response.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type'
        logger.info(f"Response status: {response.status} for {request.path_qs}")
        return response
    
    app.middlewares.append(cors_middleware)
    
    # API routes (MUST be registered BEFORE catch-all route)
    logger.info("Registering API routes...")
    app.router.add_get('/api/subscription/telegram/{telegram_id}', get_subscription_by_telegram_id)
    
    # Promo banners API
    async def get_promo_banners(request):
        """Get list of active promo banners."""
        from database import db_get_active_promo_banners
        banners = db_get_active_promo_banners()
        # Add full URL for images
        base_url = str(request.url).split('/api')[0]
        result = []
        for banner in banners:
            result.append({
                'id': banner['id'],
                'image_url': f"{base_url}/api/banners/{banner['image_filename']}",
                'link_url': banner['link_url'],
                'display_order': banner['display_order']
            })
        return web.json_response(result, headers={'Access-Control-Allow-Origin': '*'})
    
    app.router.add_get('/api/banners', get_promo_banners)
    
    # Serve banner images
    async def serve_banner_image(request):
        """Serve banner image file."""
        filename = request.match_info.get('filename')
        if not filename or '..' in filename or '/' in filename:
            return web.Response(status=404, text="Not Found")
        
        file_path = BANNERS_DIR / filename
        if not file_path.exists() or not file_path.is_file():
            return web.Response(status=404, text="Not Found")
        
        # Determine content type
        content_type = 'image/jpeg'
        if filename.lower().endswith('.png'):
            content_type = 'image/png'
        elif filename.lower().endswith('.gif'):
            content_type = 'image/gif'
        elif filename.lower().endswith('.webp'):
            content_type = 'image/webp'
        
        return web.Response(
            body=file_path.read_bytes(),
            content_type=content_type,
            headers={
                'Access-Control-Allow-Origin': '*',
                'Cache-Control': 'public, max-age=3600'
            }
        )
    
    app.router.add_get('/api/banners/{filename}', serve_banner_image)
    
    # Traffic usage details API
    async def get_traffic_usage_details(request):
        """Get traffic usage details by Telegram ID for date range."""
        telegram_id_str = request.match_info.get('telegram_id')
        start_date = request.query.get('start')
        end_date = request.query.get('end')
        
        if not telegram_id_str:
            return web.json_response(
                {"error": "Telegram ID is required"},
                status=400,
                headers={'Access-Control-Allow-Origin': '*'}
            )
        
        if not start_date or not end_date:
            return web.json_response(
                {"error": "start and end date parameters are required"},
                status=400,
                headers={'Access-Control-Allow-Origin': '*'}
            )
        
        try:
            telegram_id = int(telegram_id_str)
            
            # Get UUID from database by Telegram ID
            from database import db_get_user_backend_data
            backend_data = db_get_user_backend_data(telegram_id)
            
            if not backend_data or not backend_data[0]:
                return web.json_response(
                    {"error": "User not found or not linked"},
                    status=404,
                    headers={'Access-Control-Allow-Origin': '*'}
                )
            
            uuid = backend_data[0]
            
            # Get traffic usage from backend API
            usage_data = await get_traffic_usage_range(uuid, start_date, end_date)
            
            if usage_data is None:
                return web.json_response(
                    {"error": "Failed to fetch traffic usage data"},
                    status=500,
                    headers={'Access-Control-Allow-Origin': '*'}
                )
            
            return web.json_response(
                usage_data,
                headers={'Access-Control-Allow-Origin': '*'}
            )
            
        except ValueError:
            return web.json_response(
                {"error": "Invalid Telegram ID"},
                status=400,
                headers={'Access-Control-Allow-Origin': '*'}
            )
        except Exception as e:
            logger.exception(f"Error getting traffic usage: {e}")
            return web.json_response(
                {"error": "Internal server error"},
                status=500,
                headers={'Access-Control-Allow-Origin': '*'}
            )
    
    app.router.add_get('/api/traffic/usage/{telegram_id}', get_traffic_usage_details)
    
    # Serve icon images (supports subdirectories like bottombar/home.svg)
    async def serve_icon_image(request):
        """Serve icon image file."""
        # Get path from URL (supports subdirectories)
        path = request.match_info.get('path', '')
        if not path or '..' in path:
            return web.Response(status=404, text="Not Found")
        
        # Normalize path to prevent directory traversal
        file_path = ICONS_DIR / path
        # Ensure the path is within ICONS_DIR
        try:
            file_path = file_path.resolve()
            icons_dir_resolved = ICONS_DIR.resolve()
            if not str(file_path).startswith(str(icons_dir_resolved)):
                return web.Response(status=404, text="Not Found")
        except Exception:
            return web.Response(status=404, text="Not Found")
        
        if not file_path.exists() or not file_path.is_file():
            return web.Response(status=404, text="Not Found")
        
        # Extract filename for cache control logic
        filename = path.split('/')[-1] if '/' in path else path
        
        # Determine content type
        content_type = 'image/svg+xml'
        if filename.lower().endswith('.png'):
            content_type = 'image/png'
        elif filename.lower().endswith('.jpg') or filename.lower().endswith('.jpeg'):
            content_type = 'image/jpeg'
        elif filename.lower().endswith('.gif'):
            content_type = 'image/gif'
        elif filename.lower().endswith('.webp'):
            content_type = 'image/webp'
        
        # Для логотипа отключаем кеш, чтобы всегда загружалась актуальная версия
        no_cache_files = ['logo.svg']
        cache_control = 'no-cache, no-store, must-revalidate' if filename in no_cache_files else 'public, max-age=3600'
        headers = {
            'Access-Control-Allow-Origin': '*',
            'Cache-Control': cache_control
        }
        if filename in no_cache_files:
            headers['Pragma'] = 'no-cache'
            headers['Expires'] = '0'
        
        return web.Response(
            body=file_path.read_bytes(),
            content_type=content_type,
            headers=headers
        )
    
    app.router.add_get('/api/icons/{path:.*}', serve_icon_image)
    
    # Serve logo
    async def serve_logo(request):
        """Serve logo.svg file."""
        logo_path = ROOT_DIR / 'logo.svg'
        if not logo_path.exists() or not logo_path.is_file():
            return web.Response(status=404, text="Logo not found")
        
        return web.Response(
            body=logo_path.read_bytes(),
            content_type='image/svg+xml',
            headers={
                'Access-Control-Allow-Origin': '*',
                'Cache-Control': 'public, max-age=3600'
            }
        )
    
    app.router.add_get('/api/logo.svg', serve_logo)
    
    # Health check endpoint
    async def health_check(request):
        return web.json_response({"status": "ok", "service": "mini-app"})
    
    app.router.add_get('/api/health', health_check)
    
    # Authentication endpoints
    async def send_otp(request: Request) -> Response:
        """Send OTP code to user's email. POST /api/auth/send-otp"""
        try:
            data = await request.json()
            email = data.get('email', '').strip().lower()
            telegram_id = data.get('telegram_id')
            
            if not email:
                return web.json_response(
                    {"error": "Email is required"},
                    status=400,
                    headers={'Access-Control-Allow-Origin': '*'}
                )
            
            if not telegram_id:
                return web.json_response(
                    {"error": "Telegram ID is required"},
                    status=400,
                    headers={'Access-Control-Allow-Origin': '*'}
                )
            
            # Validate email format
            import re
            email_pattern = r'^[^\s@]+@[^\s@]+\.[^\s@]+$'
            if not re.match(email_pattern, email):
                return web.json_response(
                    {"error": "Invalid email format"},
                    status=400,
                    headers={'Access-Control-Allow-Origin': '*'}
                )
            
            # Validate allowed email domains
            allowed_domains = [
                'gmail.com',
                'yandex.ru',
                'yandex.com',
                'yandex.kz',
                'bk.ru',
                'vk.com',
                'inbox.ru',
                'list.ru',
                'mail.ru',
                'yahoo.com',
                'outlook.com',
                'hotmail.com',
                'icloud.com',
                'protonmail.com',
                'proton.me',
                'heavengate.net',
                'adacigroup.kz'
            ]
            
            email_domain = email.split('@')[1].lower() if '@' in email else ''
            if email_domain not in allowed_domains:
                return web.json_response(
                    {"error": "Разрешены только популярные почтовые сервисы (Gmail, Yandex, Mail.ru, Yahoo, Outlook и др.)"},
                    status=400,
                    headers={'Access-Control-Allow-Origin': '*'}
                )
            
            # Check if OTP was sent recently (within 1 minute)
            from datetime import datetime, timedelta
            last_otp_time = database.db_get_last_otp_time(email, telegram_id)
            if last_otp_time:
                try:
                    last_time = datetime.fromisoformat(last_otp_time.replace('Z', '+00:00'))
                    if last_time.tzinfo is None:
                        # If no timezone, assume local time
                        last_time = datetime.fromisoformat(last_otp_time)
                    time_diff = datetime.now() - last_time
                    if time_diff < timedelta(minutes=1):
                        remaining_seconds = int((timedelta(minutes=1) - time_diff).total_seconds())
                        return web.json_response(
                            {"error": f"Пожалуйста, подождите {remaining_seconds} секунд перед повторной отправкой кода"},
                            status=429,
                            headers={'Access-Control-Allow-Origin': '*'}
                        )
                except Exception as e:
                    logger.warning(f"Error checking last OTP time: {e}")
            
            # Generate OTP
            otp_code = otp_manager.generate_otp()
            
            # Store OTP
            otp_manager.store_otp(email, telegram_id, otp_code)
            
            # Send email (run in executor since it's blocking)
            import asyncio
            loop = asyncio.get_event_loop()
            email_sent = await loop.run_in_executor(None, smtp_client.send_otp_email, email, otp_code)
            
            if email_sent:
                logger.info(f"OTP sent to {email} for telegram_id {telegram_id}")
                return web.json_response(
                    {"success": True, "message": "OTP code sent to your email"},
                    headers={'Access-Control-Allow-Origin': '*'}
                )
            else:
                return web.json_response(
                    {"error": "Failed to send email. Please try again later."},
                    status=500,
                    headers={'Access-Control-Allow-Origin': '*'}
                )
                
        except Exception as e:
            logger.exception(f"Error sending OTP: {e}")
            return web.json_response(
                {"error": "Internal server error"},
                status=500,
                headers={'Access-Control-Allow-Origin': '*'}
            )
    
    async def verify_otp(request: Request) -> Response:
        """Verify OTP code and create/update user. POST /api/auth/verify-otp"""
        try:
            data = await request.json()
            email = data.get('email', '').strip().lower()
            telegram_id = data.get('telegram_id')
            otp_code = data.get('otp_code', '').strip()
            
            if not email or not telegram_id or not otp_code:
                return web.json_response(
                    {"error": "Email, telegram_id, and otp_code are required"},
                    status=400,
                    headers={'Access-Control-Allow-Origin': '*'}
                )
            
            # Verify OTP
            if not otp_manager.verify_otp(email, telegram_id, otp_code):
                return web.json_response(
                    {"error": "Invalid or expired OTP code"},
                    status=400,
                    headers={'Access-Control-Allow-Origin': '*'}
                )
            
            # Check if user exists on backend
            user_data = await get_user_by_email(email)
            
            if user_data is None:
                # User doesn't exist, create new user
                logger.info(f"Creating new user for email: {email}, telegram_id: {telegram_id}")
                user_data = await create_user(email, telegram_id)
                
                if user_data is None:
                    return web.json_response(
                        {"error": "Failed to create user. Please try again later."},
                        status=500,
                        headers={'Access-Control-Allow-Origin': '*'}
                    )
                
                uuid = user_data.get('uuid')
                if uuid:
                    # Save to local database
                    database.db_save_user_backend_data(telegram_id, uuid, email)
                    logger.info(f"Created user and saved to local DB: email={email}, telegram_id={telegram_id}, uuid={uuid}")
            else:
                # User exists, check if telegram_id is already set
                existing_telegram_id = user_data.get('telegramId')
                uuid = user_data.get('uuid')
                
                if not uuid:
                    return web.json_response(
                        {"error": "User data is invalid"},
                        status=500,
                        headers={'Access-Control-Allow-Origin': '*'}
                    )
                
                # If user already has a telegram_id
                if existing_telegram_id is not None and existing_telegram_id != 0:
                    # Check if it's the same telegram_id (user is already authorized)
                    if existing_telegram_id == telegram_id:
                        # User is already authorized with this telegram_id, just save to local DB
                        database.db_save_user_backend_data(telegram_id, uuid, email)
                        logger.info(f"User already authorized: email={email}, telegram_id={telegram_id}, uuid={uuid}")
                    else:
                        # User has different telegram_id, forbid registration
                        logger.warning(f"Attempt to register email {email} with telegram_id {telegram_id}, but user already has telegram_id {existing_telegram_id}")
                        return web.json_response(
                            {"error": "Этот email уже привязан к другому аккаунту Telegram. Пожалуйста, используйте другой email или обратитесь в поддержку."},
                            status=403,
                            headers={'Access-Control-Allow-Origin': '*'}
                        )
                else:
                    # User exists but has no telegram_id, update it
                    logger.info(f"Updating telegram_id for existing user: email={email}, telegram_id={telegram_id}, uuid={uuid}")
                    updated_user = await update_user_telegram_id(uuid, telegram_id)
                    
                    if updated_user:
                        # Save to local database
                        database.db_save_user_backend_data(telegram_id, uuid, email)
                        logger.info(f"Updated user and saved to local DB: email={email}, telegram_id={telegram_id}, uuid={uuid}")
                    else:
                        return web.json_response(
                            {"error": "Failed to update user. Please try again later."},
                            status=500,
                            headers={'Access-Control-Allow-Origin': '*'}
                        )
            
            return web.json_response(
                {"success": True, "message": "Authentication successful"},
                headers={'Access-Control-Allow-Origin': '*'}
            )
            
        except Exception as e:
            logger.exception(f"Error verifying OTP: {e}")
            return web.json_response(
                {"error": "Internal server error"},
                status=500,
                headers={'Access-Control-Allow-Origin': '*'}
            )
    
    app.router.add_post('/api/auth/send-otp', send_otp)
    app.router.add_post('/api/auth/verify-otp', verify_otp)
    
    # Payment endpoints
    async def update_user_subscription_after_payment(uuid: str, plan_days: int) -> None:
        """
        Update user subscription after successful payment.
        Handles three scenarios:
        1. New user (DISABLED) - add days to current date
        2. Active subscription renewal (ACTIVE) - add days to existing expireAt
        3. Expired subscription renewal (EXPIRED) - add days to current date
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
            
            # Обновляем подписку
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

    async def create_payment(request: Request) -> Response:
        """Create payment order. POST /api/payment/create"""
        try:
            data = await request.json()
            telegram_id = data.get('telegram_id')
            amount = data.get('amount')  # Сумма в основных единицах (тенге) - уже умножена на 100 на фронтенде
            currency = data.get('currency', 'kzt')  # Валюта
            plan_days = data.get('plan_days')  # Количество дней подписки
            
            if not telegram_id or not amount or not plan_days:
                return web.json_response(
                    {"error": "telegram_id, amount, and plan_days are required"},
                    status=400,
                    headers={'Access-Control-Allow-Origin': '*'}
                )
            
            # Получаем UUID пользователя из локальной БД
            from database import db_get_user_backend_data
            backend_data = db_get_user_backend_data(telegram_id)
            
            if not backend_data or not backend_data[0]:
                return web.json_response(
                    {"error": "User not found or not linked"},
                    status=404,
                    headers={'Access-Control-Allow-Origin': '*'}
                )
            
            uuid = backend_data[0]
            
            # СТРОГАЯ ПРОВЕРКА: только KZT и KGZ для банковских платежей
            if currency.lower() not in ['kzt', 'kgz']:
                return web.json_response(
                    {"error": "Банковские платежи доступны только для тенге (KZT) и кыргызских сомов (KGZ)"},
                    status=400,
                    headers={'Access-Control-Allow-Origin': '*'}
                )
            
            # ОСОБЕННОСТЬ ДЛЯ KGZ: отправляем в банк те же суммы что и для KZT
            # На фронтенде для KGZ показываются приблизительные суммы в сомах (для ознакомления)
            # Но в банк всегда отправляются суммы в тенге, как для KZT
            # Кыргызские банки автоматически конвертируют и списывают сомы со счета клиента
            # Фронтенд уже отправляет amount в тенге для KGZ, поэтому просто используем его
            
            # Всегда используем KZT для казахстанского эквайринга
            amount_minor = int(float(amount))
            currency_code = payment_gateway.CURRENCY_KZT
            
            logger.info(f"Creating payment: telegram_id={telegram_id}, currency={currency}, amount_minor={amount_minor}, currency_code={currency_code}, plan_days={plan_days}")
            
            # Формируем returnUrl (orderId будет подставлен платежным шлюзом)
            from config import MINIAPP_URL
            return_url = f"{MINIAPP_URL}/payment/return?telegram_id={telegram_id}"
            
            # Описание заказа
            description = f"VPN подписка на {plan_days} дней"
            
            # Генерируем orderNumber для платежного шлюза
            import uuid as uuid_module
            order_number = uuid_module.uuid4().hex[:32]  # 32 символа без дефисов
            
            logger.info(f"Registering order in payment gateway: amount={amount_minor}, currency={currency_code}, order_number={order_number}, description={description}")
            
            # Регистрируем заказ в платежном шлюзе
            order_data = await payment_gateway.register_order(
                amount=amount_minor,
                currency=currency_code,
                return_url=return_url,
                description=description,
                order_number=order_number,
                language="ru"
            )
            
            if order_data:
                logger.info(f"Order registered successfully: orderId={order_data.get('orderId')}, formUrl={order_data.get('formUrl')}")
            else:
                logger.error(f"Failed to register order in payment gateway")
            
            if not order_data:
                return web.json_response(
                    {"error": "Failed to create payment order"},
                    status=500,
                    headers={'Access-Control-Allow-Origin': '*'}
                )
            
            # Сохраняем информацию о заказе в БД
            from database import db_save_payment_order
            db_save_payment_order(
                order_id=order_data['orderId'],
                telegram_id=telegram_id,
                uuid=uuid,
                amount=float(amount) / 100,  # Конвертируем обратно в основные единицы для хранения
                currency=currency,
                plan_days=plan_days,
                status='PENDING'
            )
            
            return web.json_response(
                {
                    "orderId": order_data['orderId'],
                    "formUrl": order_data['formUrl']
                },
                headers={'Access-Control-Allow-Origin': '*'}
            )
            
        except Exception as e:
            logger.exception(f"Error creating payment: {e}")
            error_message = str(e)
            return web.json_response(
                {"error": f"Internal server error: {error_message}"},
                status=500,
                headers={'Access-Control-Allow-Origin': '*'}
            )
    
    async def check_payment_status(request: Request) -> Response:
        """Check payment order status. GET /api/payment/status/{order_id}"""
        try:
            order_id = request.match_info.get('order_id')
            
            if not order_id:
                return web.json_response(
                    {"error": "order_id is required"},
                    status=400,
                    headers={'Access-Control-Allow-Origin': '*'}
                )
            
            # КРИТИЧНО: Получаем telegram_id из запроса (query параметр или заголовок)
            telegram_id_str = request.query.get('telegram_id')
            if not telegram_id_str:
                # Пытаемся получить из заголовка (если передается)
                telegram_id_str = request.headers.get('X-Telegram-Id')
            
            if not telegram_id_str:
                logger.error(f"Missing telegram_id for order {order_id}")
                return web.json_response(
                    {"error": "telegram_id is required"},
                    status=400,
                    headers={'Access-Control-Allow-Origin': '*'}
                )
            
            try:
                telegram_id = int(telegram_id_str)
            except ValueError:
                logger.error(f"Invalid telegram_id format: {telegram_id_str}")
                return web.json_response(
                    {"error": "Invalid telegram_id format"},
                    status=400,
                    headers={'Access-Control-Allow-Origin': '*'}
                )
            
            # КРИТИЧНО: Проверяем, что заказ принадлежит этому пользователю
            from database import db_get_payment_order
            payment_order = db_get_payment_order(order_id)
            
            if not payment_order:
                logger.error(f"Order {order_id} not found in database")
                return web.json_response(
                    {"error": "Order not found"},
                    status=404,
                    headers={'Access-Control-Allow-Origin': '*'}
                )
            
            # СТРОГАЯ ПРОВЕРКА: заказ должен принадлежать текущему пользователю
            if payment_order.get('telegram_id') != telegram_id:
                logger.error(f"Order {order_id} belongs to telegram_id {payment_order.get('telegram_id')}, but request from {telegram_id}")
                return web.json_response(
                    {"error": "Access denied: order does not belong to this user"},
                    status=403,
                    headers={'Access-Control-Allow-Origin': '*'}
                )
            
            logger.info(f"Checking payment status for order {order_id}, telegram_id {telegram_id} (verified)")
            
            # Получаем статус заказа из платежного шлюза
            order_status = await payment_gateway.get_order_status(order_id)
            
            if not order_status:
                return web.json_response(
                    {"error": "Failed to get order status"},
                    status=500,
                    headers={'Access-Control-Allow-Origin': '*'}
                )
            
            # Проверяем статус
            status_code = order_status.get('orderStatus')
            is_paid = payment_gateway.is_order_paid(status_code)
            
            # Если статус = 1 (PRE_AUTH), нужно завершить заказ (deposit)
            if status_code == payment_gateway.ORDER_STATUS_PRE_AUTH:
                logger.info(f"Order {order_id} has PRE_AUTH status, attempting to deposit...")
                deposit_result = await payment_gateway.deposit_order(order_id)
                
                if deposit_result:
                    # Повторно проверяем статус после deposit
                    order_status = await payment_gateway.get_order_status(order_id)
                    if order_status:
                        status_code = order_status.get('orderStatus')
                        is_paid = payment_gateway.is_order_paid(status_code)
                        logger.info(f"Order {order_id} deposit completed, new status: {status_code}")
                else:
                    logger.warning(f"Failed to deposit order {order_id}, but status is PRE_AUTH")
            
            # Обновляем статус в БД
            from database import db_update_payment_order_status
            db_update_payment_order_status(
                order_id=order_id,
                status='PAID' if is_paid else 'FAILED',
                status_data=order_status
            )
            
            # Если платеж успешен, обновляем подписку на бэкенде
            if is_paid:
                logger.info(f"Payment successful for order {order_id}, attempting to update subscription...")
                try:
                    # КРИТИЧНО: Проверяем еще раз, что заказ принадлежит пользователю
                    # (payment_order уже получен выше и проверен)
                    if payment_order.get('telegram_id') != telegram_id:
                        logger.error(f"SECURITY: Order {order_id} ownership mismatch during subscription update")
                        return web.json_response(
                            {"error": "Security check failed"},
                            status=403,
                            headers={'Access-Control-Allow-Origin': '*'}
                        )
                    
                    # КРИТИЧНО: Проверяем, что UUID из заказа соответствует текущему пользователю
                    from database import db_get_user_backend_data
                    user_backend_data = db_get_user_backend_data(telegram_id)
                    if not user_backend_data or not user_backend_data[0]:
                        logger.error(f"User {telegram_id} not found in backend data")
                        return web.json_response(
                            {"error": "User not found"},
                            status=404,
                            headers={'Access-Control-Allow-Origin': '*'}
                        )
                    
                    user_uuid = user_backend_data[0]
                    order_uuid = payment_order.get('uuid')
                    
                    # СТРОГАЯ ПРОВЕРКА: UUID из заказа должен совпадать с UUID пользователя
                    if order_uuid != user_uuid:
                        logger.error(f"SECURITY: UUID mismatch for order {order_id}. Order UUID: {order_uuid}, User UUID: {user_uuid}")
                        return web.json_response(
                            {"error": "UUID mismatch: order does not belong to this user"},
                            status=403,
                            headers={'Access-Control-Allow-Origin': '*'}
                        )
                    
                    logger.info(f"Payment order data verified: order_id={order_id}, telegram_id={telegram_id}, uuid={user_uuid}")
                    
                    # Проверяем, не была ли подписка уже обновлена
                    if payment_order.get('subscription_updated'):
                        logger.info(f"Subscription already updated for order {order_id}, skipping")
                    elif payment_order.get('uuid') and payment_order.get('plan_days'):
                        uuid = payment_order['uuid']
                        plan_days = payment_order['plan_days']
                        logger.info(f"Updating subscription for UUID {uuid} with {plan_days} days")
                        
                        # Вызываем функцию обновления подписки
                        await update_user_subscription_after_payment(uuid, plan_days)
                        
                        # Помечаем, что подписка была обновлена
                        from database import db_mark_subscription_updated
                        db_mark_subscription_updated(order_id)
                        
                        logger.info(f"Subscription update completed for UUID {uuid}")
                    else:
                        logger.warning(f"Payment order missing required data: uuid={payment_order.get('uuid')}, plan_days={payment_order.get('plan_days')}")
                except Exception as e:
                    logger.exception(f"Error updating subscription after payment: {e}")
                    # Не прерываем ответ, просто логируем ошибку
            
            return web.json_response(
                {
                    "orderId": order_id,
                    "status": status_code,
                    "isPaid": is_paid,
                    "actionCode": order_status.get('actionCode'),
                    "orderStatus": order_status.get('orderStatus')
                },
                headers={'Access-Control-Allow-Origin': '*'}
            )
            
        except Exception as e:
            logger.exception(f"Error checking payment status: {e}")
            return web.json_response(
                {"error": "Internal server error"},
                status=500,
                headers={'Access-Control-Allow-Origin': '*'}
            )
    
    async def payment_return(request: Request) -> Response:
        """Handle payment return from gateway. GET /payment/return"""
        try:
            telegram_id_str = request.query.get('telegram_id')
            order_id = request.query.get('orderId')  # Параметр от платежного шлюза
            
            if not telegram_id_str or not order_id:
                return web.Response(
                    text="Ошибка: отсутствуют необходимые параметры",
                    status=400
                )
            
            telegram_id = int(telegram_id_str)
            
            # КРИТИЧНО: Проверяем, что заказ принадлежит этому пользователю
            from database import db_get_payment_order
            payment_order = db_get_payment_order(order_id)
            
            if not payment_order:
                logger.error(f"Order {order_id} not found in database")
                return web.Response(
                    text="Ошибка: заказ не найден",
                    status=404
                )
            
            # СТРОГАЯ ПРОВЕРКА: заказ должен принадлежать текущему пользователю
            if payment_order.get('telegram_id') != telegram_id:
                logger.error(f"SECURITY: Order {order_id} belongs to telegram_id {payment_order.get('telegram_id')}, but request from {telegram_id}")
                return web.Response(
                    text="Ошибка: заказ не принадлежит этому пользователю",
                    status=403
                )
            
            logger.info(f"Payment return for order {order_id}, telegram_id {telegram_id} (verified)")
            
            # Проверяем тип платежа по currency в базе данных
            payment_currency = payment_order.get('currency', '').lower()
            crypto_currencies = ['crypto', 'usdt', 'ton', 'eth', 'btc']
            is_crypto_payment = payment_currency in crypto_currencies
            
            if is_crypto_payment:
                # Для криптоплатежей используем Cryptomus API
                logger.info(f"Payment return for Cryptomus order {order_id}")
                payment_info = await cryptomus_client.get_payment_info(order_id)
                
                if not payment_info:
                    return web.Response(
                        text="Ошибка: не удалось проверить статус платежа",
                        status=500
                    )
                
                status = payment_info.get('status', '')
                is_paid = cryptomus_client.is_payment_successful(status)
                is_failed = cryptomus_client.is_payment_failed(status)
                
                # Обновляем статус в БД
                from database import db_update_payment_order_status
                db_update_payment_order_status(
                    order_id=order_id,
                    status=status,
                    status_data=payment_info
                )
                
                order_status = payment_info  # Для совместимости с дальнейшим кодом
            else:
                # Для банковских платежей используем Berekebank
                logger.info(f"Payment return for bank order {order_id}")
                order_status = await payment_gateway.get_order_status(order_id)
                
                if not order_status:
                    return web.Response(
                        text="Ошибка: не удалось проверить статус заказа",
                        status=500
                    )
                
                # Проверяем статус
                status_code = order_status.get('orderStatus')
                is_paid = payment_gateway.is_order_paid(status_code)
                
                # Если статус = 1 (PRE_AUTH), нужно завершить заказ (deposit)
                if status_code == payment_gateway.ORDER_STATUS_PRE_AUTH:
                    logger.info(f"Order {order_id} has PRE_AUTH status, attempting to deposit...")
                    deposit_result = await payment_gateway.deposit_order(order_id)
                    
                    if deposit_result:
                        # Повторно проверяем статус после deposit
                        order_status = await payment_gateway.get_order_status(order_id)
                        if order_status:
                            status_code = order_status.get('orderStatus')
                            is_paid = payment_gateway.is_order_paid(status_code)
                            logger.info(f"Order {order_id} deposit completed, new status: {status_code}")
                    else:
                        logger.warning(f"Failed to deposit order {order_id}, but status is PRE_AUTH")
                
                # Обновляем статус в БД
                from database import db_update_payment_order_status
                db_update_payment_order_status(
                    order_id=order_id,
                    status='PAID' if is_paid else 'FAILED',
                    status_data=order_status
                )
            
            # Если платеж успешен, обновляем подписку на бэкенде
            if is_paid:
                logger.info(f"Payment successful for order {order_id} (payment_return), attempting to update subscription...")
                try:
                    # КРИТИЧНО: Проверяем еще раз, что заказ принадлежит пользователю
                    if payment_order.get('telegram_id') != telegram_id:
                        logger.error(f"SECURITY: Order {order_id} ownership mismatch during subscription update")
                        return web.Response(
                            text="Ошибка безопасности",
                            status=403
                        )
                    
                    # КРИТИЧНО: Проверяем, что UUID из заказа соответствует текущему пользователю
                    from database import db_get_user_backend_data
                    user_backend_data = db_get_user_backend_data(telegram_id)
                    if not user_backend_data or not user_backend_data[0]:
                        logger.error(f"User {telegram_id} not found in backend data")
                        return web.Response(
                            text="Ошибка: пользователь не найден",
                            status=404
                        )
                    
                    user_uuid = user_backend_data[0]
                    order_uuid = payment_order.get('uuid')
                    
                    # СТРОГАЯ ПРОВЕРКА: UUID из заказа должен совпадать с UUID пользователя
                    if order_uuid != user_uuid:
                        logger.error(f"SECURITY: UUID mismatch for order {order_id}. Order UUID: {order_uuid}, User UUID: {user_uuid}")
                        return web.Response(
                            text="Ошибка: несоответствие данных пользователя",
                            status=403
                        )
                    
                    logger.info(f"Payment order data verified: order_id={order_id}, telegram_id={telegram_id}, uuid={user_uuid}")
                    
                    # Проверяем, не была ли подписка уже обновлена
                    if payment_order.get('subscription_updated'):
                        logger.info(f"Subscription already updated for order {order_id}, skipping")
                    elif payment_order.get('uuid') and payment_order.get('plan_days'):
                        uuid = payment_order['uuid']
                        plan_days = payment_order['plan_days']
                        logger.info(f"Updating subscription for UUID {uuid} with {plan_days} days")
                        
                        # Вызываем функцию обновления подписки
                        await update_user_subscription_after_payment(uuid, plan_days)
                        
                        # Помечаем, что подписка была обновлена
                        from database import db_mark_subscription_updated
                        db_mark_subscription_updated(order_id)
                        
                        logger.info(f"Subscription update completed for UUID {uuid}")
                    else:
                        logger.warning(f"Payment order missing required data: uuid={payment_order.get('uuid')}, plan_days={payment_order.get('plan_days')}")
                except Exception as e:
                    logger.exception(f"Error updating subscription after payment: {e}")
                    # Не прерываем ответ, просто логируем ошибку
            
            # Возвращаем HTML страницу с результатом
            if is_paid:
                html = """
                <!DOCTYPE html>
                <html lang="ru">
                <head>
                    <meta charset="UTF-8">
                    <meta name="viewport" content="width=device-width, initial-scale=1.0">
                    <title>Оплата успешна</title>
                    <style>
                        body {
                            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                            display: flex;
                            justify-content: center;
                            align-items: center;
                            min-height: 100vh;
                            margin: 0;
                            background: #f5f5f5;
                        }
                        .container {
                            background: white;
                            padding: 40px;
                            border-radius: 12px;
                            text-align: center;
                            box-shadow: 0 2px 10px rgba(0,0,0,0.1);
                        }
                        .success {
                            color: #34C759;
                            font-size: 48px;
                            margin-bottom: 20px;
                        }
                        h1 {
                            color: #000;
                            margin: 0 0 10px 0;
                        }
                        p {
                            color: #666;
                            margin: 0;
                        }
                    </style>
                </head>
                <body>
                    <div class="container">
                        <div class="success">✓</div>
                        <h1>Оплата успешна!</h1>
                        <p>Ваша подписка активирована</p>
                    </div>
                </body>
                </html>
                """
            else:
                html = """
                <!DOCTYPE html>
                <html lang="ru">
                <head>
                    <meta charset="UTF-8">
                    <meta name="viewport" content="width=device-width, initial-scale=1.0">
                    <title>Ошибка оплаты</title>
                    <style>
                        body {
                            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                            display: flex;
                            justify-content: center;
                            align-items: center;
                            min-height: 100vh;
                            margin: 0;
                            background: #f5f5f5;
                        }
                        .container {
                            background: white;
                            padding: 40px;
                            border-radius: 12px;
                            text-align: center;
                            box-shadow: 0 2px 10px rgba(0,0,0,0.1);
                        }
                        .error {
                            color: #FF3B30;
                            font-size: 48px;
                            margin-bottom: 20px;
                        }
                        h1 {
                            color: #000;
                            margin: 0 0 10px 0;
                        }
                        p {
                            color: #666;
                            margin: 0;
                        }
                    </style>
                </head>
                <body>
                    <div class="container">
                        <div class="error">✗</div>
                        <h1>Оплата не прошла</h1>
                        <p>Пожалуйста, попробуйте еще раз</p>
                    </div>
                </body>
                </html>
                """
            
            return web.Response(text=html, content_type='text/html')
            
        except Exception as e:
            logger.exception(f"Error handling payment return: {e}")
            return web.Response(
                text="Ошибка обработки платежа",
                status=500
            )
    
    async def create_cryptomus_payment(request: Request) -> Response:
        """Create Cryptomus payment order. POST /api/cryptomus/payment/create"""
        try:
            data = await request.json()
            telegram_id = data.get('telegram_id')
            amount = data.get('amount')  # Сумма в USD
            currency = data.get('currency', 'crypto')  # Валюта (crypto для Cryptomus)
            plan_days = data.get('plan_days')  # Количество дней подписки
            
            if not telegram_id or not amount or not plan_days:
                return web.json_response(
                    {"error": "telegram_id, amount, and plan_days are required"},
                    status=400,
                    headers={'Access-Control-Allow-Origin': '*'}
                )
            
            # Получаем UUID пользователя из локальной БД
            from database import db_get_user_backend_data
            backend_data = db_get_user_backend_data(telegram_id)
            
            if not backend_data or not backend_data[0]:
                return web.json_response(
                    {"error": "User not found or not linked"},
                    status=404,
                    headers={'Access-Control-Allow-Origin': '*'}
                )
            
            uuid = backend_data[0]
            
            # Проверяем, что это криптовалютный платеж
            # Принимаем crypto, usdt, ton, eth, btc
            crypto_currencies = ['crypto', 'usdt', 'ton', 'eth', 'btc']
            if currency.lower() not in crypto_currencies:
                return web.json_response(
                    {"error": "This endpoint is only for cryptocurrency payments"},
                    status=400,
                    headers={'Access-Control-Allow-Origin': '*'}
                )
            
            logger.info(f"Creating Cryptomus payment: telegram_id={telegram_id}, amount={amount} USD, plan_days={plan_days}")
            
            # Генерируем order_id для Cryptomus
            import uuid as uuid_module
            order_id = uuid_module.uuid4().hex[:32]  # 32 символа без дефисов
            
            # Создаем платеж в Cryptomus
            payment_data = await cryptomus_client.create_payment(
                amount=str(amount),
                currency="USD",
                order_id=order_id
            )
            
            if not payment_data:
                logger.error("Failed to create Cryptomus payment")
                return web.json_response(
                    {"error": "Failed to create payment order"},
                    status=500,
                    headers={'Access-Control-Allow-Origin': '*'}
                )
            
            payment_uuid = payment_data.get('uuid')
            payment_url = payment_data.get('url')
            
            if not payment_uuid or not payment_url:
                logger.error(f"Invalid Cryptomus payment response: {payment_data}")
                return web.json_response(
                    {"error": "Invalid payment response"},
                    status=500,
                    headers={'Access-Control-Allow-Origin': '*'}
                )
            
            logger.info(f"Cryptomus payment created: uuid={payment_uuid}, url={payment_url}")
            
            # Сохраняем информацию о заказе в БД (используем payment_uuid как order_id)
            from database import db_save_payment_order
            db_save_payment_order(
                order_id=payment_uuid,  # Используем UUID из Cryptomus как order_id
                telegram_id=telegram_id,
                uuid=uuid,
                amount=float(amount),
                currency='crypto',
                plan_days=plan_days,
                status='PENDING'
            )
            
            return web.json_response(
                {
                    "uuid": payment_uuid,
                    "url": payment_url,
                    "orderId": payment_uuid  # Для совместимости с фронтендом
                },
                headers={'Access-Control-Allow-Origin': '*'}
            )
            
        except Exception as e:
            logger.exception(f"Error creating Cryptomus payment: {e}")
            error_message = str(e)
            return web.json_response(
                {"error": f"Internal server error: {error_message}"},
                status=500,
                headers={'Access-Control-Allow-Origin': '*'}
            )
    
    async def check_cryptomus_payment_status(request: Request) -> Response:
        """Check Cryptomus payment status. GET /api/cryptomus/payment/status/{uuid}"""
        try:
            payment_uuid = request.match_info.get('uuid')
            
            if not payment_uuid:
                return web.json_response(
                    {"error": "uuid is required"},
                    status=400,
                    headers={'Access-Control-Allow-Origin': '*'}
                )
            
            # Получаем telegram_id из query параметра
            telegram_id_str = request.query.get('telegram_id')
            if not telegram_id_str:
                return web.json_response(
                    {"error": "telegram_id is required"},
                    status=400,
                    headers={'Access-Control-Allow-Origin': '*'}
                )
            
            try:
                telegram_id = int(telegram_id_str)
            except ValueError:
                return web.json_response(
                    {"error": "Invalid telegram_id format"},
                    status=400,
                    headers={'Access-Control-Allow-Origin': '*'}
                )
            
            # Проверяем, что заказ принадлежит этому пользователю
            from database import db_get_payment_order
            payment_order = db_get_payment_order(payment_uuid)
            
            if not payment_order:
                return web.json_response(
                    {"error": "Payment order not found"},
                    status=404,
                    headers={'Access-Control-Allow-Origin': '*'}
                )
            
            if payment_order.get('telegram_id') != telegram_id:
                logger.error(f"Payment {payment_uuid} belongs to telegram_id {payment_order.get('telegram_id')}, but request from {telegram_id}")
                return web.json_response(
                    {"error": "Access denied: payment does not belong to this user"},
                    status=403,
                    headers={'Access-Control-Allow-Origin': '*'}
                )
            
            logger.info(f"Checking Cryptomus payment status: uuid={payment_uuid}, telegram_id={telegram_id}")
            
            # Получаем статус платежа из Cryptomus
            payment_info = await cryptomus_client.get_payment_info(payment_uuid)
            
            if not payment_info:
                return web.json_response(
                    {"error": "Failed to get payment status"},
                    status=500,
                    headers={'Access-Control-Allow-Origin': '*'}
                )
            
            status = payment_info.get('status', '')
            is_final = payment_info.get('is_final', False)
            is_paid = cryptomus_client.is_payment_successful(status)
            is_failed = cryptomus_client.is_payment_failed(status)
            
            # Обновляем статус в БД
            from database import db_update_payment_order_status
            db_update_payment_order_status(
                order_id=payment_uuid,
                status=status,
                status_data=payment_info
            )
            
            # Если платеж успешен и подписка еще не обновлена
            if is_paid and not payment_order.get('subscription_updated'):
                uuid = payment_order.get('uuid')
                plan_days = payment_order.get('plan_days')
                
                if uuid and plan_days:
                    logger.info(f"Payment successful, updating subscription: uuid={uuid}, plan_days={plan_days}")
                    await update_user_subscription_after_payment(uuid, plan_days)
                    
                    # Помечаем, что подписка обновлена
                    from database import db_mark_subscription_updated
                    db_mark_subscription_updated(payment_uuid)
            
            return web.json_response(
                {
                    "uuid": payment_uuid,
                    "status": status,
                    "isPaid": is_paid,
                    "isFailed": is_failed,
                    "isFinal": is_final,
                    "paymentInfo": payment_info
                },
                headers={'Access-Control-Allow-Origin': '*'}
            )
            
        except Exception as e:
            logger.exception(f"Error checking Cryptomus payment status: {e}")
            return web.json_response(
                {"error": f"Internal server error: {str(e)}"},
                status=500,
                headers={'Access-Control-Allow-Origin': '*'}
            )
    
    async def create_stars_payment(request: Request) -> Response:
        """Create Telegram Stars payment and send invoice message to user in bot. POST /api/stars/payment/create"""
        try:
            data = await request.json()
            telegram_id = data.get('telegram_id')
            amount = data.get('amount')  # Сумма в Stars
            currency = data.get('currency', 'stars')  # Валюта (stars для Telegram Stars)
            plan_days = data.get('plan_days')  # Количество дней подписки
            
            if not telegram_id or not amount or not plan_days:
                return web.json_response(
                    {"error": "telegram_id, amount, and plan_days are required"},
                    status=400,
                    headers={'Access-Control-Allow-Origin': '*'}
                )
            
            # Проверяем, что это платеж через Stars
            if currency.lower() != 'stars':
                return web.json_response(
                    {"error": "This endpoint is only for Telegram Stars payments"},
                    status=400,
                    headers={'Access-Control-Allow-Origin': '*'}
                )
            
            # Получаем UUID пользователя из локальной БД
            from database import db_get_user_backend_data
            backend_data = db_get_user_backend_data(telegram_id)
            
            if not backend_data or not backend_data[0]:
                return web.json_response(
                    {"error": "User not found or not linked"},
                    status=404,
                    headers={'Access-Control-Allow-Origin': '*'}
                )
            
            uuid = backend_data[0]
            
            # Генерируем уникальный invoice_payload для отслеживания заказа
            invoice_payload = uuid_module.uuid4().hex[:32]
            
            # Описание заказа
            title = f"VPN подписка на {plan_days} дней"
            description = f"Подписка на VPN сервис HeavenGate на {plan_days} дней"
            
            # Сохраняем информацию о заказе в БД ПЕРЕД созданием инвойса
            from database import db_save_payment_order
            db_save_payment_order(
                order_id=invoice_payload,
                telegram_id=telegram_id,
                uuid=uuid,
                amount=float(amount),
                currency='stars',
                plan_days=plan_days,
                status='PENDING'
            )
            
            # Создаем инвойс через Bot API метод sendInvoice
            bot_api_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendInvoice"
            
            payload = {
                "chat_id": telegram_id,
                "title": title,
                "description": description,
                "payload": invoice_payload,
                "provider_token": "",  # Не требуется для Stars
                "currency": "XTR",  # Telegram Stars currency code
                "prices": [
                    {
                        "label": title,
                        "amount": int(amount) * 100  # Stars в минимальных единицах (1 Star = 100)
                    }
                ],
                "max_tip_amount": 0,
                "suggested_tip_amounts": [],
                "provider_data": "",  # Можно передать JSON с дополнительными данными
                "photo_url": "",
                "photo_size": 0,
                "photo_width": 0,
                "photo_height": 0,
                "need_name": False,
                "need_phone_number": False,
                "need_email": False,
                "need_shipping_address": False,
                "send_phone_number_to_provider": False,
                "send_email_to_provider": False,
                "is_flexible": False
            }
            
            logger.info(f"Creating Stars invoice and sending to user: telegram_id={telegram_id}, amount={amount}, plan_days={plan_days}, payload={invoice_payload}")
            
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(bot_api_url, json=payload)
                response.raise_for_status()
                result = response.json()
            
            if not result.get('ok'):
                error_description = result.get('description', 'Unknown error')
                logger.error(f"Failed to send Stars invoice: {error_description}")
                return web.json_response(
                    {"error": f"Failed to send invoice: {error_description}"},
                    status=500,
                    headers={'Access-Control-Allow-Origin': '*'}
                )
            
            logger.info(f"Stars invoice sent successfully to user {telegram_id}, payload={invoice_payload}")
            
            return web.json_response(
                {
                    "success": True,
                    "message": "Invoice sent to bot",
                    "payload": invoice_payload
                },
                headers={'Access-Control-Allow-Origin': '*'}
            )
            
        except httpx.HTTPError as e:
            logger.exception(f"HTTP error creating Stars invoice: {e}")
            return web.json_response(
                {"error": f"Failed to send invoice: {str(e)}"},
                status=500,
                headers={'Access-Control-Allow-Origin': '*'}
            )
        except Exception as e:
            logger.exception(f"Error creating Stars payment: {e}")
            return web.json_response(
                {"error": f"Internal server error: {str(e)}"},
                status=500,
                headers={'Access-Control-Allow-Origin': '*'}
            )
    
    app.router.add_post('/api/payment/create', create_payment)
    app.router.add_get('/api/payment/status/{order_id}', check_payment_status)
    app.router.add_get('/payment/return', payment_return)
    app.router.add_post('/api/cryptomus/payment/create', create_cryptomus_payment)
    app.router.add_get('/api/cryptomus/payment/status/{uuid}', check_cryptomus_payment_status)
    app.router.add_post('/api/stars/payment/create', create_stars_payment)
    
    # Static files (catch-all, must be last)
    logger.info("Registering static files route...")
    app.router.add_get('/{path:.*}', serve_static)
    
    logger.info("Application routes registered")
    logger.info("API endpoints available:")
    logger.info("  GET /api/subscription/telegram/{telegram_id}")
    logger.info("  GET /api/banners")
    logger.info("  GET /api/banners/{filename}")
    logger.info("  GET /api/icons/{filename}")
    logger.info("  GET /api/logo.svg")
    logger.info("  GET /api/traffic/usage/{telegram_id}?start=...&end=...")
    logger.info("  GET /api/health")
    logger.info("  POST /api/auth/send-otp")
    logger.info("  POST /api/auth/verify-otp")
    logger.info("  POST /api/payment/create")
    logger.info("  GET /api/payment/status/{order_id}")
    logger.info("  GET /payment/return")
    logger.info("  POST /api/cryptomus/payment/create")
    logger.info("  GET /api/cryptomus/payment/status/{uuid}")
    logger.info("  POST /api/stars/payment/create")
    
    return app


async def run_server(host: str = "0.0.0.0", port: int = 8080):
    """Run the mini-app server."""
    from config import MINIAPP_PORT
    
    try:
        logger.info(f"Creating mini-app application...")
        app = create_app()
        logger.info(f"Creating AppRunner...")
        runner = web.AppRunner(app)
        logger.info(f"Setting up AppRunner...")
        await runner.setup()
        
        actual_port = port or MINIAPP_PORT
        logger.info(f"Creating TCPSite on {host}:{actual_port}...")
        site = web.TCPSite(runner, host, actual_port)
        logger.info(f"Starting TCPSite...")
        await site.start()
        
        logger.info(f"✅ Mini-app server successfully started on http://{host}:{actual_port}")
        logger.info(f"Server is ready to accept connections")
        
        # Keep running
        try:
            await asyncio.Future()  # Run forever
        except KeyboardInterrupt:
            logger.info("Shutting down mini-app server...")
        finally:
            logger.info("Cleaning up AppRunner...")
            await runner.cleanup()
            logger.info("Mini-app server stopped")
    except Exception as e:
        logger.exception(f"❌ Failed to start mini-app server: {e}")
        raise


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    asyncio.run(run_server())

