import os
import sqlite3
import logging
import threading
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
db.execute("PRAGMA foreign_keys=ON")
db.execute(""" CREATE TABLE IF NOT EXISTS anime ( id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE NOT NULL, poster_file_id TEXT ) """)
db.execute(""" CREATE TABLE IF NOT EXISTS channels ( id INTEGER PRIMARY KEY AUTOINCREMENT, anime_id INTEGER NOT NULL, chat_id TEXT NOT NULL, title TEXT NOT NULL, FOREIGN KEY(anime_id) REFERENCES anime(id) ON DELETE CASCADE ) """)
db.execute(""" CREATE TABLE IF NOT EXISTS settings ( key TEXT PRIMARY KEY, value TEXT NOT NULL ) """)
db.execute(
    "INSERT OR IGNORE INTO settings(key,value) VALUES('expiry_minutes',?)",
    (str(DEFAULT_EXPIRY),)
)
db.commit()

app = Flask(__name__)
telegram_app = Application.builder().token(BOT_TOKEN).build()


def is_admin(user_id):
    return user_id in ADMIN_IDS


def get_expiry():
    row = db.execute(
        "SELECT value FROM settings WHERE key='expiry_minutes'"
    ).fetchone()
    return int(row[0]) if row else DEFAULT_EXPIRY


def set_expiry(minutes):
    db.execute(
        "INSERT INTO settings(key,value) VALUES('expiry_minutes',?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (str(minutes),)
    )
    db.commit()


def get_anime(name):
    return db.execute(
        "SELECT id,name,poster_file_id FROM anime "
        "WHERE lower(name)=lower(?)",
        (name.strip(),)
    ).fetchone()


def all_anime():
    return db.execute(
        "SELECT id,name,poster_file_id FROM anime ORDER BY lower(name)"
    ).fetchall()


def channels_for(anime_id):
    return db.execute(
        "SELECT id,chat_id,title FROM channels "
        "WHERE anime_id=? ORDER BY id",
        (anime_id,)
    ).fetchall()


def add_anime(name):
    cur = db.execute(
        "INSERT INTO anime(name) VALUES(?)",
        (name.strip(),)
    )
    db.commit()
    return cur.lastrowid


def remove_anime(name):
    row = get_anime(name)
    if not row:
        return False
    db.execute("DELETE FROM channels WHERE anime_id=?", (row[0],))
    db.execute("DELETE FROM anime WHERE id=?", (row[0],))
    db.commit()
    return True


def add_channel(anime_id, chat_id, title):
    db.execute(
        "INSERT INTO channels(anime_id,chat_id,title) VALUES(?,?,?)",
        (anime_id, str(chat_id), title)
    )
    db.commit()


def set_poster(anime_id, file_id):
    db.execute(
        "UPDATE anime SET poster_file_id=? WHERE id=?",
        (file_id, anime_id)
    )
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
        [
            InlineKeyboardButton("⏳ Expiry", callback_data="admin:expiry"),
        ],
    ])


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "🎬 <b>Anime Channel Finder</b>\n\n"
        "Black Clover ✅\n"
        "Black Clover Tamil ❌"
    )

    await update.message.reply_text(
        text,
        parse_mode="HTML"
    )
await update.message.reply_text(
    message,
    parse_mode="HTML"
)
    )
    if is_admin(update.effective_user.id):
        text += "\n\n⚙️ Admin: use /admin"
    await update.effective_message.reply_text(text, parse_mode="HTML")


