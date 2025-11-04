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
    if not telegram_id_str:
        return web.json_response(
            {"error": "Telegram ID is required"},
            status=400
        )

    try:
        telegram_id = int(telegram_id_str)
        
        # Get UUID from database by Telegram ID
        from database import db_get_user_backend_data
        backend_data = db_get_user_backend_data(telegram_id)
        
        if not backend_data or not backend_data[0]:
            return web.json_response(
                {"error": "User not found or not linked. Please contact support to link your account."},
                status=404
            )
        
        uuid = backend_data[0]
        
        # Get user data from backend API
        user_data = await get_user_by_uuid(uuid)
        
        if not user_data:
            return web.json_response(
                {"error": "Subscription data not found"},
                status=404
            )

        # Return subscription data
        return web.json_response(user_data)
        
    except ValueError:
        return web.json_response(
            {"error": "Invalid Telegram ID"},
            status=400
        )
    except Exception as e:
        logger.exception(f"Error getting subscription data for Telegram ID {telegram_id_str}: {e}")
        return web.json_response(
            {"error": "Internal server error"},
            status=500
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
    
    # Security: prevent directory traversal
    if '..' in path or path.startswith('/'):
        return web.Response(status=403, text="Forbidden")
    
    file_path = MINIAPP_DIR / path
    
    # Default to index.html if path is a directory
    if file_path.is_dir():
        file_path = file_path / 'index.html'
    
    if not file_path.exists():
        return web.Response(status=404, text="Not Found")
    
    # Determine content type
    content_type = 'text/html'
    if path.endswith('.js'):
        content_type = 'application/javascript'
    elif path.endswith('.css'):
        content_type = 'text/css'
    elif path.endswith('.json'):
        content_type = 'application/json'
    
    return web.Response(
        body=file_path.read_bytes(),
        content_type=content_type
    )


def create_app() -> web.Application:
    """Create aiohttp application."""
    app = web.Application()
    
    # API routes
    app.router.add_get('/api/subscription/telegram/{telegram_id}', get_subscription_by_telegram_id)
    app.router.add_get('/api/subscription/{uuid}', get_subscription_data)
    
    # Static files (catch-all, must be last)
    app.router.add_get('/{path:.*}', serve_static)
    
    return app


async def run_server(host: str = "0.0.0.0", port: int = 8080):
    """Run the mini-app server."""
    from config import MINIAPP_PORT
    
    app = create_app()
    runner = web.AppRunner(app)
    await runner.setup()
    
    actual_port = port or MINIAPP_PORT
    site = web.TCPSite(runner, host, actual_port)
    await site.start()
    
    logger.info(f"Mini-app server started on http://{host}:{actual_port}")
    
    # Keep running
    try:
        await asyncio.Future()  # Run forever
    except KeyboardInterrupt:
        logger.info("Shutting down mini-app server...")
    finally:
        await runner.cleanup()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    asyncio.run(run_server())

