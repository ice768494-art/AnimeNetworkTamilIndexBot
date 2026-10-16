import os
import sqlite3
import logging
import threading
import asyncio
from datetime import datetime, timedelta, timezone

from flask import Flask, request, jsonify
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, filters
)
from telegram.error import TelegramError

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_IDS = {
    int(x.strip())
    for x in os.getenv("ADMIN_IDS", "").split(",")
    if x.strip().isdigit()
}
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "").rstrip("/")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "").strip()
PORT = int(os.getenv("PORT", "10000"))
DB_PATH = os.getenv("DB_PATH", "/var/data/anime.db")
DEFAULT_EXPIRY = int(os.getenv("DEFAULT_EXPIRY_MINUTES", "10"))

os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("anime-channel-finder")

db = sqlite3.connect(DB_PATH, check_same_thread=False)
db_lock = threading.RLock()
db.execute("PRAGMA foreign_keys=ON")
db.execute("""
    CREATE TABLE IF NOT EXISTS anime (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT UNIQUE NOT NULL,
        poster_file_id TEXT
    )
""")
db.execute("""
    CREATE TABLE IF NOT EXISTS channels (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        anime_id INTEGER NOT NULL,
        chat_id TEXT NOT NULL,
        title TEXT NOT NULL,
        FOREIGN KEY(anime_id) REFERENCES anime(id) ON DELETE CASCADE
    )
""")
db.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
db.execute(
    "INSERT OR IGNORE INTO settings(key,value) VALUES('expiry_minutes',?)",
    (str(DEFAULT_EXPIRY),)
)
db.commit()

app = Flask(__name__)
telegram_app = Application.builder().token(BOT_TOKEN).build()

# Add more names here if you want them to appear in the availability list.
ANIME_CATALOG = ["Black Clover", "Black Clover Tamil"]


def is_admin(user_id):
    return user_id in ADMIN_IDS


def get_expiry():
    with db_lock:
        row = db.execute("SELECT value FROM settings WHERE key='expiry_minutes'").fetchone()
    return int(row[0]) if row else DEFAULT_EXPIRY


def set_expiry(minutes):
    with db_lock:
        db.execute(
            "INSERT INTO settings(key,value) VALUES('expiry_minutes',?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (str(minutes),)
        )
        db.commit()


def get_anime(name):
    with db_lock:
        return db.execute(
            "SELECT id,name,poster_file_id FROM anime WHERE lower(name)=lower(?)",
            (name.strip(),)
        ).fetchone()


def all_anime():
    with db_lock:
        return db.execute("SELECT id,name,poster_file_id FROM anime ORDER BY lower(name)").fetchall()


def channels_for(anime_id):
    with db_lock:
        return db.execute(
            "SELECT id,chat_id,title FROM channels WHERE anime_id=? ORDER BY id",
            (anime_id,)
        ).fetchall()


def add_anime(name):
    with db_lock:
        cur = db.execute("INSERT INTO anime(name) VALUES(?)", (name.strip(),))
        db.commit()
        return cur.lastrowid


def remove_anime(name):
    row = get_anime(name)
    if not row:
        return False
    with db_lock:
        db.execute("DELETE FROM channels WHERE anime_id=?", (row[0],))
        db.execute("DELETE FROM anime WHERE id=?", (row[0],))
        db.commit()
    return True


def add_channel(anime_id, chat_id, title):
    with db_lock:
        db.execute(
            "INSERT INTO channels(anime_id,chat_id,title) VALUES(?,?,?)",
            (anime_id, str(chat_id), title)
        )
        db.commit()


def set_poster(anime_id, file_id):
    with db_lock:
        db.execute("UPDATE anime SET poster_file_id=? WHERE id=?", (file_id, anime_id))
        db.commit()


