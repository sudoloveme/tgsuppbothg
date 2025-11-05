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

from api_client import get_user_by_uuid

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
    
    # Serve icon images
    async def serve_icon_image(request):
        """Serve icon image file."""
        filename = request.match_info.get('filename')
        if not filename or '..' in filename or '/' in filename:
            return web.Response(status=404, text="Not Found")
        
        file_path = ICONS_DIR / filename
        if not file_path.exists() or not file_path.is_file():
            return web.Response(status=404, text="Not Found")
        
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
        
        return web.Response(
            body=file_path.read_bytes(),
            content_type=content_type,
            headers={
                'Access-Control-Allow-Origin': '*',
                'Cache-Control': 'public, max-age=3600'
            }
        )
    
    app.router.add_get('/api/icons/{filename}', serve_icon_image)
    
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
    logger.info("  GET /api/health")
    
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

