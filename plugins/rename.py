# plugins/rename.py
import re
from pyrogram import Client, filters
from pyrogram.enums import ParseMode
from helper_func.dbhelper import Database as Db
from config import Config

db = Db()
# Track chats awaiting a rename reply
RENAMING = set()

def _sanitize(basename: str) -> str:
    # Keep letters/numbers/._-() and spaces, collapse spaces, trim to 60 chars
    name = re.sub(r'[^a-zA-Z0-9 _\-.()]+', '', (basename or '')).strip()
    name = re.sub(r'\s+', ' ', name)
    return name[:60]

async def prompt_rename(client: Client, chat_id: int, default_stub: str):
    """
    Ask user for a new file name (without extension). They can reply with text or /skip.
    """
    RENAMING.add(chat_id)
    await client.send_message(
        chat_id,
        (
            "✅ Video & subtitle are ready.\n\n"
            "<b>Rename?</b> Send a new file name <i>(without extension)</i>\n"
            f"• Suggestion: <code>{_sanitize(default_stub)}</code>\n"
            "• Or send <code>/skip</code> to keep the current name."
        ),
        parse_mode=ParseMode.HTML
    )

def _allowed_user(user_id: int) -> bool:
    # ALLOWED_USERS in your repo are strings; normalize to str for compare
    return str(user_id) in getattr(Config, "ALLOWED_USERS", [])

@Client.on_message(filters.command("skip") & filters.private)
async def skip_rename(client: Client, message):
    uid = message.from_user.id
    if not _allowed_user(uid):
        return
    if uid in RENAMING:
        RENAMING.discard(uid)
        await message.reply(
            "👍 Keeping the current name.\nChoose: /softmux , /hardmux , /nosub",
            parse_mode=ParseMode.HTML
        )
    else:
        await message.reply("Nothing to skip right now.", parse_mode=ParseMode.HTML)

@Client.on_message(filters.text & filters.private)
async def handle_rename_text(client: Client, message):
    uid = message.from_user.id
    if not _allowed_user(uid) or uid not in RENAMING:
        return  # ignore unrelated messages

    new_stub = _sanitize(message.text or "")
    if not new_stub:
        return await message.reply(
            "❌ Invalid name. Use letters, numbers, spaces, and ._-() only (≤ 60 chars).",
            parse_mode=ParseMode.HTML
        )

    # Save only the stub; extension will be attached according to the chosen mux mode.
    db.set_filename(uid, new_stub)
    RENAMING.discard(uid)
    await message.reply(
        f"✅ Saved name: <code>{new_stub}</code>\nNow choose: /softmux , /hardmux , /nosub",
        parse_mode=ParseMode.HTML
    )
