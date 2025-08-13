import os, time, re, uuid, asyncio
from config import Config
from helper_func.settings_manager import SettingsManager
from pyrogram.enums import ParseMode

# job_id -> {'proc': Popen, 'tasks': [reader, waiter]}
running_jobs: dict[str, dict] = {}

# Parse both classic ffmpeg stats AND -progress key/value output
progress_pattern = re.compile(
    r'(frame|fps|size|time|bitrate|speed|total_size|out_time_ms|progress)\s*=\s*(\S+)'
)

def parse_progress(line: str):
    items = {k: v for k, v in progress_pattern.findall(line)}
    return items or None


async def readlines(stream):
    """Yield complete lines from an asyncio stream (handles CR/LF splits)."""
    pattern = re.compile(br'[\r\n]+')
    data = bytearray()
    while not stream.at_eof():
        parts = pattern.split(data)
        data[:] = parts.pop(-1)
        for line in parts:
            yield line
        data.extend(await stream.read(1024))


async def read_stderr(start: float, msg, proc, job_id: str):
    """Tail ffmpeg stderr and periodically edit the Telegram message."""
    async for raw in readlines(proc.stderr):
        line = raw.decode(errors='ignore')
        prog = parse_progress(line)
        if not prog:
            continue

        # Prefer new -progress fields when present
        # Time
        time_str = prog.get('time')
        if not time_str and 'out_time_ms' in prog:
            try:
                # ffmpeg out_time_ms is in microseconds
                us = int(prog['out_time_ms'])
                sec = us // 1_000_000
                h = sec // 3600
                m = (sec % 3600) // 60
                s = sec % 60
                time_str = f"{h:02d}:{m:02d}:{s:02d}.00"
            except Exception:
                time_str = 'N/A'

        # Size
        size_str = prog.get('size') or prog.get('total_size', 'N/A')

        # Speed
        speed_str = prog.get('speed', 'N/A')

        elapsed = time.time() - start
        if round(elapsed) % 3 == 0:  # snappier updates
            text = (
                f"🔄 <b>Progress</b> [<code>{job_id}</code>]\n"
                f"• Size   : {size_str}\n"
                f"• Time   : {time_str}\n"
                f"• Speed  : {speed_str}"
            )
            try:
                await msg.edit(text, parse_mode=ParseMode.HTML)
            except:
                pass


async def softmux_vid(vid_filename: str, sub_filename: str, msg):
    start    = time.time()
    vid_path = os.path.join(Config.DOWNLOAD_DIR, vid_filename)
    sub_path = os.path.join(Config.DOWNLOAD_DIR, sub_filename)
    base     = os.path.splitext(vid_filename)[0]
    output   = f"{base}_soft.mkv"
    out_path = os.path.join(Config.DOWNLOAD_DIR, output)
    sub_ext  = os.path.splitext(sub_filename)[1].lstrip('.')

    # Force consistent progress output
    proc = await asyncio.create_subprocess_exec(
        'ffmpeg', '-hide_banner',
        '-progress', 'pipe:2', '-nostats',
        '-i', vid_path, '-i', sub_path,
        '-map', '1:0', '-map', '0',
        '-disposition:s:0', 'default',
        '-c:v', 'copy', '-c:a', 'copy',
        '-c:s', sub_ext,
        '-y', out_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )

    job_id = uuid.uuid4().hex[:8]
    reader = asyncio.create_task(read_stderr(start, msg, proc, job_id))
    waiter = asyncio.create_task(proc.wait())
    running_jobs[job_id] = {'proc': proc, 'tasks': [reader, waiter]}

    await msg.edit(
        f"🔄 Soft-Mux job started: <code>{job_id}</code>\n"
        f"Send <code>/cancel {job_id}</code> to abort",
        parse_mode=ParseMode.HTML
    )

    await asyncio.wait([reader, waiter])
    running_jobs.pop(job_id, None)

    if proc.returncode == 0:
        await msg.edit(
            f"✅ Soft-Mux `<code>{job_id}</code>` completed in {round(time.time()-start)}s",
            parse_mode=ParseMode.HTML
        )
        await asyncio.sleep(2)
        return output
    else:
        err = await proc.stderr.read()
        await msg.edit(
            "❌ Error during soft-mux!\n\n"
            f"<pre>{err.decode(errors='ignore')}</pre>",
            parse_mode=ParseMode.HTML
        )
        return False


async def hardmux_vid(vid_filename: str, sub_filename: str, msg):
    start    = time.time()
    cfg      = SettingsManager.get(msg.chat.id)

    # Pull user prefs (no Config.CODEC/CRF/PRESET anymore)
    res    = cfg.get('resolution','1920:1080')
    fps    = cfg.get('fps','original')
    codec  = cfg.get('codec','libx264')
    crf    = cfg.get('crf','27')
    preset = cfg.get('preset','faster')

    vid_path = os.path.join(Config.DOWNLOAD_DIR, vid_filename)
    sub_path = os.path.join(Config.DOWNLOAD_DIR, sub_filename)

    # Build filtergraph
    vf = [f"subtitles={sub_path}:fontsdir={Config.FONTS_DIR}"]
    if res != 'original':
        vf.append(f"scale={res}")
    if fps != 'original':
        vf.append(f"fps={fps}")
    vf_arg = ",".join(vf)

    base     = os.path.splitext(vid_filename)[0]
    output   = f"{base}_hard.mp4"
    out_path = os.path.join(Config.DOWNLOAD_DIR, output)

    # Force consistent progress output
    proc = await asyncio.create_subprocess_exec(
        'ffmpeg','-hide_banner',
        '-progress', 'pipe:2', '-nostats',
        '-i', vid_path,
        '-vf', vf_arg,
        '-c:v', codec,
        '-preset', preset,
        '-crf', crf,
        '-map','0:v:0','-map','0:a:0?',
        '-c:a','copy',
        '-y', out_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )

    job_id = uuid.uuid4().hex[:8]
    reader = asyncio.create_task(read_stderr(start, msg, proc, job_id))
    waiter = asyncio.create_task(proc.wait())
    running_jobs[job_id] = {'proc': proc, 'tasks': [reader, waiter]}

    await msg.edit(
        f"🔄 Hard-Mux job started: <code>{job_id}</code>\n"
        f"Send <code>/cancel {job_id}</code> to abort",
        parse_mode=ParseMode.HTML
    )

    await asyncio.wait([reader, waiter])
    running_jobs.pop(job_id, None)

    if proc.returncode == 0:
        await msg.edit(
            f"✅ Hard-Mux `<code>{job_id}</code>` completed in {round(time.time()-start)}s",
            parse_mode=ParseMode.HTML
        )
        await asyncio.sleep(2)
        return output
    else:
        err = await proc.stderr.read()
        await msg.edit(
            "❌ Error during hard-mux!\n\n"
            f"<pre>{err.decode(errors='ignore')}</pre>",
            parse_mode=ParseMode.HTML
        )
        return False
