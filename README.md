# 🎬 Anime Channel Finder V2

Telegram bot that finds an anime and creates temporary Telegram join-request invite links.

## Features

- Inline admin panel
- SQLite database — no MongoDB
- Multiple channels for one anime
- Temporary join-request invite links
- Configurable expiry from 1–1440 minutes
- Optional anime posters
- Render worker compatible
- User simply sends the anime name

## 1. Create the bot

Use @BotFather and copy the bot token.

## 2. Find your Telegram user ID

Use a Telegram ID bot or another trusted method. Put the numeric ID in `ADMIN_IDS`.

Example:

ADMIN_IDS=123456789

For multiple admins:

ADMIN_IDS=123456789,987654321

## 3. Add the bot to your target channels

For every target channel:

1. Add the bot as an administrator.
2. Give it permission to manage invite links.
3. The bot must be able to create invite links.

Private channels are recommended for join-request links.

## 4. Deploy on Render

Create a new Background Worker from this repository.

Environment variables:

BOT_TOKEN=your_bot_token
ADMIN_IDS=your_numeric_id

The included render.yaml configures the worker and SQLite disk.

## 5. Use the admin panel

Send:

/admin

Then use:

➕ Add Anime
🗑️ Remove
🖼️ Set Poster
📋 Anime List
⏳ Expiry

### Add an anime

Send:

Black Clover | @YourChannel

To attach another channel to the same anime, send another entry:

Black Clover | @YourTamilChannel

The user will then see separate buttons for the configured channels.

### Set a poster

Reply to a Telegram photo with:

/setphoto Black Clover

### Set expiry

From the panel choose Expiry, or use:

/setexpiry 10

## User flow

User sends:

Black Clover

The bot returns the saved poster (if configured), the anime name, and one temporary join-request button per channel.

Each generated invite expires after the configured number of minutes.

## Important

Telegram controls the actual invite expiration. The bot creates a new temporary invite each time a user searches for an anime.
