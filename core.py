import json
import re
import html
import subprocess
import threading
import uuid
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

import httpx
import yt_dlp
from tqdm import tqdm
import sys
import os

DEFAULT_UA = "Mozilla/5.0"

NOTE_ID_RE = re.compile(
    r"/(?:explore|discovery/item|item|user/profile/[0-9a-fA-F]{16,32})/([0-9a-fA-F]{16,32})"
)

INITIAL_STATE_RE = re.compile(
    r"window\.__INITIAL_STATE__\s*=\s*(\{.*?\})\s*</script>",
    re.DOTALL
)

INVALID_FS = re.compile(r'[\\/:*?"<>|\r\n\t]+')

_jobs = {}
_jobs_lock = threading.Lock()


def get_ffmpeg_path():
    if getattr(sys, 'frozen', False):
        return os.path.join(sys._MEIPASS, "ffmpeg.exe")
    return "ffmpeg"


def is_rednote(url):
    return "xiaohongshu" in url.lower() or "xhslink" in url.lower()


def is_youtube(url):
    return "youtube.com" in url.lower() or "youtu.be" in url.lower()


def is_tiktok(url):
    return "tiktok.com" in url.lower()


def is_youtube_list(url):
    return is_youtube(url) and ("/@" in url or "/shorts" in url or "/videos" in url)


def load_cookies(path):
    cookies = {}
    path = Path(path)
    if not path.exists():
        return cookies
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("#") or not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) >= 7:
            cookies[parts[5]] = parts[6]
    return cookies


def expand_youtube_list(url):
    videos = []
    opts = {
        "quiet": True,
        "extract_flat": True,
        "skip_download": True,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)

    def collect_entries(data):
        entries = data.get("entries", [])
        for entry in entries:
            if not entry:
                continue
            if "entries" in entry:
                collect_entries(entry)
                continue
            video_id = entry.get("id")
            title = entry.get("title", "No title")
            if video_id:
                videos.append({
                    "title": title,
                    "url": f"https://www.youtube.com/watch?v={video_id}",
                    "id": video_id,
                    "thumbnail": entry.get("thumbnail") or f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg",
                })

    collect_entries(info)
    return videos


def scan_youtube_urls(urls):
    results = []
    opts = {
        "quiet": True,
        "skip_download": True,
        "extractor_args": {
            "youtube": {
                "player_client": ["web", "android", "ios"],
            }
        },
    }
    for url in urls:
        if not is_youtube(url):
            continue
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
            video_id = info.get("id", "")
            title = info.get("title", "Unknown")
            thumbnail = info.get("thumbnail") or f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"
            duration = info.get("duration_string", "")
            results.append({
                "title": title,
                "url": f"https://www.youtube.com/watch?v={video_id}",
                "id": video_id,
                "thumbnail": thumbnail,
                "duration": duration,
            })
        except Exception as e:
            results.append({
                "title": f"Error: {str(e)[:50]}",
                "url": url,
                "id": "",
                "thumbnail": "",
                "duration": "",
                "error": True,
            })
    return results


def ytdlp_format(quality):
    if quality == 480:
        return "bestvideo[height<=480]+bestaudio/best[height<=480]/best"
    if quality == 720:
        return "bestvideo[height<=720]+bestaudio/best[height<=720]/best"
    if quality == 1080:
        return "bestvideo[height<=1080]+bestaudio/best[height<=1080]/best"
    return "bestvideo*+bestaudio/best"


def _format_bytes(b):
    if b is None or b < 0:
        return "N/A"
    for unit in ["B", "KB", "MB", "GB"]:
        if b < 1024:
            return f"{b:.1f} {unit}"
        b /= 1024
    return f"{b:.1f} TB"


