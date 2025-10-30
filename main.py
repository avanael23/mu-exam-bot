# main.py
import os
import sqlite3
import time
import logging
from io import BytesIO
from pathlib import Path
from datetime import datetime

from flask import Flask, request
import requests
import telebot
from telebot import types
from PIL import Image

# Google GenAI SDK (uses the modern package style)
from google import genai

# ---------- Configuration ----------
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
RENDER_APP_URL = os.environ.get("RENDER_APP_URL")  # e.g. https://mu-exam-bot.onrender.com
ADMIN_IDS = os.environ.get("ADMIN_IDS", "")  # optional comma-separated admin ids

if not TELEGRAM_TOKEN or not GEMINI_API_KEY:
    raise RuntimeError("Please set TELEGRAM_BOT_TOKEN and GEMINI_API_KEY in environment variables.")

# parse ADMIN_IDS into a set of ints
ADMIN_IDS = {int(x) for x in ADMIN_IDS.split(",") if x.strip().isdigit()}

UPLOAD_DIR = Path("uploads")
UPLOAD_DIR.mkdir(exist_ok=True)

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("mu-exam-bot")

# ---------- Database (sqlite) ----------
DB_PATH = "resources.db"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS resources (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT,
        filename TEXT,
        course_code TEXT,
        department TEXT,
        uploader TEXT,
        uploaded_at TEXT
    )
    """)
    conn.commit()
    conn.close()

init_db()

def add_resource(title, filename, course_code="", department="", uploader="unknown"):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO resources (title, filename, course_code, department, uploader, uploaded_at) VALUES (?,?,?,?,?,?)",
        (title, filename, course_code, department, uploader, datetime.utcnow().isoformat())
    )
    conn.commit()
    res_id = cur.lastrowid
    conn.close()
    return res_id

def search_resources(q, limit=10):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    like = f"%{q}%"
    cur.execute("""
    SELECT id, title, filename, course_code, department FROM resources
    WHERE title LIKE ? OR course_code LIKE ? OR department LIKE ?
    ORDER BY uploaded_at DESC
    LIMIT ?
    """, (like, like, like, limit))
    rows = cur.fetchall()
    conn.close()
    return rows

def get_resource(res_id):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT id, title, filename, course_code, department FROM resources WHERE id = ?", (res_id,))
    row = cur.fetchone()
    conn.close()
    return row

# ---------- Telegram bot and Gemini client ----------
bot = telebot.TeleBot(TELEGRAM_TOKEN)

# Initialize Google GenAI client
client = genai.Client(api_key=GEMINI_API_KEY)

TEXT_MODEL = "gemini-2.5-flash"  # text-capable model id
IMAGE_MODEL = "gemini-2.5-flash-image-preview"  # image-capable model id (if available)

def gemini_text_query(prompt, max_output_tokens=512):
    try:
        response = client.models.generate_content(model=TEXT_MODEL, contents=prompt)
        # try common attributes
        if hasattr(response, "text") and response.text:
            return response.text
        # fallback candidates
        candidates = getattr(response, "candidates", None)
        if candidates and len(candidates):
            c = candidates[0]
            content = getattr(c, "content", None)
            if hasattr(content, "text"):
                return content.text
            return str(content)
        return "Sorry — Gemini returned no answer."
    except Exception as e:
        logger.exception("Gemini text call failed")
        return f"Error querying Gemini: {e}"

def gemini_image_query(image_bytes: bytes, prompt: str):
    try:
        image = Image.open(BytesIO(image_bytes)).convert("RGB")
        response = client.models.generate_content(model=IMAGE_MODEL, contents=[prompt, image])
        if hasattr(response, "text") and response.text:
            return response.text
        candidates = getattr(response, "candidates", None)
        if candidates and len(candidates):
            c = candidates[0]
            content = getattr(c, "content", None)
            if hasattr(content, "text"):
                return content.text
            return str(content)
        return "No textual response from Gemini."
    except Exception as e:
        logger.exception("Gemini image error")
        return f"Error processing image: {e}"

# ---------- Bot handlers ----------
START_TEXT = (
    "*Welcome to Mekelle University Exam Share Bot* 🎓\n\n"
    "I help MU students access past exams, tutorial sheets and module PDFs.\n\n"
    "Commands:\n"
    "/search <query> - search by course name, code or department\n"
    "/list - list recent uploads\n"
    "/help - show this message\n\n"
    "Admins: send a PDF as a document with caption: Title|COURSE_CODE|DEPARTMENT\n"
)

@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    bot.send_message(message.chat.id, START_TEXT, parse_mode="Markdown")

@bot.message_handler(commands=['list'])
def cmd_list(message):
    rows = search_resources("", limit=20)
    if not rows:
        bot.send_message(message.chat.id, "No resources uploaded yet.")
        return
    text = "Recent resources:\n"
    for r in rows:
        text += f"{r[0]}. {r[1]} — {r[2]} ({r[3] or 'no code'})\n"
    bot.send_message(message.chat.id, text)

@bot.message_handler(commands=['search'])
def cmd_search(message):
    args = message.text.partition(" ")[2].strip()
    if not args:
        bot.send_message(message.chat.id, "Usage: /search <course name or code or department>")
        return
    rows = search_resources(args, limit=20)
    if not rows:
        bot.send_message(message.chat.id, "No matches found.")
        return
    for r in rows:
        markup = types.InlineKeyboardMarkup()
        btn = types.InlineKeyboardButton("Download", callback_data=f"get:{r[0]}")
        markup.add(btn)
        bot.send_message(message.chat.id, f"{r[0]}. {r[1]}\nCourse: {r[3] or 'N/A'}\nDept: {r[4] or 'N/A'}", reply_markup=markup)

@bot.callback_query_handler(func=lambda c: c.data and c.data.startswith("get:"))
def handle_get_callback(call):
    _, id_str = call.data.split(":", 1)
    try:
        rid = int(id_str)
    except:
        bot.answer_callback_query(call.id, "Invalid id.")
        return
    row = get_resource(rid)
    if not row:
        bot.answer_callback_query(call.id, "Resource not found.")
        return
    _, title, filename, course_code, department = row
    fpath = UPLOAD_DIR / filename
    if not fpath.exists():
        bot.answer_callback_query(call.id, "File missing on server.")
        return
    with open(fpath, "rb") as fh:
        bot.send_document(call.message.chat.id, fh, caption=f"{title} — {course_code} / {department}")
    bot.answer_callback_query(call.id, "Sent!")

@bot.message_handler(content_types=['document'])
def handle_document(message):
    user_id = message.from_user.id
    if user_id not in ADMIN_IDS:
        bot.reply_to(message, "Only admins may upload files. Ask an admin to upload.")
        return
    doc = message.document
    file_info = bot.get_file(doc.file_id)
    file_path = file_info.file_path
    file_url = f"https://api.telegram.org/file/bot{TELEGRAM_TOKEN}/{file_path}"
    r = requests.get(file_url)
    if r.status_code != 200:
        bot.reply_to(message, "Failed to download document from Telegram.")
        return
    caption = (message.caption or "").strip()
    if "|" in caption:
        title, course_code, department = [x.strip() for x in caption.split("|", 2)]
    else:
        title = caption or doc.file_name or f"resource_{int(time.time())}"
        course_code = ""
        department = ""
    safe_name = f"{int(time.time())}_{doc.file_name}"
    out_path = UPLOAD_DIR / safe_name
    with open(out_path, "wb") as fh:
        fh.write(r.content)
    add_resource(title=title, filename=safe_name, course_code=course_code, department=department, uploader=str(message.from_user.id))
    bot.reply_to(message, f"Uploaded: {title} (Course: {course_code or 'N/A'})")

@bot.message_handler(content_types=['photo'])
def handle_photo(message):
    bot.reply_to(message, "Processing image... (may take a few seconds)")
    photo = message.photo[-1]
    file_info = bot.get_file(photo.file_id)
    file_path = file_info.file_path
    file_url = f"https://api.telegram.org/file/bot{TELEGRAM_TOKEN}/{file_path}"
    r = requests.get(file_url)
    if r.status_code != 200:
        bot.reply_to(message, "Failed to download image.")
        return
    img_bytes = r.content
    prompt = ("You are a helpful study assistant. Describe this image, point out anything useful to students, "
              "and suggest 5 possible exam-style questions about the content of the image.")
    gemini_resp = gemini_image_query(img_bytes, prompt)
    bot.reply_to(message, gemini_resp)

@bot.message_handler(func=lambda m: True, content_types=['text'])
def handle_text(message):
    text = message.text.strip()
    if text.lower().startswith("search:"):
        q = text.partition(":")[2].strip()
        rows = search_resources(q, limit=10)
        if not rows:
            bot.reply_to(message, "No resources found.")
            return
        s = "\n".join([f"{r[0]}. {r[1]} — {r[2]}" for r in rows])
        bot.reply_to(message, s)
        return
    bot.send_chat_action(message.chat.id, 'typing')
    prompt = f"You are a helpful assistant for Mekelle University students. Answer the following concisely and clearly:\n\n{text}"
    resp = gemini_text_query(prompt)
    bot.reply_to(message, resp)

# ---------- Flask app: webhook endpoints ----------
app = Flask(__name__)

@app.route("/", methods=["GET"])
def index():
    # This route sets webhook when visited (safe to call repeatedly).
    if not RENDER_APP_URL:
        message = ("RENDER_APP_URL is not configured. "
                   "Set env var RENDER_APP_URL to your https URL (no trailing slash).")
        logger.error(message)
        return message, 500
    webhook_url = f"{RENDER_APP_URL}/webhook"
    try:
        bot.remove_webhook()
        bot.set_webhook(url=webhook_url)
        logger.info(f"Webhook set to {webhook_url}")
        return f"Webhook set to {webhook_url}\nBot is running.", 200
    except Exception as e:
        logger.exception("Failed to set webhook")
        return f"Failed to set webhook: {e}", 500

@app.route("/webhook", methods=["POST"])
def telegram_webhook():
    # Telegram will POST updates here
    try:
        json_str = request.get_data().decode("utf-8")
        update = telebot.types.Update.de_json(json_str)
        bot.process_new_updates([update])
        return "", 200
    except Exception as e:
        logger.exception("Error processing update")
        return "", 500

# ---------- Run server (for local dev) ----------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    logger.info("Starting Flask server...")
    app.run(host="0.0.0.0", port=port)