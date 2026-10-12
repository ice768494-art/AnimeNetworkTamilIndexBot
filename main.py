import os
import sqlite3
from datetime import datetime, timedelta, timezone

from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, filters, ConversationHandler
)
from telegram.error import TelegramError

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS = {
    int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",")
    if x.strip().isdigit()
}
DB_PATH = os.getenv("DB_PATH", "/var/data/anime.db")
DEFAULT_EXPIRY = int(os.getenv("DEFAULT_EXPIRY_MINUTES", "10"))

os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)

db = sqlite3.connect(DB_PATH, check_same_thread=False)
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
db.execute("""
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
)
""")
db.execute(
    "INSERT OR IGNORE INTO settings(key,value) VALUES('expiry_minutes',?)",
    (str(DEFAULT_EXPIRY_MINUTES if 'DEFAULT_EXPIRY_MINUTES' in globals() else DEFAULT_EXPIRY),)
)
db.commit()

WAIT_ADD, WAIT_REMOVE, WAIT_POSTER, WAIT_EXPIRY = range(4)

def is_admin(user_id):
    return user_id in ADMIN_IDS

def get_expiry():
    row = db.execute("SELECT value FROM settings WHERE key='expiry_minutes'").fetchone()
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
        "SELECT id,name,poster_file_id FROM anime WHERE lower(name)=lower(?)",
        (name.strip(),)
    ).fetchone()

def all_anime():
    return db.execute("SELECT id,name,poster_file_id FROM anime ORDER BY lower(name)").fetchall()

def channels_for(anime_id):
    return db.execute(
        "SELECT id,chat_id,title FROM channels WHERE anime_id=? ORDER BY id",
        (anime_id,)
    ).fetchall()

def add_anime(name):
    cur = db.execute("INSERT INTO anime(name) VALUES(?)", (name.strip(),))
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
    db.execute("UPDATE anime SET poster_file_id=? WHERE id=?", (file_id, anime_id))
    db.commit()

async def admin_only(update: Update):
    return is_admin(update.effective_user.id)

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
        "Send me an anime name to get its channel links.\n\n"
        "Example: <code>Black Clover</code>"
    )
    if await admin_only(update):
        text += "\n\n⚙️ <b>Admin:</b> use /admin"
    await update.message.reply_text(text, parse_mode="HTML")

async def admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_only(update):
        return
    await update.message.reply_text(
        f"⚙️ <b>Admin Panel</b>\n\n"
        f"⏳ Current expiry: <b>{get_expiry()} minutes</b>",
        parse_mode="HTML",
        reply_markup=admin_keyboard()
    )

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await admin_only(update):
        await update.message.reply_text(
            "⚙️ <b>Admin commands</b>\n\n"
            "/admin — Open panel\n"
            "/add Anime | channel — Quick add\n"
            "/remove Anime — Remove anime\n"
            "/setexpiry 10 — Set expiry minutes\n"
            "/setphoto Anime — Reply to a photo\n"
            "/list — List anime",
            parse_mode="HTML"
        )
    else:
        await start(update, context)

async def list_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_only(update):
        return
    await send_anime_list(update.effective_chat.id, context)

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
            lines.append(f"   • {ch[2]}")
    await context.bot.send_message(chat_id, "\n".join(lines), parse_mode="HTML")

async def add_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_only(update):
        return
    raw = update.message.text.partition(" ")[2].strip()
    if "|" not in raw:
        await update.message.reply_text(
            "Usage:\n<code>/add Black Clover | @YourChannel</code>",
            parse_mode="HTML"
        )
        return
    name, channel = [x.strip() for x in raw.split("|", 1)]
    row = get_anime(name)
    try:
        if not row:
            anime_id = add_anime(name)
        else:
            anime_id = row[0]
        chat = await context.bot.get_chat(channel)
        title = chat.title or channel
        existing = db.execute(
            "SELECT 1 FROM channels WHERE anime_id=? AND chat_id=?",
            (anime_id, str(chat.id))
        ).fetchone()
        if not existing:
            add_channel(anime_id, chat.id, title)
        await update.message.reply_text(
            f"✅ Added <b>{name}</b>\n📺 {title}",
            parse_mode="HTML"
        )
    except TelegramError as e:
        await update.message.reply_text(
            "❌ I couldn't access that channel.\n"
            "Make sure the bot is an admin with permission to manage invite links."
        )

async def remove_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_only(update):
        return
    name = update.message.text.partition(" ")[2].strip()
    if not name:
        await update.message.reply_text("Usage: /remove Anime Name")
        return
    await update.message.reply_text(
        "✅ Removed." if remove_anime(name) else "❌ Anime not found."
    )

async def setexpiry_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_only(update):
        return
    raw = update.message.text.partition(" ")[2].strip()
    try:
        minutes = int(raw)
        if not 1 <= minutes <= 1440:
            raise ValueError
        set_expiry(minutes)
        await update.message.reply_text(f"⏳ Expiry set to {minutes} minutes.")
    except ValueError:
        await update.message.reply_text("Use a number from 1 to 1440.")

