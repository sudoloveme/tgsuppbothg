"""
HTTP server for serving mini-app static files and API endpoints.
"""
import logging
import asyncio
from pathlib import Path
from typing import Optional

from aiohttp import web
from aiohttp.web_request import Request
from aiohttp.web_response import Response

from api_client import get_user_by_uuid, get_traffic_usage_range, get_user_by_email, create_user, update_user_telegram_id
import database
import otp_manager
import smtp_client

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
        
        # Для логотипа и иконок способов оплаты отключаем кеш, чтобы всегда загружалась актуальная версия
        no_cache_files = ['logo.svg', 'crypto.svg', 'stars.svg']
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
    async def create_payment(request: Request) -> Response:
        """Create payment order. POST /api/payment/create"""
        try:
            data = await request.json()
            telegram_id = data.get('telegram_id')
            amount = data.get('amount')  # Сумма в основных единицах (тенге)
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
            
            # Определяем код валюты
            currency_codes = {
                'kzt': payment_gateway.CURRENCY_KZT,
                'kgz': payment_gateway.CURRENCY_KGZ,
                'rub': payment_gateway.CURRENCY_RUB,
                'cny': payment_gateway.CURRENCY_CNY
            }
            currency_code = currency_codes.get(currency.lower(), payment_gateway.CURRENCY_KZT)
            
            # Конвертируем сумму в минимальные единицы
            amount_minor = payment_gateway.convert_amount_to_minor_units(float(amount), currency_code)
            
            # Формируем returnUrl (orderId будет подставлен платежным шлюзом)
            from config import MINIAPP_URL
            return_url = f"{MINIAPP_URL}/payment/return?telegram_id={telegram_id}"
            
            # Описание заказа
            description = f"VPN подписка на {plan_days} дней"
            
            # Регистрируем заказ в платежном шлюзе
            order_data = await payment_gateway.register_order(
                amount=amount_minor,
                currency=currency_code,
                return_url=return_url,
                description=description,
                language="ru"
            )
            
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
                amount=float(amount),
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
            return web.json_response(
                {"error": "Internal server error"},
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
            is_paid = (status_code == payment_gateway.ORDER_STATUS_SUCCESS)
            
            # Обновляем статус в БД
            from database import db_update_payment_order_status
            db_update_payment_order_status(
                order_id=order_id,
                status='PAID' if is_paid else 'FAILED',
                status_data=order_status
            )
            
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
    
    app.router.add_post('/api/payment/create', create_payment)
    app.router.add_get('/api/payment/status/{order_id}', check_payment_status)
    
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