def admin_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("➕ Add Anime", callback_data="admin:add"),
            InlineKeyboardButton("🗑️ Remove", callback_data="admin:remove"),
        ],
        [
            InlineKeyboardButton("🖼️ Set Poster", callback_data="admin:poster"),
            InlineKeyboardButton("📋 Anime List", callback_data="admin:list"),
        ],
        [InlineKeyboardButton("⏳ Expiry", callback_data="admin:expiry")],
    ])


def availability_text():
    """Show each catalog title as available only when it has a configured channel."""
    lines = ["🎬 <b>Search Anime</b>\n"]
    names = list(ANIME_CATALOG)
    for row in all_anime():
        if not any(row[1].casefold() == n.casefold() for n in names):
            names.append(row[1])

    for name in names:
        row = get_anime(name)
        available = bool(row and channels_for(row[0]))
        lines.append(f"{name} {'✅' if available else '❌'}")

    lines.append("\n🔎 Send an anime name to get its channel link.")
    return "\n".join(lines)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = availability_text()
    if is_admin(update.effective_user.id):
        text += "\n\n⚙️ Admin: use /admin"
    await update.effective_message.reply_text(text, parse_mode="HTML")


async def admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.effective_message.reply_text("❌ Admin only.")
        return
    await update.effective_message.reply_text(
        f"⚙️ <b>Admin Panel</b>\n\n⏳ Current expiry: <b>{get_expiry()} minutes</b>",
        parse_mode="HTML",
        reply_markup=admin_keyboard()
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if is_admin(update.effective_user.id):
        await update.effective_message.reply_text(
            "⚙️ <b>Admin commands</b>\n\n"
            "/admin — Open panel\n"
            "/add Anime | @channel — Add anime/channel\n"
            "/remove Anime — Remove anime\n"
            "/setexpiry 10 — Set expiry\n"
            "/setphoto Anime — Reply to a photo\n"
            "/list — List anime\n\n"
            "👤 Users can send an anime name to search.",
            parse_mode="HTML"
        )
    else:
        await start(update, context)


async def send_anime_list(chat_id, context):
    rows = all_anime()
    if not rows:
        await context.bot.send_message(chat_id, "📋 No anime added yet.")
        return
    lines = ["📋 <b>Anime List</b>\n"]
    for row in rows:
        chs = channels_for(row[0])
        lines.append(f"🎬 <b>{row[1]}</b> — {len(chs)} channel(s)")
        for ch in chs:
            lines.append(f" • {ch[2]}")
    await context.bot.send_message(chat_id, "\n".join(lines), parse_mode="HTML")


async def list_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if is_admin(update.effective_user.id):
        await send_anime_list(update.effective_chat.id, context)


async def add_entry(update, context, raw):
    if "|" not in raw:
        await update.effective_message.reply_text(
            "Usage:\n<code>/add Black Clover | @YourChannel</code>",
            parse_mode="HTML"
        )
        return
    name, channel = [x.strip() for x in raw.split("|", 1)]
    if not name or not channel:
        await update.effective_message.reply_text("Enter an anime name and channel username.")
        return
    try:
        row = get_anime(name)
        anime_id = row[0] if row else add_anime(name)
        chat = await context.bot.get_chat(channel)
        with db_lock:
            exists = db.execute(
                "SELECT 1 FROM channels WHERE anime_id=? AND chat_id=?",
                (anime_id, str(chat.id))
            ).fetchone()
        if not exists:
            add_channel(anime_id, chat.id, chat.title or channel)
        await update.effective_message.reply_text(
            f"✅ Added <b>{name}</b>\n📺 {chat.title or channel}\n\n"
            "Users can now search this anime.",
            parse_mode="HTML"
        )
    except TelegramError as e:
        logger.exception("Could not add channel %r", channel)
        await update.effective_message.reply_text(
            "❌ Telegram could not access this channel. Make sure the bot is an "
            "administrator with permission to manage invite links, and use its public "
            f"@username.\n\nDetails: <code>{str(e)[:500]}</code>",
            parse_mode="HTML"
        )
    except sqlite3.IntegrityError:
        await update.effective_message.reply_text("❌ Could not add anime. Check whether its name already exists.")


async def add_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    await add_entry(update, context, update.effective_message.text.partition(" ")[2].strip())


async def remove_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    name = update.effective_message.text.partition(" ")[2].strip()
    if not name:
        await update.effective_message.reply_text("Usage: /remove Anime Name")
        return
    await update.effective_message.reply_text(
        "✅ Removed." if remove_anime(name) else "❌ Anime not found."
    )


async def setexpiry_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    raw = update.effective_message.text.partition(" ")[2].strip()
    try:
        minutes = int(raw)
        if not 1 <= minutes <= 1440:
            raise ValueError
        set_expiry(minutes)
        await update.effective_message.reply_text(f"⏳ Expiry set to {minutes} minutes.")
    except ValueError:
        await update.effective_message.reply_text("Use a number from 1 to 1440.")


async def setphoto_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    name = update.effective_message.text.partition(" ")[2].strip()
    reply = update.effective_message.reply_to_message
    if not name or not reply or not reply.photo:
        await update.effective_message.reply_text("Reply to a photo and use:\n/setphoto Anime Name")
        return
    row = get_anime(name)
    if not row:
        await update.effective_message.reply_text("❌ Anime not found.")
        return
    set_poster(row[0], reply.photo[-1].file_id)
    await update.effective_message.reply_text("🖼️ Poster saved.")


async def send_anime_links(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = update.effective_message.text.strip()
    row = get_anime(name)
    if not row:
        await update.effective_message.reply_text(
            "❌ Anime not found or not available yet.\n\n" + availability_text(),
            parse_mode="HTML"
        )
        return
    chs = channels_for(row[0])
    if not chs:
        await update.effective_message.reply_text("❌ This anime is listed but has no channel configured yet.")
        return

    expiry = get_expiry()
    expire_at = datetime.now(timezone.utc) + timedelta(minutes=expiry)
    buttons = []
    for ch in chs:
        try:
            invite = await context.bot.create_chat_invite_link(
                chat_id=int(ch[1]),
                name=f"Anime Finder {expiry}m",
                expire_date=expire_at,
                creates_join_request=True
            )
            buttons.append([InlineKeyboardButton(f"🔗 {ch[2]}", url=invite.invite_link)])
        except TelegramError as e:
            logger.warning("Invite creation failed for %s (%s): %s", ch[2], ch[1], e)

    if not buttons:
        await update.effective_message.reply_text(
            "❌ I found the anime, but couldn't create its channel link. "
            "The bot needs administrator permission to manage invite links."
        )
        return

    caption = (
        f"🎬 <b>{row[1]}</b>\n\n"
        f"⏳ Link expires in <b>{expiry} minutes</b>\n"
        "👇 Join Channel:"
    )
    markup = InlineKeyboardMarkup(buttons)
    if row[2]:
        try:
            await update.effective_message.reply_photo(
                photo=row[2], caption=caption, parse_mode="HTML", reply_markup=markup
            )
            return
        except TelegramError as e:
            logger.warning("Poster send failed: %s", e)
    await update.effective_message.reply_text(caption, parse_mode="HTML", reply_markup=markup)


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if not is_admin(q.from_user.id):
        await q.answer("Admin only.", show_alert=True)
        return

    if q.data == "admin:home":
        await q.edit_message_text(
            f"⚙️ <b>Admin Panel</b>\n\n⏳ Current expiry: <b>{get_expiry()} minutes</b>",
            parse_mode="HTML", reply_markup=admin_keyboard()
        )
    elif q.data == "admin:add":
        context.user_data["state"] = "add"
        await q.edit_message_text(
            "➕ <b>Add Anime</b>\n\nSend:\n<code>Anime Name | @channelusername</code>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="admin:home")]])
        )
    elif q.data == "admin:remove":
        context.user_data["state"] = "remove"
        await q.edit_message_text(
            "🗑️ Send the anime name to remove.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="admin:home")]])
        )
    elif q.data == "admin:poster":
        await q.edit_message_text(
            "🖼️ Reply to a poster photo with:\n<code>/setphoto Anime Name</code>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="admin:home")]])
        )
    elif q.data == "admin:list":
        await send_anime_list(q.message.chat_id, context)
    elif q.data == "admin:expiry":
        context.user_data["state"] = "expiry"
        await q.edit_message_text(
            f"⏳ Current expiry: <b>{get_expiry()} minutes</b>\n\nSend a number from 1 to 1440.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="admin:home")]])
        )


async def admin_text_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await send_anime_links(update, context)
        return
    state = context.user_data.get("state")
    if not state:
        await send_anime_links(update, context)
        return
    text = update.effective_message.text.strip()

    if state == "add":
        await add_entry(update, context, text)
    elif state == "remove":
        await update.effective_message.reply_text(
            "✅ Removed." if remove_anime(text) else "❌ Anime not found."
        )
        context.user_data.pop("state", None)
    elif state == "expiry":
        try:
            minutes = int(text)
            if not 1 <= minutes <= 1440:
                raise ValueError
            set_expiry(minutes)
            context.user_data.pop("state", None)
            await update.effective_message.reply_text(f"⏳ Expiry changed to {minutes} minutes.")
        except ValueError:
            await update.effective_message.reply_text("Enter a number from 1 to 1440.")


@app.get("/")
def health():
    return jsonify({"status": "online", "service": "anime-channel-finder", "mode": "webhook"})


@app.post("/webhook")
def webhook():
    if WEBHOOK_SECRET:
        supplied = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if supplied != WEBHOOK_SECRET:
            return jsonify({"ok": False}), 403
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"ok": True})
    update = Update.de_json(data, telegram_app.bot)
    future = asyncio.run_coroutine_threadsafe(telegram_app.process_update(update), bot_loop)
    try:
        future.result(timeout=30)
    except Exception:
        logger.exception("Failed to process Telegram webhook update")
        return jsonify({"ok": False}), 500
    return jsonify({"ok": True})


