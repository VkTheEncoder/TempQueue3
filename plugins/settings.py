# plugins/settings.py

from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.enums import ParseMode
from helper_func.settings_manager import SettingsManager
from config import Config

# in‑memory state for who’s currently in settings
_PENDING = {}

# Option lists
RESOLUTIONS = [
    ('8K','7680:4320'),('4K','3840:2160'),
    ('1440p','2560:1440'),('1080p','1920:1080'),
    ('720p','1280:720'),('480p','854:480'),
    ('360p','640:360'),('240p','426:240'),
    ('144p','256:144'),('original','original'),
]
FPS_OPTIONS = [
    ('60 FPS','60'),('50 FPS','50'),
    ('30 FPS','30'),('25 FPS','25'),
    ('24 FPS','24'),('original','original'),
]
CODECS = [
    ('H.264','libx264'),('H.265','libx265'),
    ('VP9','libvpx-vp9'),('AV1','libaom-av1'),
]
PRESETS = [
    ('ultrafast','ultrafast'),('superfast','superfast'),
    ('veryfast','veryfast'),('faster','faster'),
    ('fast','fast'),('medium','medium'),
    ('slow','slow'),('slower','slower'),
    ('veryslow','veryslow'),
]

def _is_crf_stage(filt, client, message):
    uid = getattr(message.from_user, "id", None)
    return uid is not None and _PENDING.get(uid) == "crf"

is_crf_stage = filters.create(_is_crf_stage)

async def _check_user(filt, client, update):
    user = getattr(update, "from_user", None)
    if user is None:
        msg = getattr(update, "message", None)
        if msg:
            user = getattr(msg, "from_user", None)
    return bool(user) and (str(user.id) in Config.ALLOWED_USERS)

check_user = filters.create(_check_user)

def _keyboard(options: list, tag: str) -> InlineKeyboardMarkup:
    """Build inline keyboard rows of one button each."""
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(n, callback_data=f"{tag}*{v}")]
         for n, v in options]
    )

@Client.on_message(filters.command("settings") & filters.private)
async def start_settings(client: Client, message):
    """Entry point: choose resolution."""
    uid = message.from_user.id
    _PENDING[uid] = 'res'
    await message.reply(
        "<b>🔧 Settings</b>\nChoose your target resolution:",
        reply_markup=_keyboard(RESOLUTIONS, 'res'),
        parse_mode=ParseMode.HTML
    )

@Client.on_callback_query()
async def handle_settings_cb(client: Client, cq):
    """Handle each button press."""
    uid = cq.from_user.id
    stage = _PENDING.get(uid)
    if not stage:
        return  # not in settings flow

    action, val = cq.data.split('*', 1)
    await cq.answer()

    if action == 'res':
        SettingsManager.set(uid, 'resolution', val)
        _PENDING[uid] = 'fps'
        await cq.edit_message_text(
            "<b>Step 2/5</b>: Choose your target frame rate:",
            reply_markup=_keyboard(FPS_OPTIONS, 'fps'),
            parse_mode=ParseMode.HTML
        )

    elif action == 'fps':
        SettingsManager.set(uid, 'fps', val)
        _PENDING[uid] = 'codec'
        await cq.edit_message_text(
            "<b>Step 3/5</b>: Choose your video codec:",
            reply_markup=_keyboard(CODECS, 'codec'),
            parse_mode=ParseMode.HTML
        )

    elif action == 'codec':
        SettingsManager.set(uid, 'codec', val)
        _PENDING[uid] = 'crf'
        await cq.edit_message_text(
            "<b>Step 4/5</b>: Now send me a CRF value (0–51):",
            parse_mode=ParseMode.HTML
        )

    elif action == 'preset':
        SettingsManager.set(uid, 'preset', val)
        cfg = SettingsManager.get(uid)
        summary = (
            "<b>✅ Settings Saved!</b>\n\n"
            f"• Resolution: <code>{cfg['resolution']}</code>\n"
            f"• FPS:        <code>{cfg['fps']}</code>\n"
            f"• Codec:      <code>{cfg['codec']}</code>\n"
            f"• CRF:        <code>{cfg['crf']}</code>\n"
            f"• Preset:     <code>{cfg['preset']}</code>"
        )
        _PENDING.pop(uid, None)
        await cq.edit_message_text(summary, parse_mode=ParseMode.HTML)


@Client.on_message(
    filters.text
    & ~filters.command(["start","softmux","hardmux","nosub","cancel","settings"])
    & check_user
    & filters.private,
    group=1
)
async def handle_crf_text(client: Client, message):
    """Catch CRF numeric entry."""
    uid = message.from_user.id
    if _PENDING.get(uid) != 'crf':
        return

    txt = message.text.strip()
    if not txt.isdigit() or not (0 <= int(txt) <= 51):
        return await message.reply(
            "❌ Please enter a number between 0 and 51."
        )

    SettingsManager.set(uid, 'crf', txt)
    _PENDING[uid] = 'preset'
    await message.reply(
        "<b>Step 5/5</b>: Finally, choose your encoding preset:",
        reply_markup=_keyboard(PRESETS, 'preset'),
        parse_mode=ParseMode.HTML
    )
