"""
Simple HTTP server for serving mini-app static files and API endpoints.
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


async def get_subscription_by_telegram_id(request: Request) -> Response:
    """
    API endpoint to get subscription data by Telegram ID.
    GET /api/subscription/telegram/{telegram_id}
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


async def get_subscription_data(request: Request) -> Response:
    """
    API endpoint to get subscription data by UUID.
    GET /api/subscription/{uuid}
    """
    uuid = request.match_info.get('uuid')
    if not uuid:
        return web.json_response(
            {"error": "UUID is required"},
            status=400
        )

    try:
        # Get user data from backend API
        user_data = await get_user_by_uuid(uuid)
        
        if not user_data:
            return web.json_response(
                {"error": "User not found"},
                status=404
            )

        # Return subscription data
        return web.json_response(user_data)
        
    except Exception as e:
        logger.exception(f"Error getting subscription data for UUID {uuid}: {e}")
        return web.json_response(
            {"error": "Internal server error"},
            status=500
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
    
    # API routes (MUST be registered BEFORE catch-all route to avoid conflicts)
    logger.info("Registering API routes...")
    
    # Register with explicit path matching to ensure they're not caught by catch-all
    app.router.add_get('/api/subscription/telegram/{telegram_id}', get_subscription_by_telegram_id)
    app.router.add_get('/api/subscription/{uuid}', get_subscription_data)
    
    # Health check endpoint
    async def health_check(request):
        return web.json_response({"status": "ok", "service": "mini-app"})
    
    app.router.add_get('/api/health', health_check)
    
    # Static files (catch-all, must be last)
    # This will match everything that doesn't match API routes above
    logger.info("Registering static files route...")
    app.router.add_get('/{path:.*}', serve_static)
    
    logger.info("Application routes registered")
    logger.info("API endpoints available:")
    logger.info("  GET /api/subscription/telegram/{telegram_id}")
    logger.info("  GET /api/subscription/{uuid}")
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

