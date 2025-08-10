# plugins/muxer.py

from pyrogram import Client, filters
from pyrogram.enums import ParseMode
from helper_func.queue import Job, job_queue
from helper_func.mux   import softmux_vid, hardmux_vid, nosub_encode, running_jobs
from helper_func.progress_bar import progress_bar
from helper_func.dbhelper       import Database as Db
from config import Config
import uuid, time, os, asyncio

db = Db()
# Chat state: expecting a filename reply after picking a mode
_PENDING_RENAME = {}  # chat_id -> dict(mode, vid, sub, default_name, status_msg)

# only allow configured users
async def _check_user(filt, client, message):
    return str(message.from_user.id) in Config.ALLOWED_USERS
check_user = filters.create(_check_user)

async def _ask_for_name(client, chat_id, mode, vid, sub, default_name):
    status = await client.send_message(
        chat_id,
        "✍️ Send the output file name **with extension** (or type `default` to keep it):\n\n"
        f"<code>{default_name}</code>",
        parse_mode=ParseMode.HTML
    )
    _PENDING_RENAME[chat_id] = dict(
        mode=mode, vid=vid, sub=sub, default_name=default_name, status_msg=status
    )
# ------------------------------------------------------------------------------
# enqueue a soft-mux job
# ------------------------------------------------------------------------------
@Client.on_message(filters.command('softmux') & check_user & filters.private)
async def enqueue_soft(client, message):
    chat_id = message.from_user.id
    vid     = db.get_vid_filename(chat_id)
    sub     = db.get_sub_filename(chat_id)
    if not vid or not sub:
        text = ''
        if not vid: text += 'First send a Video File\n'
        if not sub: text += 'Send a Subtitle File!'
        return await client.send_message(chat_id, text, parse_mode=ParseMode.HTML)

    # enqueue & clear DB so user can queue again immediately
    default_final = db.get_filename(chat_id) or (os.path.splitext(vid)[0] + "_soft.mkv")
    await _ask_for_name(client, chat_id, 'soft', vid, sub, default_final)
    db.erase(chat_id)


# ------------------------------------------------------------------------------
# enqueue a hard-mux job
# ------------------------------------------------------------------------------
@Client.on_message(filters.command('hardmux') & check_user & filters.private)
async def enqueue_hard(client, message):
    chat_id = message.from_user.id
    vid     = db.get_vid_filename(chat_id)
    sub     = db.get_sub_filename(chat_id)
    if not vid or not sub:
        text = ''
        if not vid: text += 'First send a Video File\n'
        if not sub: text += 'Send a Subtitle File!'
        return await client.send_message(chat_id, text, parse_mode=ParseMode.HTML)

    default_final = db.get_filename(chat_id) or (os.path.splitext(vid)[0] + "_hard.mp4")
    await _ask_for_name(client, chat_id, 'hard', vid, sub, default_final)
    db.erase(chat_id)


@Client.on_message(filters.command('nosub') & check_user & filters.private)
async def enqueue_nosub(client, message):
    chat_id = message.from_user.id
    vid     = db.get_vid_filename(chat_id)
    if not vid:
        return await client.send_message(chat_id, "First send a Video File.", parse_mode=ParseMode.HTML)

    # pick a sensible default name (use whatever you stored as original save_filename if present)
    default_final = db.get_filename(chat_id) or (os.path.splitext(vid)[0] + "_nosub.mp4")
    await _ask_for_name(client, chat_id, 'nosub', vid, None, default_final)


# ------------------------------------------------------------------------------
# cancel a single job (pending or running)
# ------------------------------------------------------------------------------
@Client.on_message(filters.command('cancel') & check_user & filters.private)
async def cancel_job(client, message):
    if len(message.command) != 2:
        return await message.reply_text(
            "Usage: /cancel <job_id>", parse_mode=ParseMode.HTML
        )
    target = message.command[1]

    # try removing from pending queue first
    removed = False
    temp_q  = asyncio.Queue()
    while not job_queue.empty():
        job = await job_queue.get()
        if job.job_id == target:
            removed = True
            await job.status_msg.edit(
                f"❌ Job <code>{target}</code> cancelled before start.",
                parse_mode=ParseMode.HTML
            )
        else:
            await temp_q.put(job)
        job_queue.task_done()
    # restore the rest
    while not temp_q.empty():
        await job_queue.put(await temp_q.get())

    if removed:
        return

    # otherwise, if it's already running, kill ffmpeg
    entry = running_jobs.get(target)
    if not entry:
        return await message.reply_text(
            f"No job `<code>{target}</code>` found.", parse_mode=ParseMode.HTML
        )

    entry['proc'].kill()
    for t in entry.get('tasks', []):
        t.cancel()
    running_jobs.pop(target, None)

    await message.reply_text(
        f"🛑 Job `<code>{target}</code>` aborted.", parse_mode=ParseMode.HTML
    )


# ------------------------------------------------------------------------------
# worker: processes exactly one job at a time
# ------------------------------------------------------------------------------
async def queue_worker(client: Client):
    while True:
        job = await job_queue.get()

        await job.status_msg.edit(
            f"▶️ Starting <code>{job.job_id}</code> ({job.mode}-mux)…",
            parse_mode=ParseMode.HTML
        )

        # run ffmpeg (this will itself show live progress including job_id)
        out_file = await (
            softmux_vid if job.mode == 'soft'
            else hardmux_vid if job.mode == 'hard'
            else nosub_encode
        )(job.vid, job.status_msg)

        if out_file:
            # rename to the captured final_name
            src = os.path.join(Config.DOWNLOAD_DIR, out_file)
            dst = os.path.join(Config.DOWNLOAD_DIR, job.final_name)
            os.rename(src, dst)

            # upload with progress bar (= live)
            await client.send_document(
                job.chat_id,
                document=dst,
                caption=job.final_name,
                progress=progress_bar,
                progress_args=('Uploading…', job.status_msg, t0, job.job_id)
            )
    

            await job.status_msg.edit(
                f"✅ Job <code>{job.job_id}</code> done in {round(time.time() - t0)}s",
                parse_mode=ParseMode.HTML
            )

            # cleanup disk
            for fn in (job.vid, job.sub, job.final_name):
                try:
                    os.remove(os.path.join(Config.DOWNLOAD_DIR, fn))
                except:
                    pass

        # signal done, move to next
        job_queue.task_done()


# ------------------------------------------------------------------------------
# capture rename replies
# ------------------------------------------------------------------------------
@Client.on_message(filters.text & check_user & filters.private)
async def maybe_capture_name(client, message):
    chat_id = message.from_user.id
    if chat_id not in _PENDING_RENAME:
        return  # ignore unrelated messages

    pending = _PENDING_RENAME.pop(chat_id)
    user_text = (message.text or '').strip()

    final_name = pending['default_name']
    if user_text and user_text.lower() not in ('default', '/skip'):
        # ensure extension exists
        root, ext = os.path.splitext(user_text)
        if not ext:
            # inherit ext from default
            _, ext0 = os.path.splitext(final_name)
            final_name = user_text + ext0
        else:
            final_name = user_text

    # Create job
    job_id = uuid.uuid4().hex[:8]
    status = await client.send_message(
        chat_id,
        f"🔄 Job <code>{job_id}</code> enqueued at position {job_queue.qsize() + 1}",
        parse_mode=ParseMode.HTML
    )

    await job_queue.put(Job(
        job_id,
        pending['mode'],
        chat_id,
        pending['vid'],
        pending['sub'],
        final_name,
        status
    ))
    db.erase(chat_id)