async def admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.effective_message.reply_text("❌ Admin only.")
        return

    await update.effective_message.reply_text(
        f"⚙️ <b>Admin Panel</b>\n\n"
        f"⏳ Current expiry: <b>{get_expiry()} minutes</b>",
        parse_mode="HTML",
        reply_markup=admin_keyboard()
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if is_admin(update.effective_user.id):
        await update.effective_message.reply_text(
            "⚙️ <b>Admin commands</b>\n\n"
            "/admin — Open panel\n"
            "/add Anime | @channel — Quick add\n"
            "/remove Anime — Remove anime\n"
            "/setexpiry 10 — Set expiry\n"
            "/setphoto Anime — Reply to a photo\n"
            "/list — List anime\n\n"
            "👤 Normal users only need to send an anime name.",
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

    await context.bot.send_message(
        chat_id, "\n".join(lines), parse_mode="HTML"
    )


async def list_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    await send_anime_list(update.effective_chat.id, context)


async def add_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return

    raw = update.effective_message.text.partition(" ")[2].strip()

    if "|" not in raw:
        await update.effective_message.reply_text(
            "Usage:\n<code>/add Black Clover | @YourChannel</code>",
            parse_mode="HTML"
        )
        return

    name, channel = [x.strip() for x in raw.split("|", 1)]

    try:
        row = get_anime(name)
        anime_id = row[0] if row else add_anime(name)

        chat = await context.bot.get_chat(channel)
        title = chat.title or channel

        exists = db.execute(
            "SELECT 1 FROM channels WHERE anime_id=? AND chat_id=?",
            (anime_id, str(chat.id))
        ).fetchone()

        if not exists:
            add_channel(anime_id, chat.id, title)

        await update.effective_message.reply_text(
            f"✅ Added <b>{name}</b>\n📺 {title}\n\n"
            "👤 All users can now request this anime.",
            parse_mode="HTML"
        )

    except TelegramError as e:
        logger.exception("Could not access channel %r", channel)
        await update.effective_message.reply_text(
            "❌ Telegram could not access this channel.\n\n"
            f"Channel entered: <code>{channel}</code>\n"
            f"Telegram error: <code>{str(e)[:700]}</code>\n\n"
            "Use the public @username (not a t.me invite URL), and confirm "
            "this bot is an administrator in that exact channel.",
            parse_mode="HTML"
        )


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
        await update.effective_message.reply_text(
            f"⏳ Expiry set to {minutes} minutes."
        )

    except ValueError:
        await update.effective_message.reply_text(
            "Use a number from 1 to 1440."
        )


async def setphoto_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return

    name = update.effective_message.text.partition(" ")[2].strip()
    reply = update.effective_message.reply_to_message

    if not name or not reply or not reply.photo:
        await update.effective_message.reply_text(
            "Reply to a photo and use:\n/setphoto Anime Name"
        )
        return

    row = get_anime(name)

    if not row:
        await update.effective_message.reply_text(
            "❌ Anime not found."
        )
        return

    set_poster(row[0], reply.photo[-1].file_id)

    await update.effective_message.reply_text(
        "🖼️ Poster saved."
    )


async def send_anime_links(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """USER FLOW — deliberately NOT admin-only."""
    name = update.effective_message.text.strip()
    row = get_anime(name)

    if not row:
        await update.effective_message.reply_text(
            "❌ Anime not found.\n"
            "Try another name.\n"
            "Report @admin."
        )
        return

    chs = channels_for(row[0])

    if not chs:
        await update.effective_message.reply_text(
            "⚠️ No channels are configured for this anime."
        )
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

            buttons.append([
                InlineKeyboardButton(
                    f"🔗 {ch[2]}",
                    url=invite.invite_link
                )
            ])

        except TelegramError as e:
            logger.warning(
                "Invite creation failed for %s (%s): %s",
                ch[2], ch[1], e
            )

    if not buttons:
        await update.effective_message.reply_text(
            "❌ I found the anime, but I couldn't create the channel link.\n\n"
            "The bot must be an administrator in the channel with "
            "permission to manage invite links."
        )
        return

    caption = (
        f"🎬 <b>{row[1]}</b>\n\n"
        f"⏳ Link expires in <b>{expiry} minutes</b>\n"
        f"👇 Join Channel :"
    )

    markup = InlineKeyboardMarkup(buttons)

    if row[2]:
        try:
            await update.effective_message.reply_photo(
                photo=row[2],
                caption=caption,
                parse_mode="HTML",
                reply_markup=markup
            )
            return
        except TelegramError as e:
            logger.warning("Poster send failed: %s", e)

    await update.effective_message.reply_text(
        caption,
        parse_mode="HTML",
        reply_markup=markup
    )


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    if not is_admin(q.from_user.id):
        return

    if q.data == "admin:home":
        await q.edit_message_text(
            f"⚙️ <b>Admin Panel</b>\n\n"
            f"⏳ Current expiry: <b>{get_expiry()} minutes</b>",
            parse_mode="HTML",
            reply_markup=admin_keyboard()
        )

    elif q.data == "admin:add":
        context.user_data["state"] = "add"
        await q.edit_message_text(
            "➕ <b>Add Anime</b>\n\n"
            "Send:\n<code>Anime Name | @channelusername</code>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ Back", callback_data="admin:home")]
            ])
        )

    elif q.data == "admin:remove":
        context.user_data["state"] = "remove"
        await q.edit_message_text(
            "🗑️ Send the anime name to remove.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ Back", callback_data="admin:home")]
            ])
        )

    elif q.data == "admin:poster":
        await q.edit_message_text(
            "🖼️ Reply to a poster photo with:\n"
            "<code>/setphoto Anime Name</code>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ Back", callback_data="admin:home")]
            ])
        )

    elif q.data == "admin:list":
        await send_anime_list(q.message.chat_id, context)

    elif q.data == "admin:expiry":
        context.user_data["state"] = "expiry"
        await q.edit_message_text(
            f"⏳ Current expiry: <b>{get_expiry()} minutes</b>\n\n"
            "Send a number from 1 to 1440.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ Back", callback_data="admin:home")]
            ])
        )