def download_ytdlp_with_progress(url, output, quality, platform_name, job_id, send_fn):
    folder = output / platform_name
    folder.mkdir(parents=True, exist_ok=True)

    info_opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "extractor_args": {
            "youtube": {
                "player_client": ["web", "android", "ios", "tv_embedded"],
                "player_skip": ["webpage"],
            }
        },
    }

    title = url
    try:
        with yt_dlp.YoutubeDL(info_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            title = info.get("title", url)
    except Exception:
        pass

    send_fn(job_id, {"type": "log", "message": f"📥 Starting: {title}"})

    def progress_hook(d):
        if d["status"] == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            downloaded = d.get("downloaded_bytes", 0)
            speed = d.get("speed", 0)
            eta = d.get("eta")
            pct = (downloaded / total * 100) if total else 0
            send_fn(job_id, {
                "type": "progress",
                "title": title,
                "percent": round(pct, 1),
                "downloaded": _format_bytes(downloaded),
                "total": _format_bytes(total),
                "speed": _format_bytes(speed) + "/s" if speed else "N/A",
                "eta": f"{eta}s" if eta else "N/A",
            })
        elif d["status"] == "finished":
            send_fn(job_id, {
                "type": "progress",
                "title": title,
                "percent": 100,
                "downloaded": _format_bytes(d.get("total_bytes", 0)),
                "total": _format_bytes(d.get("total_bytes", 0)),
                "speed": "Done",
                "eta": "Done",
            })
            send_fn(job_id, {"type": "log", "message": f"✅ Downloaded: {title}"})
        elif d["status"] == "error":
            send_fn(job_id, {"type": "log", "message": f"❌ Error: {title}"})

    opts = {
        "format": ytdlp_format(quality),
        "outtmpl": f"{folder}/%(title).80s [%(id)s].%(ext)s",
        "merge_output_format": "mp4",
        "quiet": True,
        "no_warnings": True,
        "continuedl": True,
        "retries": 10,
        "fragment_retries": 10,
        "extractor_args": {
            "youtube": {
                "player_client": ["web", "android", "ios", "tv_embedded"],
                "player_skip": ["webpage"],
            }
        },
        "progress_hooks": [progress_hook],
    }

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([url])
        return f"✅ {platform_name}: {url}"
    except Exception as e:
        send_fn(job_id, {"type": "log", "message": f"❌ {platform_name} failed: {str(e)}"})
        return f"❌ {platform_name} failed: {url}\n   {e}"


def extract_note_id(url):
    match = NOTE_ID_RE.search(url)
    return match.group(1) if match else None


def decode_state(raw):
    raw = re.sub(r"\bundefined\b", "null", raw)
    return json.loads(raw)


def find_rednote_video(state, note_id):
    note_map = state["note"]["noteDetailMap"]
    entry = note_map.get(note_id) or next(iter(note_map.values()))
    note = entry["note"]
    title = note.get("title") or note.get("desc") or note_id
    video = note.get("video") or {}
    media = video.get("media") or {}
    stream = media.get("stream") or {}
    for codec in ("h265", "av1", "h264"):
        arr = stream.get(codec) or []
        if arr:
            video_url = arr[0].get("masterUrl")
            if not video_url:
                backup_urls = arr[0].get("backupUrls") or []
                video_url = backup_urls[0] if backup_urls else None
            if video_url:
                return title, video_url
    if video.get("url"):
        return title, video["url"]
    return None


def get_rednote_info(client, url):
    note_id = extract_note_id(url)
    if not note_id:
        raise Exception("Invalid RedNote URL.")
    headers = {
        "User-Agent": DEFAULT_UA,
        "Referer": "https://www.xiaohongshu.com/",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    response = client.get(url, headers=headers, follow_redirects=True, timeout=30)
    if response.status_code != 200:
        raise Exception(f"HTTP error: {response.status_code}")
    match = INITIAL_STATE_RE.search(response.text)
    if not match:
        raise Exception("Cannot find video data. Cookie may be expired.")
    state = decode_state(match.group(1))
    result = find_rednote_video(state, note_id)
    if not result:
        raise Exception("No video found. Maybe image-only post.")
    title, video_url = result
    title = html.unescape(title).strip()
    safe_title = INVALID_FS.sub("_", title)[:80].strip("._ ")
    if not safe_title:
        safe_title = note_id
    filename = f"{safe_title}__{note_id}"
    return filename, video_url


def download_rednote_file(client, url, path, job_id, send_fn):
    headers = {
        "User-Agent": DEFAULT_UA,
        "Referer": "https://www.xiaohongshu.com/",
    }
    temp_path = path.with_suffix(path.suffix + ".part")
    with client.stream("GET", url, headers=headers, timeout=60) as response:
        response.raise_for_status()
        total = int(response.headers.get("content-length", 0))
        downloaded = 0
        with open(temp_path, "wb") as f:
            for chunk in response.iter_bytes(chunk_size=1024 * 256):
                if chunk:
                    f.write(chunk)
                    downloaded += len(chunk)
                    pct = (downloaded / total * 100) if total else 0
                    send_fn(job_id, {
                        "type": "progress",
                        "title": path.name[:50],
                        "percent": round(pct, 1),
                        "downloaded": _format_bytes(downloaded),
                        "total": _format_bytes(total),
                        "speed": "N/A",
                        "eta": "N/A",
                    })
    temp_path.replace(path)


def crop_watermark(input_path):
    clean_path = input_path.with_name(input_path.stem + "_clean.mp4")
    command = [
        get_ffmpeg_path(),
        "-y",
        "-i", str(input_path),
        "-vf", "crop=iw-80:ih-80:0:0",
        "-c:a", "copy",
        str(clean_path),
    ]
    result = subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if result.returncode != 0:
        return None
    return clean_path


def download_rednote_with_progress(url, output, cookies, crop, job_id, send_fn):
    try:
        folder = output / "RedNote"
        folder.mkdir(parents=True, exist_ok=True)
        with httpx.Client(http2=False, cookies=cookies) as client:
            name, video_url = get_rednote_info(client, url)
            video_path = folder / f"{name}.mp4"
            send_fn(job_id, {"type": "log", "message": f"📥 Downloading: {name}"})
            download_rednote_file(client, video_url, video_path, job_id, send_fn)
            if crop:
                send_fn(job_id, {"type": "log", "message": "✂️ Cropping watermark..."})
                clean_path = crop_watermark(video_path)
                if clean_path:
                    try:
                        video_path.unlink()
                    except Exception:
                        pass
                    send_fn(job_id, {"type": "log", "message": f"✅ RedNote clean: {clean_path.name}"})
                    return f"✅ RedNote clean video: {clean_path}"
                send_fn(job_id, {"type": "log", "message": "⚠️ Crop failed, keeping original"})
                return f"✅ RedNote (crop failed): {video_path}"
            send_fn(job_id, {"type": "log", "message": f"✅ RedNote: {video_path.name}"})
            return f"✅ RedNote: {video_path}"
    except Exception as e:
        send_fn(job_id, {"type": "log", "message": f"❌ RedNote failed: {str(e)}"})
        return f"❌ RedNote failed: {url}\n   {e}"


def process_url_with_progress(url, output, cookies, quality, crop, job_id, send_fn):
    if is_youtube(url):
        if is_youtube_list(url):
            return "LIST_URL"
        return download_ytdlp_with_progress(url, output, quality, "YouTube", job_id, send_fn)
    if is_tiktok(url):
        return download_ytdlp_with_progress(url, output, quality, "TikTok", job_id, send_fn)
    if is_rednote(url):
        return download_rednote_with_progress(url, output, cookies, crop, job_id, send_fn)
    send_fn(job_id, {"type": "log", "message": f"❌ Unsupported: {url}"})
    return f"❌ Unsupported link: {url}"


def create_job():
    job_id = str(uuid.uuid4())[:8]
    with _jobs_lock:
        _jobs[job_id] = {"queue": [], "status": "pending", "results": []}
    return job_id


def get_job(job_id):
    with _jobs_lock:
        return _jobs.get(job_id)


def remove_job(job_id):
    with _jobs_lock:
        _jobs.pop(job_id, None)


def start_download(urls, output, quality, crop, cookies, job_id):
    job = get_job(job_id)
    if not job:
        return

    job["status"] = "running"

    def send(job_id, msg):
        with _jobs_lock:
            j = _jobs.get(job_id)
            if j:
                j["queue"].append(msg)

    send(job_id, {"type": "log", "message": f"🚀 Starting download ({len(urls)} items)"})
    send(job_id, {"type": "log", "message": f"📁 Output: {output}"})
    send(job_id, {"type": "log", "message": f"🎬 Quality: {quality if quality else 'best'}"})

    def process_one(url):
        result = process_url_with_progress(url, output, cookies, quality, crop, job_id, send)
        with _jobs_lock:
            j = _jobs.get(job_id)
            if j:
                j["results"].append(result)

    with ThreadPoolExecutor(max_workers=3) as executor:
        executor.map(process_one, urls)

    with _jobs_lock:
        j = _jobs.get(job_id)
        if j:
            j["status"] = "done"
            j["queue"].append({"type": "log", "message": "\n✅ All downloads finished!"})
