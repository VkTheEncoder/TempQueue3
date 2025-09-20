# plugins/rename.py
from pyrogram import Client, filters
from pyrogram.enums import ParseMode
from helper_func.dbhelper import Database as Db
from config import Config
import re

db = Db()
# Track which users we’re waiting on to send the rename
RENAMING = set()

def _sanitize(basename: str) -> str:
    # keep alnum _ - space .() and collapse spaces
    name = re.sub(r'[^a-zA-Z0-9 _\-.()]+', '', basename).strip()
    name = re.sub(r'\s+', ' ', name)
    return name[:60]  # enforce 60 char limit like your Chat.LONG_CUS_FILENAME

async def prompt_rename(client: Client, chat_id: int, default_stub: str):
    RENAMING.add(chat_id)
    await client.send_message(
        chat_id,
        (
            "✅ Video & subtitle ready.\n\n"
            "<b>Rename?</b> Send a new file name <i>(without extension)</i>\n"
            "• Example: <code>{}</code>\n"
            "• Or send <code>/skip</code> to keep original."
        ).format(default_stub),
        parse_mode=ParseMode.HTML
    )

@Client.on_message(filters.command("skip") & filters.user(list(map(int, Config.ALLOWED_USERS))))
async def skip_rename(client, message):
    uid = message.from_user.id
    if uid in RENAMING:
        RENAMING.discard(uid)
        await message.reply("👍 Keeping original name.\nChoose: /softmux , /hardmux , /nosub")
    else:
        await message.reply("Nothing to skip right now.")

@Client.on_message(filters.text & filters.user(list(map(int, Config.ALLOWED_USERS))))
async def handle_rename_text(client, message):
    uid = message.from_user.id
    if uid not in RENAMING:
        return  # ignore random text if we aren’t asking for rename

    name = _sanitize(message.text or "")
    if not name:
        return await message.reply("❌ Invalid name. Use letters/numbers/._-() and keep it short (≤60).")

    # persist
    # add this helper in dbhelper.py if not present:
    #   def set_filename(self, user_id, name): UPDATE muxbot SET filename=? WHERE user_id=?
    db.set_filename(uid, name)
    RENAMING.discard(uid)
    await message.reply(
        f"✅ Saved name: <code>{name}</code>\nNow choose: /softmux , /hardmux , /nosub",
        parse_mode=ParseMode.HTML
    )
