# Render Blueprint — Telegram Password Reset Bot (Hobby / Free)

This repository contains a minimal, single-file Flask + Telegram bot app (`app.py`) and a `render.yaml` for easy deployment on Render's free Hobby tier.

## Files
- `app.py` — main application (web + bot)
- `render.yaml` — Render blueprint to create a Web service and a Worker
- `requirements.txt` — Python dependencies
- `.gitignore`
- `.env.example` — sample env file (do not commit secrets)

## Quick deploy (summary)
1. Create a new GitHub repo and push these files.
2. In Render → New → Connect repository. Render will detect `render.yaml` and propose to create the services.
3. Provision a free **Render Postgres (Hobby)** from the dashboard (Services → New → PostgreSQL).
4. In each service (web and worker) set environment variables:
   - **Web service**: set `FRONTEND_BASE` to `https://<your-web-service>.onrender.com`
   - **Worker**: set `BOT_TOKEN` to your Telegram bot token
   - For both: set SMTP vars (`SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASS`, `FROM_EMAIL`)
   - Paste `DATABASE_URL` from the Postgres add-on into both services' env vars.
5. For the worker service set **Scale = 1** (only one poller).
6. Deploy. Check logs for `Starting Telegram bot (polling)`.

## Running locally
1. Copy `.env.example` to `.env` and fill values.
2. Create a virtualenv and install:
   ```bash
   python -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   ```
3. Run web (for testing):
   ```bash
   export FLASK_APP=app.py
   python app.py web
   # or: gunicorn app:web_app -b 0.0.0.0:8000
   ```
4. Run bot (local testing polling):
   ```bash
   python app.py bot
   ```

## Notes & security
- **Do not** use this to target other services (Instagram/Gmail/etc.). This is for your app's users only.
- Use a proper password hashing strategy (bcrypt/argon2) and update the `post_reset` handler to change real user passwords.
- For email delivery, use a transactional email provider (SendGrid/Mailgun/SES) for better deliverability.
- Keep secrets in Render's dashboard — do not commit them to the repo.
