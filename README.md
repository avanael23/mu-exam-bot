# Mekelle University Exam Share Bot

Telegram bot (Flask + Telebot) to share past exams and tutorial materials.
Uses Google Gemini (Gemini 2.5 Flash) for answering questions and image analysis.

## Files
- main.py — application & webhook
- db_init.py — initialize sqlite DB
- requirements.txt — dependencies
- Procfile — start command for Render

## Deploy steps (Render)
1. Push this repo to GitHub.
2. On Render, create a new Web Service, connect repo.
3. Environment variables (Render → Environment):
   - TELEGRAM_BOT_TOKEN = your bot token
   - GEMINI_API_KEY = your Gemini API key
   - RENDER_APP_URL = https://your-app-name.onrender.com   (no trailing slash)
   - ADMIN_IDS = 123456789 (optional)
4. Start deploy. When live, open the app root:
   `https://your-app-name.onrender.com/`
   That will set the webhook automatically to `https://.../webhook`.
5. Test the bot in Telegram.

## Local testing
- Run `python db_init.py`
- Run `python main.py` (local dev); use ngrok/localtunnel to expose port 5000 if testing webhooks.