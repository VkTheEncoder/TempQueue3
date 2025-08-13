# plugins/muxer.py

from pyrogram import Client, filters
from pyrogram.enums import ParseMode
from helper_func.queue import Job, job_queue
from helper_func.mux   import softmux_vid, hardmux_vid, running_jobs
from helper_func.progress_bar import progress_bar
from helper_func.dbhelper       import Database as Db
from config import Config
import uuid, time, os, asyncio

db = Db()

# only allow configured users
async def _check_user(filt, client, message):
    return str(message.from_user.id) in Config.ALLOWED_USERS
check_user = filters.create(_check_user)


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

    # capture everything now
    final_name = db.get_filename(chat_id)
    job_id     = uuid.uuid4().hex[:8]
    status     = await client.send_message(
        chat_id,
        f"🔄 Job <code>{job_id}</code> enqueued at position {job_queue.qsize() + 1}",
        parse_mode=ParseMode.HTML
    )

    # enqueue & clear DB so user can queue again immediately
    await job_queue.put(Job(job_id, 'soft', chat_id, vid, sub, final_name, status))
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

    final_name = db.get_filename(chat_id)
    job_id     = uuid.uuid4().hex[:8]
    status     = await client.send_message(
        chat_id,
        f"🔄 Job <code>{job_id}</code> enqueued at position {job_queue.qsize() + 1}",
        parse_mode=ParseMode.HTML
    )

    await job_queue.put(Job(job_id, 'hard', chat_id, vid, sub, final_name, status))
    db.erase(chat_id)


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
    for t in entry['tasks']:
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

        # Run ffmpeg and show live progress (message passed by keyword)
        if job.mode in ('soft', 'hard'):
            func = softmux_vid if job.mode == 'soft' else hardmux_vid
            out_file = await func(job.vid, job.sub, msg=job.status_msg)
        else:
            # If you add /nosub later, import nosub_encode and handle it here.
            out_file = None

        if out_file:
            # rename to the captured final_name
            src = os.path.join(Config.DOWNLOAD_DIR, out_file)
            dst = os.path.join(Config.DOWNLOAD_DIR, job.final_name)
            os.rename(src, dst)

            # upload with progress bar (= live)
            t0 = time.time()
            if job.mode == 'soft':
                await client.send_document(
                    job.chat_id,
                    document=dst,
                    caption=job.final_name,
                    progress=progress_bar,
                    progress_args=('Uploading…', job.status_msg, t0, job.job_id)
                )
            else:
                await client.send_video(
                    job.chat_id,
                    video=dst,
                    caption=job.final_name,
                    progress=progress_bar,
                    progress_args=('Uploading…', job.status_msg, t0, job.job_id)
                )

            await job.status_msg.edit(
                f"✅ Job <code>{job.job_id}</code> done.",
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