async def admin_text_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin panel state handler. Normal users bypass this completely."""
    if not is_admin(update.effective_user.id):
        await send_anime_links(update, context)
        return

    state = context.user_data.get("state")

    if not state:
        await send_anime_links(update, context)
        return

    text = update.effective_message.text.strip()

    if state == "add":
        if "|" not in text:
            await update.effective_message.reply_text(
                "Use: Anime Name | @channelusername"
            )
            return

        name, channel = [x.strip() for x in text.split("|", 1)]

        try:
            row = get_anime(name)
            anime_id = row[0] if row else add_anime(name)

            chat = await context.bot.get_chat(channel)

            exists = db.execute(
                "SELECT 1 FROM channels WHERE anime_id=? AND chat_id=?",
                (anime_id, str(chat.id))
            ).fetchone()

            if not exists:
                add_channel(anime_id, chat.id, chat.title or channel)

            await update.effective_message.reply_text(
                f"✅ <b>{name}</b> → {chat.title or channel} added.\n\n"
                "Send another entry or /admin.",
                parse_mode="HTML"
            )

        except TelegramError as e:
            logger.exception("Add channel failed for %r", channel)
            await update.effective_message.reply_text(
                "❌ Telegram could not access this channel.\n\n"
                f"Channel entered: <code>{channel}</code>\n"
                f"Telegram error: <code>{str(e)[:700]}</code>\n\n"
                "Use the public @username (not a t.me invite URL), and confirm "
                "this bot is an administrator in that exact channel.",
                parse_mode="HTML"
            )

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

            await update.effective_message.reply_text(
                f"⏳ Expiry changed to {minutes} minutes."
            )

        except ValueError:
            await update.effective_message.reply_text(
                "Enter a number from 1 to 1440."
            )


# =========================
# WEB SERVICE / WEBHOOK
# =========================

@app.get("/")
def health():
    return jsonify({
        "status": "online",
        "service": "anime-channel-finder",
        "mode": "webhook"
    })


@app.post("/webhook")
def webhook():
    if WEBHOOK_SECRET:
        supplied = request.headers.get(
            "X-Telegram-Bot-Api-Secret-Token", ""
        )
        if supplied != WEBHOOK_SECRET:
            return jsonify({"ok": False}), 403

    data = request.get_json(silent=True)
    if not data:
        return jsonify({"ok": True})

    update = Update.de_json(data, telegram_app.bot)

    # Keep Telegram Bot API work on the same long-lived event loop.
    # Flask's per-request async loop would otherwise close and cause:
    # RuntimeError("Event loop is closed").
    future = asyncio.run_coroutine_threadsafe(
        telegram_app.process_update(update), bot_loop
    )
    try:
        future.result(timeout=30)
    except Exception:
        logger.exception("Failed to process Telegram webhook update")
        return jsonify({"ok": False}), 500

    return jsonify({"ok": True})


async def setup():
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

    telegram_app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            admin_text_router
        )
    )

    await telegram_app.start()

    if not WEBHOOK_URL:
        raise RuntimeError(
            "WEBHOOK_URL is missing. Set it to your Render public URL."
        )

    webhook_url = f"{WEBHOOK_URL}/webhook"

    await telegram_app.bot.set_webhook(
        url=webhook_url,
        secret_token=WEBHOOK_SECRET or None,
        drop_pending_updates=True
    )

    logger.info("Webhook set: %s", webhook_url)


# Run python-telegram-bot on one persistent event loop. Do not use
# asyncio.run(setup()) here: it closes the loop when setup finishes,
# breaking Bot API calls made later by webhook handlers.
import asyncio

bot_loop = asyncio.new_event_loop()

def _run_bot_loop():
    asyncio.set_event_loop(bot_loop)
    bot_loop.run_forever()

bot_loop_thread = threading.Thread(
    target=_run_bot_loop,
    name="telegram-bot-loop",
    daemon=True,
)
bot_loop_thread.start()

asyncio.run_coroutine_threadsafe(setup(), bot_loop).result(timeout=60)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT)
