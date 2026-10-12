# Anime Channel Finder — Render Web Service

This version uses a Render **Web Service + Telegram webhook**.

## User flow

User sends:
`Black Clover`

Bot:
1. Finds the anime.
2. Finds its configured channel(s).
3. Creates a Telegram join-request invite link.
4. Sets the link to expire after the configured number of minutes.
5. Sends the poster and an `🔗 Open Channel` button.

Anime searching/link generation is NOT admin-only.

## Admin

`/admin`
`/add Black Clover | @YourChannel`
`/remove Black Clover`
`/setexpiry 10`
`/setphoto Black Clover` (reply to a photo)
`/list`

## Render variables

BOT_TOKEN = Telegram bot token
ADMIN_IDS = comma-separated Telegram user IDs
WEBHOOK_URL = your Render public URL, e.g. https://anime-channel-finder.onrender.com
WEBHOOK_SECRET = any random secret string
DEFAULT_EXPIRY_MINUTES = 10
DB_PATH = /var/data/anime.db

## Important

The bot must be an administrator in every target channel and must have permission to manage invite links.

The Render disk in render.yaml is used for SQLite persistence. If your Render plan does not support persistent disks, the anime list can disappear after a restart/redeploy.

After deployment, opening `/` should show a JSON health response.

Do not run this version as a Background Worker. It is specifically configured as a Web Service.
