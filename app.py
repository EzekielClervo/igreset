# app.py
"""
Single main file for Render deployment.

Usage on Render:
- Web service (Flask) start command:
    gunicorn app:web_app -b 0.0.0.0:$PORT
- Worker (Telegram polling bot) start command:
    python app.py bot

ENV (required):
- BOT_TOKEN            -> Telegram bot token
- FRONTEND_BASE        -> public URL of the web service (e.g. https://myapp.onrender.com)
- RESET_PATH           -> path for reset endpoint (default: /reset)
- RESET_EXPIRY_MINUTES -> token expiry (default: 60)
- SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS, FROM_EMAIL -> SMTP for sending emails
- DATABASE_URL         -> SQLAlchemy DB URL (Render Postgres). If omitted, falls back to sqlite file reset_tokens.db

Dependencies (put in requirements.txt):
flask
python-telegram-bot==20.4
SQLAlchemy
psycopg2-binary

This file implements:
- a small Flask web app (object: web_app) serving /reset
- a Telegram bot (polling) to request reset tokens and email users
- DB layer using SQLAlchemy (works with Postgres or sqlite fallback)

Only use this for your own application accounts. Do NOT use for other services.
"""

import os
import re
import sys
import secrets
import smtplib
import ssl
from datetime import datetime, timedelta
from email.message import EmailMessage

from flask import Flask, request, render_template_string

# SQLAlchemy
from sqlalchemy import (create_engine, Column, Integer, String, DateTime,
                        Boolean)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

# Telegram
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (Application, CommandHandler, CallbackQueryHandler,
                          MessageHandler, filters, ContextTypes, ConversationHandler)

# -----------------------------
# Configuration (from env)
# -----------------------------
BOT_TOKEN = os.environ.get("BOT_TOKEN")
FRONTEND_BASE = os.environ.get("FRONTEND_BASE", "http://localhost:8000")
RESET_PATH = os.environ.get("RESET_PATH", "/reset")
RESET_EXPIRY_MINUTES = int(os.environ.get("RESET_EXPIRY_MINUTES", "60"))

SMTP_HOST = os.environ.get("SMTP_HOST")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER")
SMTP_PASS = os.environ.get("SMTP_PASS")
FROM_EMAIL = os.environ.get("FROM_EMAIL", SMTP_USER)

DATABASE_URL = os.environ.get("DATABASE_URL")

# -----------------------------
# DB setup
# -----------------------------
if DATABASE_URL:
    DATABASE_URL_USED = DATABASE_URL
    engine = create_engine(DATABASE_URL_USED, pool_pre_ping=True)
else:
    # fallback to sqlite file for quick testing (not recommended for prod)
    DATABASE_URL_USED = "sqlite:///reset_tokens.db"
    engine = create_engine(DATABASE_URL_USED, connect_args={"check_same_thread": False})

Base = declarative_base()

class ResetToken(Base):
    __tablename__ = "reset_tokens"
    id = Column(Integer, primary_key=True)
    email = Column(String, nullable=False, index=True)
    token = Column(String, nullable=False, unique=True, index=True)
    created_at = Column(DateTime, nullable=False)
    expires_at = Column(DateTime, nullable=False)
    used = Column(Boolean, default=False)

SessionLocal = sessionmaker(bind=engine)
Base.metadata.create_all(engine)

# -----------------------------
# DB helpers
# -----------------------------
def create_token(email: str) -> str:
    session = SessionLocal()
    token = secrets.token_urlsafe(32)
    now = datetime.utcnow()
    expires = now + timedelta(minutes=RESET_EXPIRY_MINUTES)
    r = ResetToken(email=email.lower().strip(), token=token, created_at=now, expires_at=expires, used=False)
    session.add(r)
    session.commit()
    session.close()
    return token

def get_token_row(token: str):
    session = SessionLocal()
    row = session.query(ResetToken).filter(ResetToken.token == token).first()
    session.close()
    return row

def mark_used(token: str):
    session = SessionLocal()
    row = session.query(ResetToken).filter(ResetToken.token == token).first()
    if row:
        row.used = True
        session.commit()
    session.close()

