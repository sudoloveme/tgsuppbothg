"""
OTP (One-Time Password) manager for email authentication.
"""
import logging
import secrets
import string
from datetime import datetime, timedelta
from typing import Optional
import database

logger = logging.getLogger("support-bot")

# OTP configuration
OTP_LENGTH = 6
OTP_EXPIRY_MINUTES = 10


def generate_otp() -> str:
    """
    Generate a random 6-digit OTP code.
    
    Returns:
        6-digit OTP code as string
    """
    return ''.join(secrets.choice(string.digits) for _ in range(OTP_LENGTH))


def store_otp(email: str, telegram_id: int, otp_code: str) -> bool:
    """
    Store OTP code in database with expiration time.
    
    Args:
        email: User's email address
        telegram_id: User's Telegram ID
        otp_code: Generated OTP code
        
    Returns:
        True if stored successfully, False otherwise
    """
    try:
        database.db_store_otp(email, telegram_id, otp_code)
        logger.info(f"OTP stored for email: {email}, telegram_id: {telegram_id}")
        return True
    except Exception as e:
        logger.error(f"Failed to store OTP: {e}")
        return False


def verify_otp(email: str, telegram_id: int, otp_code: str) -> bool:
    """
    Verify OTP code for email and telegram_id.
    
    Args:
        email: User's email address
        telegram_id: User's Telegram ID
        otp_code: OTP code to verify
        
    Returns:
        True if OTP is valid, False otherwise
    """
    try:
        stored_otp = database.db_get_otp(email, telegram_id)
        if not stored_otp:
            logger.warning(f"No OTP found for email: {email}, telegram_id: {telegram_id}")
            return False
        
        stored_code, created_at = stored_otp
        
        # Check if OTP matches
        if stored_code != otp_code:
            logger.warning(f"OTP mismatch for email: {email}, telegram_id: {telegram_id}")
            return False
        
        # Check if OTP is expired
        created_time = datetime.fromisoformat(created_at)
        expiry_time = created_time + timedelta(minutes=OTP_EXPIRY_MINUTES)
        
        if datetime.now() > expiry_time:
            logger.warning(f"OTP expired for email: {email}, telegram_id: {telegram_id}")
            database.db_delete_otp(email, telegram_id)
            return False
        
        # OTP is valid, delete it after verification
        database.db_delete_otp(email, telegram_id)
        logger.info(f"OTP verified successfully for email: {email}, telegram_id: {telegram_id}")
        return True
        
    except Exception as e:
        logger.error(f"Failed to verify OTP: {e}")
        return False


def cleanup_expired_otps():
    """
    Clean up expired OTP codes from database.
    This should be called periodically.
    """
    try:
        database.db_cleanup_expired_otps(OTP_EXPIRY_MINUTES)
    except Exception as e:
        logger.error(f"Failed to cleanup expired OTPs: {e}")

