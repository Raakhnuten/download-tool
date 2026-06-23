import asyncio
import json
import os
import sys
import threading
import uuid
from pathlib import Path

import yt_dlp
from fastapi import FastAPI, Request, UploadFile, File, Query
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from core import (
    load_cookies,
    create_job,
    get_job,
    remove_job,
    start_download,
    detect_url_type,
    get_profile_error,
    normalize_video,
    expand_youtube_channel,
    expand_tiktok_profile,
    extract_rednote_metadata,
    extract_note_id,
)


def _get_base_path():
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS)
    return Path(os.environ.get("DOWNLOADER_BASE_DIR", Path(__file__).parent))


BASE = _get_base_path()

app = FastAPI(title="All-in-one Downloader")
app.mount("/image", StaticFiles(directory=str(BASE / "image")), name="image")
templates = Jinja2Templates(directory=str(BASE / "templates"))

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

    scan_id = str(uuid.uuid4())[:8]
    all_videos = []
    idx = 0
    cookies = load_cookies(COOKIE_PATH)

    for url in parsed:
        url_type = detect_url_type(url)

        if url_type == "rednote_profile_unsupported":
            msg = get_profile_error(url)
            yield f"data: {json.dumps({'type': 'scan_log', 'message': msg})}\n\n"
            continue

        elif url_type == "youtube_video":
            try:
                opts = {
                    "quiet": True,
                    "skip_download": True,
                }
                with yt_dlp.YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(url, download=False)
                vid = normalize_video(info, "YouTube")
                if not vid["thumbnail"] and vid["id"]:
                    vid["thumbnail"] = f"https://i.ytimg.com/vi/{vid['id']}/hqdefault.jpg"
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

        elif url_type == "youtube_channel":
            try:
                channel_videos = expand_youtube_channel(url)
                if not channel_videos:
                    raise Exception("No videos found in channel tab.")
                for vid in channel_videos:
                    all_videos.append(vid)
                    idx += 1
                    yield f"data: {json.dumps({'type': 'video', 'index': idx, 'video': vid})}\n\n"
            except Exception as e:
                err_video = {
                    "title": f"Error scanning channel: {str(e)[:60]}",
                    "url": url,
                    "id": "",
                    "thumbnail": "",
                    "duration": "",
                    "error": True,
                    "platform": "YouTube",
                }
                all_videos.append(err_video)
                idx += 1
                yield f"data: {json.dumps({'type': 'video', 'index': idx, 'video': err_video})}\n\n"

        elif url_type == "tiktok_video":
            platform = "TikTok"
            vid = {
                "title": f"Direct {platform} link — ready to download",
                "url": url,
                "id": "",
                "thumbnail": "",
                "duration": "",
                "platform": platform,
            }
            all_videos.append(vid)
            idx += 1
            yield f"data: {json.dumps({'type': 'video', 'index': idx, 'video': vid})}\n\n"

        elif url_type == "tiktok_profile":
            try:
                profile_videos = expand_tiktok_profile(url)
                if not profile_videos:
                    raise Exception("No videos found on profile.")
                for vid in profile_videos:
                    all_videos.append(vid)
                    idx += 1
                    yield f"data: {json.dumps({'type': 'video', 'index': idx, 'video': vid})}\n\n"
            except Exception as e:
                err_video = {
                    "title": f"Error scanning profile: {str(e)[:60]}",
                    "url": url,
                    "id": "",
                    "thumbnail": "",
                    "duration": "",
                    "error": True,
                    "platform": "TikTok",
                }
                all_videos.append(err_video)
                idx += 1
                yield f"data: {json.dumps({'type': 'video', 'index': idx, 'video': err_video})}\n\n"

        elif url_type == "rednote_video":
            meta = extract_rednote_metadata(url, cookies=cookies)
            note_id = meta.get("note_id") or ""
            vid = {
                "title": meta.get("title") or "RedNote post",
                "url": url,
                "id": note_id,
                "thumbnail": meta.get("thumbnail") or "",
                "duration": "",
                "platform": "RedNote",
            }
            all_videos.append(vid)
            idx += 1
            yield f"data: {json.dumps({'type': 'video', 'index': idx, 'video': vid})}\n\n"

        else:
            yield f"data: {json.dumps({'type': 'scan_log', 'message': f'Unsupported URL: {url[:80]}'})}\n\n"

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
    if not parsed:
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
    download_mode = body.get("download_mode", "video_audio")

    print(f"Received quality: {quality}, download_mode: {download_mode}")

    ALLOWED_DOWNLOAD_MODES = {"video_audio", "audio_mp3", "video_mute"}
    if download_mode not in ALLOWED_DOWNLOAD_MODES:
        download_mode = "video_audio"

    try:
        quality = int(quality) if quality else 720
    except ValueError:
        quality = 720

    cookies = load_cookies(COOKIE_PATH)
    job_id = create_job()

    threading.Thread(
        target=start_download,
        args=(urls, Path(output), quality, crop, cookies, job_id, download_mode),
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


@app.get("/api/browse-folder")
def browse_folder():
    try:
        import tkinter as tk
        from tkinter import filedialog

        print("[browse-folder] Endpoint called")
        print("[browse-folder] Opening Windows folder picker...")

        root = tk.Tk()
        root.withdraw()
        root.lift()
        root.attributes("-topmost", True)
        root.update()

        folder = filedialog.askdirectory(
            parent=root,
            title="Select output folder",
            mustexist=True
        )

        root.destroy()

        if not folder:
            print("[browse-folder] Cancelled")
            return JSONResponse({"path": None, "cancelled": True})

        print(f"[browse-folder] Selected: {folder}")
        return JSONResponse({"path": folder, "cancelled": False})

    except Exception as e:
        print(f"[browse-folder] Error: {e}")
        return JSONResponse(
            {"path": None, "cancelled": False, "error": str(e)},
            status_code=500
        )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
