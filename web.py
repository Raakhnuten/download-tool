import asyncio
import json
import os
import threading
import uuid
from pathlib import Path

import yt_dlp
from fastapi import FastAPI, Request, UploadFile, File, Query
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

from core import (
    expand_youtube_list,
    scan_youtube_urls,
    load_cookies,
    create_job,
    get_job,
    remove_job,
    start_download,
    is_youtube,
)

app = FastAPI(title="All-in-one Downloader")
templates = Jinja2Templates(directory="templates")

COOKIE_PATH = Path("cookies.txt")
DEFAULT_OUTPUT = Path("downloads")

_scan_cache = {}
_scan_cache_lock = threading.Lock()


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/api/list-dir")
async def list_dir(path: str = Query(default="")):
    if not path:
        if os.name == "nt":
            drives = []
            for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
                if os.path.exists(f"{letter}:\\"):
                    drives.append({"name": f"{letter}:\\", "path": f"{letter}:\\"})
            return {"drives": drives, "parent": None, "current": ""}
        path = "/"

    p = Path(path)
    if not p.exists() or not p.is_dir():
        return {"error": "Path not found"}

    items = []
    try:
        for entry in sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
            if entry.is_dir():
                items.append({"name": entry.name, "path": str(entry), "is_dir": True})
    except PermissionError:
        return {"error": "Permission denied"}

    parent = str(p.parent) if p.parent != p else None
    return {"items": items, "parent": parent, "current": str(p)}


async def _scan_generator(urls_str):
    parsed = [u.strip() for u in urls_str.split() if u.strip().startswith("http")]
    yt_urls = [u for u in parsed if is_youtube(u)]

    scan_id = str(uuid.uuid4())[:8]
    all_videos = []
    idx = 0

    for url in yt_urls:
        if "/@" in url or "/shorts" in url or "/videos" in url:
            try:
                videos = expand_youtube_list(url)
                for v in videos:
                    all_videos.append(v)
                    idx += 1
                    yield f"data: {json.dumps({'type': 'video', 'index': idx, 'video': v})}\n\n"
                    await asyncio.sleep(0.02)
            except Exception as e:
                err_video = {
                    "title": f"Error: {str(e)[:60]}",
                    "url": url,
                    "id": "",
                    "thumbnail": "",
                    "duration": "",
                    "error": True,
                }
                all_videos.append(err_video)
                idx += 1
                yield f"data: {json.dumps({'type': 'video', 'index': idx, 'video': err_video})}\n\n"
        else:
            try:
                opts = {
                    "quiet": True,
                    "skip_download": True,
                    "extractor_args": {
                        "youtube": {
                            "player_client": ["web", "android", "ios"],
                        }
                    },
                }
                with yt_dlp.YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(url, download=False)
                video_id = info.get("id", "")
                title = info.get("title", "Unknown")
                thumbnail = info.get("thumbnail") or f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"
                duration = info.get("duration_string", "")
                vid = {
                    "title": title,
                    "url": f"https://www.youtube.com/watch?v={video_id}",
                    "id": video_id,
                    "thumbnail": thumbnail,
                    "duration": duration,
                }
                all_videos.append(vid)
                idx += 1
                yield f"data: {json.dumps({'type': 'video', 'index': idx, 'video': vid})}\n\n"
            except Exception as e:
                err_video = {
                    "title": f"Error: {str(e)[:60]}",
                    "url": url,
                    "id": "",
                    "thumbnail": "",
                    "duration": "",
                    "error": True,
                }
                all_videos.append(err_video)
                idx += 1
                yield f"data: {json.dumps({'type': 'video', 'index': idx, 'video': err_video})}\n\n"

    with _scan_cache_lock:
        _scan_cache[scan_id] = all_videos

    total = len(all_videos)
    pages = max(1, (total + 30 - 1) // 30)
    yield f"data: {json.dumps({'type': 'done', 'scan_id': scan_id, 'total': total, 'pages': pages})}\n\n"


@app.post("/api/scan")
async def scan_urls(request: Request):
    body = await request.json()
    urls = body.get("urls", "")

    parsed = [u.strip() for u in urls.split() if u.strip().startswith("http")]
    yt_urls = [u for u in parsed if is_youtube(u)]
    if not yt_urls:
        async def empty_gen():
            yield f"data: {json.dumps({'type': 'done', 'scan_id': '', 'total': 0, 'pages': 0})}\n\n"
        return StreamingResponse(empty_gen(), media_type="text/event-stream")

    return StreamingResponse(_scan_generator(urls), media_type="text/event-stream")


@app.post("/api/scan-page")
async def scan_page(request: Request):
    body = await request.json()
    scan_id = body.get("scan_id", "")
    page = body.get("page", 1)
    page_size = 30

    with _scan_cache_lock:
        all_videos = _scan_cache.get(scan_id, [])

    if not all_videos:
        return {"success": False, "error": "Scan expired"}

    total = len(all_videos)
    pages = max(1, (total + page_size - 1) // page_size)
    start = (page - 1) * page_size
    end = start + page_size
    page_videos = all_videos[start:end]

    return {
        "success": True,
        "videos": page_videos,
        "count": len(page_videos),
        "total": total,
        "page": page,
        "pages": pages,
    }


@app.post("/api/fetch-videos")
async def fetch_videos(request: Request):
    body = await request.json()
    url = body.get("url", "")
    try:
        videos = expand_youtube_list(url)
        return {"success": True, "videos": videos, "count": len(videos)}
    except Exception as e:
        return {"success": False, "error": str(e)}


@app.post("/api/upload-cookies")
async def upload_cookies(file: UploadFile = File(...)):
    try:
        content = await file.read()
        COOKIE_PATH.write_bytes(content)
        return {"success": True, "message": f"Cookies saved ({len(content)} bytes)"}
    except Exception as e:
        return {"success": False, "detail": str(e)}


@app.post("/api/download")
async def start_download_endpoint(request: Request):
    body = await request.json()
    urls = body.get("urls", [])
    quality = body.get("quality")
    crop = body.get("crop", True)
    output = body.get("output", str(DEFAULT_OUTPUT))

    if quality and quality != "best":
        try:
            quality = int(quality)
        except ValueError:
            quality = None
    else:
        quality = None

    cookies = load_cookies(COOKIE_PATH)
    job_id = create_job()

    threading.Thread(
        target=start_download,
        args=(urls, Path(output), quality, crop, cookies, job_id),
        daemon=True,
    ).start()

    return {"job_id": job_id}


@app.get("/api/stream/{job_id}")
async def stream_progress(job_id: str):
    job = get_job(job_id)
    if not job:
        return StreamingResponse(iter([]), media_type="text/event-stream")

    async def event_generator():
        idx = 0
        while True:
            queue = job.get("queue", [])
            status = job.get("status")

            while idx < len(queue):
                msg = queue[idx]
                idx += 1
                yield f"data: {json.dumps(msg)}\n\n"

            if status == "done":
                yield f"data: {json.dumps({'type': 'done'})}\n\n"
                remove_job(job_id)
                break

            await asyncio.sleep(0.2)

    return StreamingResponse(event_generator(), media_type="text/event-stream")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
