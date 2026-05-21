import smtplib
from email.message import EmailMessage

from app.config import Settings


def send_verification_code(settings: Settings, email: str, code: str) -> None:
    if not settings.smtp_host:
        print(f"[AiWardrobe] Verification code for {email}: {code}")
        return

    message = EmailMessage()
    message["Subject"] = "Your AiWardrobe verification code"
    message["From"] = settings.smtp_from_email
    message["To"] = email
    message.set_content(f"Your AiWardrobe login code is {code}. It expires in {settings.email_code_minutes} minutes.")

    with smtplib.SMTP(settings.smtp_host, settings.smtp_port) as smtp:
        smtp.starttls()
        if settings.smtp_username and settings.smtp_password:
            smtp.login(settings.smtp_username, settings.smtp_password)
        smtp.send_message(message)