# -----------------------------
# Email helper
# -----------------------------
def send_reset_email(to_email: str, token: str):
    reset_link = f"{FRONTEND_BASE.rstrip('/')}{RESET_PATH}?token={token}"
    msg = EmailMessage()
    msg["Subject"] = "Password reset request"
    msg["From"] = FROM_EMAIL or "no-reply@example.com"
    msg["To"] = to_email
    msg.set_content(f"""
Hello,

A password reset was requested for this account. If you requested it, open the link below to reset your password:

{reset_link}

If you didn't request this, ignore this email.
This link expires in {RESET_EXPIRY_MINUTES} minutes.
""")

    if not SMTP_HOST or not FROM_EMAIL:
        raise RuntimeError("SMTP not configured. Set SMTP_HOST, SMTP_USER, SMTP_PASS, FROM_EMAIL in env.")

    ctx = ssl.create_default_context()
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        try:
            server.starttls(context=ctx)
        except Exception:
            pass
        if SMTP_USER and SMTP_PASS:
            server.login(SMTP_USER, SMTP_PASS)
        server.send_message(msg)

# -----------------------------
# Flask web part (reset page)
# -----------------------------
web_app = Flask(__name__)

HTML_FORM = """
<!doctype html>
<title>Password Reset</title>
<h2>Password Reset</h2>
{% if message %}<p>{{ message }}</p>{% endif %}
{% if show_form %}
<form method="POST">
  <label>New password for {{ email }}:</label><br>
  <input name="password" type="password" required minlength="6"><br><br>
  <button type="submit">Set new password</button>
</form>
{% endif %}
"""

@web_app.route(RESET_PATH, methods=["GET"])
def get_reset():
    token = request.args.get("token", "")
    if not token:
        return render_template_string(HTML_FORM, message="Missing token.", show_form=False)
    row = get_token_row(token)
    if not row:
        return render_template_string(HTML_FORM, message="Invalid token.", show_form=False)
    if row.used:
        return render_template_string(HTML_FORM, message="This link has already been used.", show_form=False)
    if row.expires_at < datetime.utcnow():
        return render_template_string(HTML_FORM, message="This link has expired.", show_form=False)
    # Show the password form
    return render_template_string(HTML_FORM, message=None, show_form=True, email=row.email)

@web_app.route(RESET_PATH, methods=["POST"])
def post_reset():
    token = request.args.get("token", "")
    password = request.form.get("password", "")
    if not token or not password:
        return "Missing token or password", 400
    row = get_token_row(token)
    if not row or row.used or row.expires_at < datetime.utcnow():
        return "Invalid or expired token", 400
    # TODO: integrate with your users DB here -> find user by email and set hashed password
    # For now we just mark the token used
    mark_used(token)
    return "Password updated (demo). Connect this endpoint to your user DB to actually change passwords."

# -----------------------------
# Telegram bot part
# -----------------------------
ASK_EMAIL = 0

EMAIL_RE = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
def valid_email(email: str) -> bool:
    return EMAIL_RE.match(email) is not None

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [[InlineKeyboardButton("🔄 Reset Password", callback_data="reset")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Welcome! Choose an option:", reply_markup=reply_markup)

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "reset":
        await query.edit_message_text("Please reply with the **email** you want to reset (example: you@domain.com).")

async def reset_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Please reply with the **email** you want to reset (example: you@domain.com).")
    return ASK_EMAIL

async def receive_email(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = update.message.text.strip()
    if not valid_email(user_text):
        await update.message.reply_text("That doesn't look like a valid email. Please try again.")
        return ASK_EMAIL

    token = create_token(user_text)
    try:
        send_reset_email(user_text, token)
    except Exception as e:
        await update.message.reply_text("Failed to send email. Check SMTP settings in environment variables.")
        return ConversationHandler.END

    await update.message.reply_text(f"Reset link sent to {user_text}. Check your email.")
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Cancelled.")
    return ConversationHandler.END

async def run_bot():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN env variable is required for bot mode")

    app = Application.builder().token(BOT_TOKEN).build()

    conv = ConversationHandler(
        entry_points=[CommandHandler("reset", reset_command)],
        states={ASK_EMAIL: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_email)]},
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(conv)

    print("Starting Telegram bot (polling).")
    await app.run_polling()

# -----------------------------
# Entrypoint
# -----------------------------
if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1].lower() == "bot":
        import asyncio
        asyncio.run(run_bot())
    elif len(sys.argv) >= 2 and sys.argv[1].lower() == "web":
        # run built-in server (use gunicorn on Render)
        web_app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
    else:
        print("Usage: python app.py [bot|web]")
        print("- Bot mode (startCommand): python app.py bot")
        print("- Web mode (gunicorn): gunicorn app:web_app -b 0.0.0.0:$PORT")