async def setphoto_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_only(update):
        return
    name = update.message.text.partition(" ")[2].strip()
    if not name or not update.message.reply_to_message or not update.message.reply_to_message.photo:
        await update.message.reply_text(
            "Reply to a photo and use:\n/setphoto Anime Name"
        )
        return
    row = get_anime(name)
    if not row:
        await update.message.reply_text("❌ Anime not found.")
        return
    file_id = update.message.reply_to_message.photo[-1].file_id
    set_poster(row[0], file_id)
    await update.message.reply_text("🖼️ Poster saved.")

async def handle_user_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = update.message.text.strip()
    row = get_anime(name)
    if not row:
        await update.message.reply_text(
            "❌ Anime not found.\nTry another name."
        )
        return

    chs = channels_for(row[0])
    if not chs:
        await update.message.reply_text("⚠️ No channels are configured for this anime.")
        return

    expiry = get_expiry()
    expire_at = datetime.now(timezone.utc) + timedelta(minutes=expiry)
    buttons = []

    for ch in chs:
        try:
            invite = await context.bot.create_chat_invite_link(
                chat_id=int(ch[1]),
                name=f"Finder {expiry}m",
                expire_date=expire_at,
                creates_join_request=True
            )
            buttons.append([
                InlineKeyboardButton(
                    f"🔗 {ch[2]}",
                    url=invite.invite_link
                )
            ])
        except TelegramError:
            buttons.append([
                InlineKeyboardButton(
                    f"⚠️ {ch[2]} (unavailable)",
                    callback_data="noop"
                )
            ])

    caption = (
        f"🎬 <b>{row[1]}</b>\n\n"
        f"⏳ Links expire in <b>{expiry} minutes</b>\n"
        f"👇 Choose a channel:"
    )
    markup = InlineKeyboardMarkup(buttons)

    if row[2]:
        await update.message.reply_photo(
            photo=row[2],
            caption=caption,
            parse_mode="HTML",
            reply_markup=markup
        )
    else:
        await update.message.reply_text(
            caption,
            parse_mode="HTML",
            reply_markup=markup
        )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    if q.data == "noop":
        return
    if not is_admin(q.from_user.id):
        return

    if q.data == "admin:home":
        await q.edit_message_text(
            f"⚙️ <b>Admin Panel</b>\n\n⏳ Current expiry: <b>{get_expiry()} minutes</b>",
            parse_mode="HTML",
            reply_markup=admin_keyboard()
        )
    elif q.data == "admin:add":
        context.user_data["state"] = "add"
        await q.edit_message_text(
            "➕ <b>Add Anime</b>\n\n"
            "Send:\n<code>Anime Name | @channelusername</code>\n\n"
            "You can repeat this to add multiple channels to the same anime.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ Back", callback_data="admin:home")]
            ])
        )
    elif q.data == "admin:remove":
        context.user_data["state"] = "remove"
        await q.edit_message_text(
            "🗑️ Send the anime name to remove it.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ Back", callback_data="admin:home")]
            ])
        )
    elif q.data == "admin:poster":
        context.user_data["state"] = "poster"
        await q.edit_message_text(
            "🖼️ Send the anime name first.\n"
            "Then reply to a poster photo with /setphoto Anime Name.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ Back", callback_data="admin:home")]
            ])
        )
    elif q.data == "admin:list":
        await send_anime_list(q.message.chat_id, context)
    elif q.data == "admin:expiry":
        context.user_data["state"] = "expiry"
        await q.edit_message_text(
            f"⏳ Current expiry: {get_expiry()} minutes\n\n"
            "Send a number from 1 to 1440.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ Back", callback_data="admin:home")]
            ])
        )

async def admin_text_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_only(update):
        return
    state = context.user_data.get("state")
    if not state:
        await handle_user_text(update, context)
        return

    text = update.message.text.strip()

    if state == "add":
        if "|" not in text:
            await update.message.reply_text("Use: Anime Name | @channelusername")
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
            await update.message.reply_text(
                f"✅ <b>{name}</b> → {chat.title or channel} added.\n"
                "Send another entry or /admin.",
                parse_mode="HTML"
            )
        except TelegramError:
            await update.message.reply_text(
                "❌ Channel access failed. Add the bot as an admin with invite-link permission."
            )

    elif state == "remove":
        await update.message.reply_text(
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
            await update.message.reply_text(f"⏳ Expiry changed to {minutes} minutes.")
        except ValueError:
            await update.message.reply_text("Enter a number from 1 to 1440.")

    elif state == "poster":
        await update.message.reply_text(
            "Now reply to a poster photo with:\n/setphoto Anime Name"
        )
        context.user_data.pop("state", None)

def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is missing.")
    if not ADMIN_IDS:
        raise RuntimeError("ADMIN_IDS is missing.")

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("list", list_cmd))
    app.add_handler(CommandHandler("add", add_cmd))
    app.add_handler(CommandHandler("remove", remove_cmd))
    app.add_handler(CommandHandler("setexpiry", setexpiry_cmd))
    app.add_handler(CommandHandler("setphoto", setphoto_cmd))
    app.add_handler(CallbackQueryHandler(button_handler))

    # Admin text with a temporary panel state gets first chance.
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, admin_text_router)
    )

    print("Anime Channel Finder started.")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
