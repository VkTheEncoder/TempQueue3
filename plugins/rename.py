# plugins/rename.py
import re
from pyrogram import Client, filters
from pyrogram.enums import ParseMode
from helper_func.dbhelper import Database as Db
from config import Config

db = Db()

# Chats/users currently being asked for a rename
RENAMING = set()  # stores user_id (int)

def _sanitize(basename: str) -> str:
    """
    Allow letters, numbers, spaces, . _ - ( )
    Collapse multiple spaces and trim to 60 chars.
    """
    name = re.sub(r'[^a-zA-Z0-9 _\-.()]+', '', (basename or '')).strip()
    name = re.sub(r'\s+', ' ', name)
    return name[:60]

def _allowed_user(user_id: int) -> bool:
    # ALLOWED_USERS typically stored as strings; normalize for comparison
    return str(user_id) in getattr(Config, "ALLOWED_USERS", [])

async def prompt_rename(client: Client, chat_id: int, default_stub: str):
    """
    Call this when BOTH video & subtitle are ready.
    It asks the user for a new file name (without extension) or /skip.
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
    # 1) Always let slash-commands pass through to other handlers (e.g., /settings).
    #    This is the key fix so your /settings keeps working.
    if (message.text or "").lstrip().startswith("/"):
        return

    # 2) Only process when we're actually waiting for a rename from this user.
    if not _allowed_user(uid) or uid not in RENAMING:
        return

    new_stub = _sanitize(message.text or "")
    if not new_stub:
        return await message.reply(
            "❌ Invalid name. Use letters, numbers, spaces, and ._-() only (≤ 60 chars).",
            parse_mode=ParseMode.HTML
        )

    # Save only the stub; the final extension is applied after encode/mux.
    db.set_filename(uid, new_stub)
    RENAMING.discard(uid)
    await message.reply(
        f"✅ Saved name: <code>{new_stub}</code>\nNow choose: /softmux , /hardmux , /nosub",
        parse_mode=ParseMode.HTML
    )
