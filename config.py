"""
Configuration module - loads and validates environment variables.
"""
import os
from dotenv import load_dotenv

# Load environment variables from .env if present
load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
OWNER_ID_ENV = os.getenv("OWNER_ID", "").strip()
SUPPORT_CHAT_ID_ENV = os.getenv("SUPPORT_CHAT_ID", "").strip()
DB_PATH = os.getenv("DB_PATH", "data.db").strip() or "data.db"
ARCHIVE_AFTER_HOURS = int(os.getenv("ARCHIVE_AFTER_HOURS", "72").strip() or 72)
RATINGS_NOTIFICATIONS_THREAD_ID = int(os.getenv("RATINGS_NOTIFICATIONS_THREAD_ID", "1").strip() or "1")

# Mini-app configuration
MINIAPP_URL = os.getenv("MINIAPP_URL", "").strip() or None  # URL to mini-app (e.g., https://app.heavengate.net)
MINIAPP_PORT = int(os.getenv("MINIAPP_PORT", "8080").strip() or 8080)

# Payment Gateway configuration (Berekebank.kz)
PAYMENT_GATEWAY_URL = os.getenv("PAYMENT_GATEWAY_URL", "https://3dsec.berekebank.kz").strip()
PAYMENT_GATEWAY_USERNAME = os.getenv("PAYMENT_GATEWAY_USERNAME", "").strip()
PAYMENT_GATEWAY_PASSWORD = os.getenv("PAYMENT_GATEWAY_PASSWORD", "").strip()

if not TELEGRAM_BOT_TOKEN:
    raise RuntimeError("TELEGRAM_BOT_TOKEN is not set. Put it in your environment or a .env file.")

OWNER_ID: int | None = int(OWNER_ID_ENV) if OWNER_ID_ENV.isdigit() else None
SUPPORT_CHAT_ID: int | None = int(SUPPORT_CHAT_ID_ENV) if SUPPORT_CHAT_ID_ENV.startswith("-") else None