async def setup():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is missing. Add it to your service environment variables.")
    await telegram_app.initialize()
    telegram_app.add_handler(CommandHandler("start", start))
    telegram_app.add_handler(CommandHandler("admin", admin))
    telegram_app.add_handler(CommandHandler("help", help_cmd))
    telegram_app.add_handler(CommandHandler("list", list_cmd))
    telegram_app.add_handler(CommandHandler("add", add_cmd))
    telegram_app.add_handler(CommandHandler("remove", remove_cmd))
    telegram_app.add_handler(CommandHandler("setexpiry", setexpiry_cmd))
    telegram_app.add_handler(CommandHandler("setphoto", setphoto_cmd))
    telegram_app.add_handler(CallbackQueryHandler(button_handler))
    telegram_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, admin_text_router))
    await telegram_app.start()
    if not WEBHOOK_URL:
        raise RuntimeError("WEBHOOK_URL is missing. Set it to your Render public URL.")
    await telegram_app.bot.set_webhook(
        url=f"{WEBHOOK_URL}/webhook",
        secret_token=WEBHOOK_SECRET or None,
        drop_pending_updates=True
    )
    logger.info("Webhook set: %s/webhook", WEBHOOK_URL)


bot_loop = asyncio.new_event_loop()


def _run_bot_loop():
    asyncio.set_event_loop(bot_loop)
    bot_loop.run_forever()


bot_loop_thread = threading.Thread(target=_run_bot_loop, name="telegram-bot-loop", daemon=True)
bot_loop_thread.start()
asyncio.run_coroutine_threadsafe(setup(), bot_loop).result(timeout=60)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT)
