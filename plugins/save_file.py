import logging
logging.basicConfig(level=logging.DEBUG,
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

logger = logging.getLogger(__name__)
logging.getLogger("pyrogram").setLevel(logging.WARNING)

import os
import time
from chat import Chat
from config import Config
from pyrogram import Client, filters
from helper_func.progress_bar import progress_bar
from helper_func.dbhelper import Database as Db
from plugins.rename import prompt_rename  # NEW
import re
import requests
from urllib.parse import quote, unquote

db = Db()

async def _check_user(filt, c, m):
    chat_id = str(m.from_user.id)
    return chat_id in Config.ALLOWED_USERS

check_user = filters.create(_check_user)

def _base_stub(name: str) -> str:
    return os.path.splitext(name)[0][:60] if name else "output"

@Client.on_message(filters.document & check_user & filters.private)
async def save_doc(client, message):
    chat_id = message.from_user.id
    start_time = time.time()
    downloading = await client.send_message(chat_id, 'Downloading your File!')
    download_location = await client.download_media(
        message=message,
        file_name=Config.DOWNLOAD_DIR+'/',
        progress=progress_bar,
        progress_args=('Initializing', downloading, start_time)
    )

    if download_location is None:
        return client.edit_message_text(
            text='Downloading Failed!',
            chat_id=chat_id,
            message_id=downloading.id
        )

    await client.edit_message_text(
        text=Chat.DOWNLOAD_SUCCESS.format(round(time.time()-start_time)),
        chat_id=chat_id,
        message_id=downloading.id
    )

    tg_filename = os.path.basename(download_location)
    try:
        og_filename = message.document.filename
    except:
        og_filename = False

    save_filename = og_filename if og_filename else tg_filename
    ext = save_filename.split('.').pop().lower()
    filename = f"{round(start_time)}.{ext}"

    # Move into our deterministic name
    os.rename(os.path.join(Config.DOWNLOAD_DIR, tg_filename),
              os.path.join(Config.DOWNLOAD_DIR, filename))

    if ext in ['srt','ass']:
        db.put_sub(chat_id, filename)
        if db.check_video(chat_id):
            # both ready → ask rename
            # build suggestion stub from original video display name (if any)
            vid_display = db.get_filename(chat_id) or db.get_vid_filename(chat_id) or save_filename
            await prompt_rename(client, chat_id, _base_stub(vid_display))
        else:
            await client.edit_message_text(
                text='✅ Subtitle file saved.\nNow send Video File!',
                chat_id=chat_id,
                message_id=downloading.id
            )

    elif ext in ['mp4','mkv']:
        db.put_video(chat_id, filename, save_filename)
        if db.check_sub(chat_id):
            # both ready → ask rename (use nicer display name we just saved)
            await prompt_rename(client, chat_id, _base_stub(save_filename))
        else:
            await client.edit_message_text(
                text='✅ Video file saved.\nNow send Subtitle File (srt/ass)!',
                chat_id=chat_id,
                message_id=downloading.id
            )
    else:
        text = Chat.UNSUPPORTED_FORMAT.format(ext)+f'\nFile = {tg_filename}'
        await client.edit_message_text(
            text=text,
            chat_id=chat_id,
            message_id=downloading.id
        )
        try:
            os.remove(os.path.join(Config.DOWNLOAD_DIR, tg_filename))
        except:
            pass


@Client.on_message(filters.video & check_user & filters.private)
async def save_video(client, message):
    chat_id = message.from_user.id
    start_time = time.time()
    downloading = await client.send_message(chat_id, 'Downloading your File!')
    download_location = await client.download_media(
        message=message,
        file_name=Config.DOWNLOAD_DIR+'/',
        progress=progress_bar,
        progress_args=('Initializing', downloading, start_time)
    )

    if download_location is None:
        return client.edit_message_text(
            text='Downloading Failed!',
            chat_id=chat_id,
            message_id=downloading.id
        )

    await client.edit_message_text(
        text=Chat.DOWNLOAD_SUCCESS.format(round(time.time()-start_time)),
        chat_id=chat_id,
        message_id=downloading.id
    )

    tg_filename = os.path.basename(download_location)
    try:
        og_filename = message.document.filename
    except:
        og_filename = False

    save_filename = og_filename if og_filename else tg_filename
    ext = save_filename.split('.').pop().lower()
    filename = f"{round(start_time)}.{ext}"

    os.rename(os.path.join(Config.DOWNLOAD_DIR, tg_filename),
              os.path.join(Config.DOWNLOAD_DIR, filename))

    db.put_video(chat_id, filename, save_filename)
    if db.check_sub(chat_id):
        await prompt_rename(client, chat_id, _base_stub(save_filename))
    else:
        await client.edit_message_text(
            text='✅ Video file saved.\nNow send Subtitle File (srt/ass)!',
            chat_id=chat_id,
            message_id=downloading.id
        )


@Client.on_message(filters.text & filters.regex('^http') & check_user)
async def save_url(client, message):
    chat_id = message.from_user.id
    save_filename = None

    if "|" in message.text and len(message.text.split('|')) == 2:
        save_filename = message.text.split('|')[1].strip()
        url = message.text.split('|')[0].strip()
    else:
        url = message.text.strip()

    if save_filename and len(save_filename) > 60:
        return await client.send_message(chat_id, Chat.LONG_CUS_FILENAME)

    r = requests.get(url, stream=True, allow_redirects=True)
    if save_filename is None:
        if 'content-disposition' in r.headers:
            m = re.search(r'filename="(.*?)"', str(r.headers))
            if m:
                save_filename = m.group(1)
            else:
                if '?' in url:
                    url = ''.join(url.split('?')[0:-1])
                save_filename = unquote(url.split('/')[-1])
        else:
            if '?' in url:
                url = ''.join(url.split('?')[0:-1])
            save_filename = unquote(url.split('/')[-1])

    sent_msg = await client.send_message(chat_id, 'Preparing Your Download')
    ext = save_filename.split('.')[-1].lower()
    if ext not in ['mp4', 'mkv']:
        return await sent_msg.edit(Chat.UNSUPPORTED_FORMAT.format(ext))

    size = int(r.headers.get('content-length', 0))
    if not size:
        return await sent_msg.edit(Chat.FILE_SIZE_ERROR)
    if size > (2 * 1000 * 1000 * 1000):
        return await sent_msg.edit(Chat.MAX_FILE_SIZE)

    os.makedirs(Config.DOWNLOAD_DIR, exist_ok=True)

    current = 0
    start = time.time()
    filename = f"{round(start)}.{ext}"

    with requests.get(url, stream=True, allow_redirects=True) as r2:
        with open(os.path.join(Config.DOWNLOAD_DIR, filename), 'wb') as f:
            for chunk in r2.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    written = f.write(chunk)
                    current += written
                    await progress_bar(current, size, 'Downloading Your File!', sent_msg, start)

    try:
        await sent_msg.edit(Chat.DOWNLOAD_SUCCESS.format(round(time.time() - start)))
    except:
        pass

    # Save the video record
    db.put_video(chat_id, filename, save_filename)

    # If subtitle already exists, prompt rename; else ask for subtitle
    if db.check_sub(chat_id):
        # Lazy import to avoid circulars
        from plugins.rename import prompt_rename
        base_stub = os.path.splitext(save_filename)[0][:60]
        await prompt_rename(client, chat_id, base_stub)
    else:
        try:
            await sent_msg.edit("✅ Video saved. Now send the subtitle file (srt/ass).")
        except:
            pass
