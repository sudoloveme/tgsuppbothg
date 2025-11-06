"""
SMTP client for sending emails via SendPulse.
"""
import logging
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Optional
import os
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("support-bot")

# SendPulse SMTP configuration
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.sendpulse.com").strip()
SMTP_PORT = int(os.getenv("SMTP_PORT", "587").strip() or 587)
SMTP_USER = os.getenv("SMTP_USER", "").strip()
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "").strip()
SMTP_FROM_EMAIL = os.getenv("SMTP_FROM_EMAIL", "").strip()
SMTP_FROM_NAME = os.getenv("SMTP_FROM_NAME", "HeavenGate VPN").strip()


def send_otp_email(email: str, otp_code: str) -> bool:
    """
    Send OTP code to user's email via SendPulse SMTP.
    
    Args:
        email: Recipient email address
        otp_code: OTP code to send
        
    Returns:
        True if email sent successfully, False otherwise
    """
    if not SMTP_USER or not SMTP_PASSWORD or not SMTP_FROM_EMAIL:
        logger.error("SMTP credentials not configured. Cannot send email.")
        return False
    
    try:
        # Create message
        msg = MIMEMultipart('alternative')
        msg['Subject'] = "Код подтверждения - HeavenGate VPN"
        msg['From'] = f"{SMTP_FROM_NAME} <{SMTP_FROM_EMAIL}>"
        msg['To'] = email
        
        # Create HTML email body
        html_body = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <style>
                body {{
                    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
                    line-height: 1.6;
                    color: #333;
                    max-width: 600px;
                    margin: 0 auto;
                    padding: 20px;
                }}
                .container {{
                    background: #ffffff;
                    border-radius: 12px;
                    padding: 30px;
                    box-shadow: 0 2px 8px rgba(0, 0, 0, 0.1);
                }}
                .otp-code {{
                    background: #007AFF;
                    color: #ffffff;
                    font-size: 32px;
                    font-weight: 700;
                    text-align: center;
                    padding: 20px;
                    border-radius: 8px;
                    letter-spacing: 4px;
                    margin: 20px 0;
                }}
                .footer {{
                    margin-top: 30px;
                    padding-top: 20px;
                    border-top: 1px solid #e5e5ea;
                    font-size: 12px;
                    color: #8e8e93;
                    text-align: center;
                }}
            </style>
        </head>
        <body>
            <div class="container">
                <h1>Код подтверждения</h1>
                <p>Ваш код подтверждения для авторизации в HeavenGate VPN:</p>
                <div class="otp-code">{otp_code}</div>
                <p>Этот код действителен в течение 10 минут.</p>
                <p>Если вы не запрашивали этот код, просто проигнорируйте это письмо.</p>
                <div class="footer">
                    <p>HeavenGate VPN</p>
                </div>
            </div>
        </body>
        </html>
        """
        
        # Create plain text version
        text_body = f"""
Код подтверждения для авторизации в HeavenGate VPN:

{otp_code}

Этот код действителен в течение 10 минут.

Если вы не запрашивали этот код, просто проигнорируйте это письмо.

HeavenGate VPN
        """
        
        # Attach parts
        part1 = MIMEText(text_body, 'plain', 'utf-8')
        part2 = MIMEText(html_body, 'html', 'utf-8')
        
        msg.attach(part1)
        msg.attach(part2)
        
        # Send email
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.send_message(msg)
        
        logger.info(f"OTP email sent successfully to {email}")
        return True
        
    except Exception as e:
        logger.error(f"Failed to send OTP email to {email}: {e}")
        return False

