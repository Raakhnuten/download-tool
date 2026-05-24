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
import sys
import os

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

NOTE_ID_RE = re.compile(
    r"/(?:explore|discovery/item|item)/([0-9a-fA-F]{16,32})"
)



INITIAL_STATE_RE = re.compile(
    r"window\.__INITIAL_STATE__\s*=\s*(\{.*?\})\s*</script>",
    re.DOTALL
)

INVALID_FS = re.compile(r'[\\/:*?"<>|\r\n\t]+')

_jobs = {}
_jobs_lock = threading.Lock()


def clean_url(value):
    if not value:
        return None
    value = str(value).strip()
    if not value:
        return None
    value = value.replace("\\u002F", "/")
    value = value.replace("\\/", "/")
    value = value.replace("&amp;", "&")
    value = html.unescape(value)
    if "<" in value or ">" in value:
        return None
    if '"' in value or "'" in value:
        return None
    if not value.startswith("http"):
        return None
    return value


def get_ffmpeg_path():
    if getattr(sys, 'frozen', False):
        return os.path.join(sys._MEIPASS, "ffmpeg.exe")
    return "ffmpeg"


def is_rednote(url):
    return "xiaohongshu" in url.lower() or "xhslink" in url.lower() or "rednote.com" in url.lower()


def is_youtube(url):
    return "youtube.com" in url.lower() or "youtu.be" in url.lower()


def is_tiktok(url):
    return "tiktok.com" in url.lower()


def detect_url_type(url):
    """Classify a URL for scanning and download.

    Returns one of:
      "youtube_video"            — YouTube /watch or /shorts (direct)
      "youtube_channel"          — YouTube @handle/shorts or /videos tab
      "tiktok_video"             — TikTok /@user/video/ID (direct)
      "tiktok_profile"           — TikTok /@user profile
      "rednote_video"            — RedNote /explore/ or xhslink (direct)
      "rednote_profile_unsupported" — RedNote /user/profile/
      "unsupported"              — everything else
    """
    u = url.strip().lower()

    # --- RedNote / Xiaohongshu ---
    if "xiaohongshu" in u or "xhslink" in u or "rednote.com" in u:
        if "/user/profile/" in u:
            return "rednote_profile_unsupported"
        if "/explore/" in u or "/discovery/item/" in u or "/item/" in u:
            return "rednote_video"
        if "xhslink.com" in u:
            return "rednote_video"
        return "unsupported"

    # --- YouTube ---
    if "youtube.com" in u or "youtu.be" in u:
        if "/watch" in u or "/shorts/" in u:
            return "youtube_video"
        if "youtu.be/" in u:
            return "youtube_video"
        if ("youtube.com/@" in u or "youtube.com/channel/" in u) and ("/shorts" in u or "/videos" in u):
            return "youtube_channel"
        if "youtube.com/@" in u or "youtube.com/channel/" in u or "/user/" in u or "/c/" in u:
            return "unsupported"
        return "youtube_video"

    # --- TikTok ---
    if "tiktok.com" in u:
        if "/video/" in u:
            return "tiktok_video"
        if "/@" in u:
            return "tiktok_profile"
        return "tiktok_video"

    return "unsupported"


def get_profile_error(url):
    """Return a human-readable error message for an unsupported profile URL."""
    u = url.lower()
    if "xiaohongshu" in u or "rednote.com" in u:
        return "Profile links are not supported. Please paste a direct RedNote video link like https://www.rednote.com/explore/POST_ID?..."
    return "Profile links are not supported."


def _rednote_headers(url=""):
    is_rednote_domain = "rednote.com" in url.lower()
    base = "https://www.rednote.com/" if is_rednote_domain else "https://www.xiaohongshu.com/"
    return {
        "User-Agent": DEFAULT_UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9,km;q=0.8,zh-CN;q=0.7,zh;q=0.6",
        "Referer": base,
        "Connection": "keep-alive",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "same-origin",
        "Sec-Ch-Ua": '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"Windows"',
        "Upgrade-Insecure-Requests": "1",
    }


def _sanitize_title(title, fallback="Untitled video"):
    if not title or not title.strip():
        return fallback
    title = title.strip()
    if not title:
        return fallback
    only_emoji = re.sub(r'[\U00010000-\U0010ffff\u200d\u20e3\ufe0f\u2600-\u27bf\u2700-\u27bf]', '', title)
    if not only_emoji.strip():
        return f"{fallback} (emoji title)"
    return title


def _pick_best_thumbnail(item):
    thumbnail = item.get("thumbnail")
    clean = clean_url(thumbnail)
    if clean:
        return clean
    thumbnails = item.get("thumbnails") or []
    valid = [t for t in thumbnails if clean_url(t.get("url"))]
    if valid:
        return clean_url(valid[-1]["url"])
    return ""


def normalize_video(item, platform):
    thumbnail = _pick_best_thumbnail(item)
    title = _sanitize_title(
        item.get("title") or item.get("description") or item.get("uploader") or item.get("id"),
        f"{platform} video"
    )
    video_id = item.get("id") or item.get("display_id") or str(uuid.uuid4())
    url = item.get("webpage_url") or item.get("url") or ""
    duration = item.get("duration_string") or ""
    if not duration and item.get("duration"):
        secs = int(item["duration"])
        duration = f"{secs // 60}:{secs % 60:02d}"

    if platform == "TikTok" and not url:
        url = f"https://www.tiktok.com/@user/video/{video_id}"
    if platform == "YouTube" and not url and video_id:
        url = f"https://www.youtube.com/watch?v={video_id}"

    return {
        "id": video_id,
        "title": title,
        "url": url,
        "thumbnail": thumbnail,
        "duration": duration,
        "platform": platform,
        "error": False,
    }


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


def expand_youtube_channel(url):
    """Extract video list from a YouTube channel tab (/shorts or /videos) using yt-dlp."""
    videos = []
    opts = {
        "quiet": True,
        "extract_flat": True,
        "skip_download": True,
        "extractor_args": {
            "youtube": {
                "player_client": ["web", "android", "ios"],
            }
        },
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)

    def collect_entries(data):
        for entry in data.get("entries", []):
            if not entry:
                continue
            if "entries" in entry:
                collect_entries(entry)
                continue
            video_id = entry.get("id")
            title = entry.get("title", "No title")
            if video_id:
                videos.append({
                    "id": video_id,
                    "title": title,
                    "url": f"https://www.youtube.com/watch?v={video_id}",
                    "thumbnail": f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg",
                    "duration": "",
                    "platform": "YouTube",
                    "error": False,
                })
    collect_entries(info)
    return videos


def expand_tiktok_profile(url):
    """Extract video list from a TikTok profile using yt-dlp."""
    videos = []
    opts = {
        "quiet": True,
        "skip_download": True,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)

    def collect_entries(data):
        if isinstance(data, list):
            entries = data
        elif isinstance(data, dict):
            entries = data.get("entries") or data.get("items") or []
        else:
            return
        for entry in entries:
            if not entry:
                continue
            if isinstance(entry, dict) and ("entries" in entry or "items" in entry):
                collect_entries(entry)
                continue
            video_id = None
            if isinstance(entry, dict):
                video_id = entry.get("id") or entry.get("display_id") or entry.get("video_id")
            if not video_id:
                continue
            title = entry.get("title") or entry.get("description") or "TikTok video"
            webpage_url = entry.get("webpage_url") or ""
            if not webpage_url:
                webpage_url = url.rstrip("/") + f"/video/{video_id}"
            videos.append({
                "id": video_id,
                "title": title,
                "url": webpage_url,
                "thumbnail": entry.get("thumbnail", ""),
                "duration": "",
                "platform": "TikTok",
                "error": False,
            })
    collect_entries(info)
    return videos


def find_ffmpeg():
    import shutil
    known = [
        Path("C:\\ffmpeg-8.0-essentials_build\\bin\\ffmpeg.exe"),
        Path("C:\\ffmpeg-8.0-essentials_build\\bin\\ffmpeg.EXE"),
        Path("ffmpeg.exe"),
        Path("tools/ffmpeg.exe"),
    ]
    for p in known:
        if p.exists():
            return str(p.parent.resolve())
    found = shutil.which("ffmpeg")
    if found:
        return str(Path(found).parent.resolve())
    return None


def get_ytdlp_format(quality, download_mode="video_audio"):
    quality = str(quality or "720")
    download_mode = str(download_mode or "video_audio")

    if download_mode == "audio_mp3":
        return "bestaudio/best"

    if download_mode == "video_mute":
        if quality == "1080":
            return "bestvideo[height<=1080][ext=mp4]/bestvideo[height<=1080]/best[height<=1080]"
        return "bestvideo[height<=720][ext=mp4]/bestvideo[height<=720]/best[height<=720]"

    if quality == "1080":
        return "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[height<=1080]/best"
    if quality == "720":
        return "bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/best[height<=720]/best"
    if quality == "480":
        return "bestvideo[height<=480][ext=mp4]+bestaudio[ext=m4a]/best[height<=480]/best"
    return "bestvideo*+bestaudio/best"


def _format_bytes(b):
    if b is None or b < 0:
        return "N/A"
    for unit in ["B", "KB", "MB", "GB"]:
        if b < 1024:
            return f"{b:.1f} {unit}"
        b /= 1024
    return f"{b:.1f} TB"


def download_ytdlp_with_progress(url, output, quality, platform_name, job_id, send_fn, download_mode="video_audio"):
    folder = output / platform_name
    folder.mkdir(parents=True, exist_ok=True)

    mode_labels = {
        "video_audio": "Video + Audio",
        "audio_mp3": "Audio only MP3",
        "video_mute": "Video only mute",
    }

    ffmpeg_path = find_ffmpeg()

    if download_mode == "audio_mp3":
        fmt = "bestaudio/best"
        outtmpl = f"{folder}/%(title).80s [%(id)s]_audio.%(ext)s"
        opts = {
            "format": fmt,
            "outtmpl": outtmpl,
            "quiet": True,
            "no_warnings": True,
            "continuedl": True,
            "retries": 10,
            "fragment_retries": 10,
            "postprocessors": [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": "192",
                }
            ],
        }
        if not ffmpeg_path:
            send_fn(job_id, {"type": "log", "message": "❌ FFmpeg is required for MP3 conversion."})
            return f"❌ {platform_name} MP3 failed: FFmpeg not found"
        opts["ffmpeg_location"] = ffmpeg_path
        send_fn(job_id, {"type": "log", "message": (
            f"Download mode: {mode_labels[download_mode]}\n"
            f"yt-dlp format: {fmt}\n"
            f"FFmpeg location: {ffmpeg_path}\n"
            f"Output: MP3 audio"
        )})
    else:
        fmt = get_ytdlp_format(quality, download_mode)
        outtmpl = f"{folder}/%(title).80s [%(id)s].%(ext)s"
        opts = {
            "format": fmt,
            "outtmpl": outtmpl,
            "merge_output_format": "mp4",
            "quiet": True,
            "no_warnings": True,
            "continuedl": True,
            "retries": 10,
            "fragment_retries": 10,
        }
        if ffmpeg_path:
            opts["ffmpeg_location"] = ffmpeg_path
        mode_str = mode_labels.get(download_mode, "Video + Audio")
        send_fn(job_id, {"type": "log", "message": (
            f"Selected quality: {quality}p\n"
            f"Download mode: {mode_str}\n"
            f"yt-dlp format: {fmt}\n"
            f"FFmpeg location: {ffmpeg_path or 'NOT FOUND'}\n"
            f"Download URL: {url[:100]}"
        )})

    send_fn(job_id, {"type": "log", "message": f"yt-dlp version: {yt_dlp.version.__version__}"})

    # Pre-check: extract info and log available heights (info only, never blocks)
    if download_mode != "audio_mp3":
        requested_quality = int(quality) if str(quality).isdigit() else 0
        try:
            pre_opts = {
                "quiet": True,
                "no_warnings": True,
                "skip_download": True,
            }
            with yt_dlp.YoutubeDL(pre_opts) as ydl:
                info = ydl.extract_info(url, download=False)
            heights = sorted(set(
                f.get("height") for f in (info.get("formats") or [])
                if f.get("height")
            ))
            title = info.get("title", url)
            send_fn(job_id, {"type": "log", "message": f"Title: {title[:80]}"})
            if heights:
                send_fn(job_id, {"type": "log", "message": f"Available heights: {heights}"})
                if requested_quality > 0 and requested_quality not in heights:
                    send_fn(job_id, {"type": "log", "message": (
                        f"Requested {requested_quality}p, but this video may only "
                        f"provide lower quality. yt-dlp will download best available under {requested_quality}p."
                    )})
            else:
                send_fn(job_id, {"type": "log", "message": "Height info not available from extractor."})
        except Exception as e:
            send_fn(job_id, {"type": "log", "message": f"Pre-check: {str(e)[:80]}"})
            title = url
    else:
        # Audio mode: still try to get title
        try:
            pre_opts = {"quiet": True, "no_warnings": True, "skip_download": True}
            with yt_dlp.YoutubeDL(pre_opts) as ydl:
                info = ydl.extract_info(url, download=False)
            title = info.get("title", url)
            send_fn(job_id, {"type": "log", "message": f"Title: {title[:80]}"})
        except Exception as e:
            send_fn(job_id, {"type": "log", "message": f"Pre-check: {str(e)[:80]}"})
            title = url

    send_fn(job_id, {"type": "log", "message": f"Downloading: {title[:80]}"})

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
            send_fn(job_id, {"type": "log", "message": f"Downloaded: {title[:80]}"})
        elif d["status"] == "error":
            send_fn(job_id, {"type": "log", "message": f"Error: {title[:80]}"})

    opts["progress_hooks"] = [progress_hook]

    cookie_file = Path("cookies.txt")
    if cookie_file.exists():
        opts["cookiefile"] = str(cookie_file.resolve())
        send_fn(job_id, {"type": "log", "message": f"Using cookies: {cookie_file.resolve()}"})

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([url])
        send_fn(job_id, {"type": "log", "message": "Download completed."})
        if download_mode == "audio_mp3":
            return f"MP3: {url}"
        return f"Downloaded: {url}"
    except Exception as e:
        send_fn(job_id, {"type": "log", "message": f"{platform_name} failed: {str(e)}"})
        return f"{platform_name} failed: {url}\n   {e}"


def extract_note_id(url):
    match = NOTE_ID_RE.search(url)
    return match.group(1) if match else None


def extract_rednote_metadata(url, cookies=None):
    """
    Fetch RedNote note page and extract title + thumbnail.

    Returns: dict with keys: title, thumbnail, note_id
    On any failure, all values are None.
    """
    note_id = extract_note_id(url)
    if not note_id:
        return {"title": None, "thumbnail": None, "note_id": None}

    try:
        headers = _rednote_headers(url)
        with httpx.Client(http2=False, cookies=cookies or {}, timeout=30) as client:
            response = client.get(url, headers=headers, follow_redirects=True)

        if response.status_code != 200:
            return {"title": None, "thumbnail": None, "note_id": note_id}

        match = INITIAL_STATE_RE.search(response.text)
        if not match:
            return {"title": None, "thumbnail": None, "note_id": note_id}

        state = decode_state(match.group(1))

        note = None
        note_map = state.get("note", {}).get("noteDetailMap", {})
        if note_id in note_map:
            entry = note_map[note_id]
            note = entry.get("note") if isinstance(entry, dict) else None
        elif note_map:
            first_key = next(iter(note_map))
            entry = note_map[first_key]
            note = entry.get("note") if isinstance(entry, dict) else None

        if not note:
            return {"title": None, "thumbnail": None, "note_id": note_id}

        # --- title ---
        title = (
            note.get("title")
            or note.get("displayTitle")
            or note.get("display_title")
            or note.get("desc")
            or note.get("description")
        )
        if title:
            title = html.unescape(title).strip()

        # --- thumbnail ---
        thumbnail = None
        thumbnail = thumbnail or note.get("cover")
        thumbnail = thumbnail or note.get("coverUrl")
        thumbnail = thumbnail or note.get("cover_url")
        thumbnail = thumbnail or note.get("displayCover")
        thumbnail = thumbnail or note.get("display_cover")

        if not thumbnail:
            images = (
                note.get("imageList")
                or note.get("images_list")
                or note.get("images")
                or []
            )
            if images and isinstance(images, list):
                first = images[0]
                if isinstance(first, dict):
                    for key in ("url", "urlDefault", "original", "urlDefault"):
                        val = first.get(key)
                        if val:
                            thumbnail = val
                            break

        if thumbnail:
            thumbnail = clean_url(thumbnail)

        return {
            "title": title,
            "thumbnail": thumbnail or "",
            "note_id": note_id,
        }

    except Exception:
        return {"title": None, "thumbnail": None, "note_id": note_id}


def decode_state(raw):
    raw = re.sub(r"\bundefined\b", "null", raw)
    return json.loads(raw)


def _extract_video_urls_from_note(note):
    """Extract video URLs from a RedNote note object using multiple strategies. Returns list of (codec, url)."""
    candidates = []

    # Strategy 1: video.media.stream.{codec}[0].masterUrl
    video = note.get("video") or {}
    media = video.get("media") or {}
    stream = media.get("stream") or {}
    for codec in ("h265", "av1", "h264", "h266"):
        arr = stream.get(codec) or []
        if arr and isinstance(arr, list):
            for item in arr:
                if isinstance(item, dict):
                    url = item.get("masterUrl")
                    if url:
                        clean = clean_url(url)
                        if clean:
                            candidates.append((codec, clean))
                    backup = item.get("backupUrls") or []
                    if backup and isinstance(backup, list):
                        for b in backup:
                            clean = clean_url(b)
                            if clean:
                                candidates.append((f"{codec}_backup", clean))

    # Strategy 2: video.url (direct URL)
    direct_url = video.get("url")
    if direct_url:
        clean = clean_url(direct_url)
        if clean:
            candidates.append(("direct", clean))

    # Strategy 3: video.media.videoUrl
    video_url = media.get("videoUrl") or media.get("video_url")
    if video_url:
        clean = clean_url(video_url)
        if clean:
            candidates.append(("media_videoUrl", clean))

    # Strategy 4: video.consumer.originVideoUrl
    consumer = video.get("consumer") or {}
    origin_url = consumer.get("originVideoUrl")
    if origin_url:
        clean = clean_url(origin_url)
        if clean:
            candidates.append(("consumer_origin", clean))

    # Strategy 5: video.captionInfo (sometimes has video)
    caption = video.get("captionInfo") or {}
    caption_url = caption.get("url")
    if caption_url:
        clean = clean_url(caption_url)
        if clean:
            candidates.append(("caption", clean))

    # Strategy 6: imageList with video type (some posts store video in images)
    images = note.get("imageList") or note.get("images_list") or note.get("images") or []
    if images and isinstance(images, list):
        for img in images:
            if isinstance(img, dict):
                if img.get("type") == "VIDEO" or img.get("isVideo"):
                    img_url = img.get("url") or img.get("urlDefault") or img.get("original")
                    if img_url:
                        clean = clean_url(img_url)
                        if clean:
                            candidates.append(("imageList_video", clean))
                # Also check video-specific fields in image objects
                stream_data = img.get("stream") or {}
                for codec in ("h265", "av1", "h264"):
                    arr = stream_data.get(codec) or []
                    if arr and isinstance(arr, list):
                        for item in arr:
                            if isinstance(item, dict):
                                url = item.get("masterUrl")
                                if url:
                                    clean = clean_url(url)
                                    if clean:
                                        candidates.append((f"imageList_{codec}", clean))

    # Strategy 7: noteCard.videoUrl
    note_card = note.get("noteCard") or {}
    if isinstance(note_card, dict):
        nc_video = note_card.get("video") or {}
        if isinstance(nc_video, dict):
            nc_url = nc_video.get("url") or nc_video.get("videoUrl")
            if nc_url:
                clean = clean_url(nc_url)
                if clean:
                    candidates.append(("noteCard", clean))

    return candidates


def find_rednote_video(state, note_id, send_fn=None, job_id=None):
    """Find video URL from RedNote state. Tries multiple extraction paths with debug logging."""

    def log(msg):
        if send_fn and job_id:
            send_fn(job_id, {"type": "log", "message": msg})

    # Try to find the note in noteDetailMap
    note = None
    note_map = state.get("note", {}).get("noteDetailMap", {})

    if note_id and note_id in note_map:
        log(f"  Found note_id '{note_id}' in noteDetailMap")
        entry = note_map[note_id]
        note = entry.get("note") if isinstance(entry, dict) else None
    elif note_map:
        # Try first available note
        first_key = next(iter(note_map))
        entry = note_map[first_key]
        note = entry.get("note") if isinstance(entry, dict) else None
        log(f"  note_id '{note_id}' not in map, using first available: '{first_key}'")

    # Fallback: search entire state for matching note_id
    if not note and note_id:
        def search_notes(obj, depth=0):
            if depth > 10:
                return None
            if isinstance(obj, dict):
                if obj.get("noteId") == note_id or obj.get("note_id") == note_id:
                    return obj
                for v in obj.values():
                    result = search_notes(v, depth + 1)
                    if result:
                        return result
            elif isinstance(obj, list):
                for item in obj:
                    result = search_notes(item, depth + 1)
                    if result:
                        return result
            return None

        note = search_notes(state)
        if note:
            log(f"  Found note by deep search for id '{note_id}'")

    if not note:
        log(f"  Note not found in state. noteDetailMap keys: {list(note_map.keys())[:5]}")
        return None

    # Extract title
    title = (
        note.get("title")
        or note.get("displayTitle")
        or note.get("display_title")
        or note.get("desc")
        or note.get("description")
        or note_id
        or "RedNote video"
    )

    # Check if this is a video post
    note_type = note.get("type", "")
    log(f"  Note type: '{note_type}', title: '{str(title)[:60]}'")

    if note_type == "normal":
        # Could still be a video — try extraction anyway
        log(f"  Note type is 'normal' (may be image-only), attempting video extraction...")

    # Extract video URLs using multiple strategies
    candidates = _extract_video_urls_from_note(note)

    if candidates:
        log(f"  Found {len(candidates)} video URL candidate(s)")
        for i, (codec, url) in enumerate(candidates[:3]):
            log(f"  [{i+1}] {codec}: {url[:100]}...")
        # Return first candidate (prefer h264/h265 over backups)
        for codec, url in candidates:
            if codec in ("h264", "h265", "av1", "direct", "media_videoUrl"):
                return title, url
        # Fallback to any available
        return title, candidates[0][1]

    log(f"  No video URLs found in note. Keys in note: {list(note.keys())[:10]}")
    return None


def get_rednote_info(client, url, job_id=None, send_fn=None):
    def log(msg):
        if send_fn and job_id:
            send_fn(job_id, {"type": "log", "message": msg})

    note_id = extract_note_id(url)
    if not note_id:
        raise Exception(f"Invalid RedNote URL. Could not extract note_id from: {url}")

    from urllib.parse import urlparse as _urlparse
    parsed_url = _urlparse(url)
    has_xsec_token = "xsec_token" in parsed_url.query
    has_xsec_source = "xsec_source" in parsed_url.query

    log(f"  RedNote note_id: {note_id}")
    log(f"  Requesting: {url}")
    log(f"  has_xsec_token={has_xsec_token}, has_xsec_source={has_xsec_source}")

    headers = _rednote_headers(url)
    response = client.get(url, headers=headers, follow_redirects=True, timeout=30)

    final_url = str(response.url)
    log(f"  HTTP {response.status_code}, final URL: {final_url[:100]}")
    log(f"  Response length: {len(response.text)} bytes")

    if response.status_code != 200:
        raise Exception(f"HTTP error: {response.status_code}")

    # Check for login-required / blocked page
    text = response.text
    if "login" in text.lower() and "sign in" in text.lower() and len(text) < 50000:
        log(f"  Response appears to be a login page. Cookies may be expired.")

    # Check for video-related keywords
    video_keywords = ["video", "videoUrl", "video_url", "stream", "h264", "h265", "masterUrl", "backupUrls", "media"]
    found_keywords = [kw for kw in video_keywords if kw.lower() in text.lower()]
    log(f"  Video keywords in response: {found_keywords}")

    match = INITIAL_STATE_RE.search(text)
    if not match:
        # Try alternate patterns
        alt_patterns = [
            re.compile(r"__INITIAL_STATE__\s*=\s*(\{.*?\})\s*;\s*</script>", re.DOTALL),
            re.compile(r"window\.__INITIAL_STATE__\s*=\s*(\{.*?\})\s*;\s*", re.DOTALL),
            re.compile(r'"noteDetailMap"\s*:\s*(\{.*?\})\s*[,}]', re.DOTALL),
        ]
        for pat in alt_patterns:
            match = pat.search(text)
            if match:
                log(f"  Found state with alternate pattern")
                break

    if not match:
        raise Exception("Cannot find video data in page. Cookie may be expired or page structure changed.")

    log(f"  Parsing __INITIAL_STATE__ JSON...")
    try:
        state = decode_state(match.group(1))
    except Exception as e:
        raise Exception(f"Failed to parse page data: {str(e)[:80]}")

    log(f"  State top-level keys: {list(state.keys())[:8]}")

    result = find_rednote_video(state, note_id, send_fn, job_id)
    if not result:
        raise Exception("No video found. Maybe image-only post.")

    title, video_url = result
    title = html.unescape(title).strip()
    safe_title = INVALID_FS.sub("_", title)[:80].strip("._ ")
    if not safe_title:
        safe_title = note_id
    filename = f"{safe_title}__{note_id}"
    log(f"  Video URL found: {video_url[:100]}...")
    return filename, video_url


def download_rednote_file(client, url, path, job_id, send_fn):
    headers = _rednote_headers(url)
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


def _rednote_extract_audio(video_path, job_id, send_fn):
    """Extract audio from RedNote video using ffmpeg."""
    ffmpeg = get_ffmpeg_path()
    if not ffmpeg:
        send_fn(job_id, {"type": "log", "message": "FFmpeg is required for MP3 conversion."})
        return None
    audio_path = video_path.with_suffix(".mp3")
    cmd = [ffmpeg, "-y", "-i", str(video_path), "-vn", "-acodec", "libmp3lame", "-b:a", "192k", str(audio_path)]
    result = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if result.returncode != 0:
        send_fn(job_id, {"type": "log", "message": "Failed to extract audio from RedNote video."})
        return None
    return audio_path


def _rednote_mute_video(video_path, job_id, send_fn):
    """Remove audio from RedNote video using ffmpeg."""
    ffmpeg = get_ffmpeg_path()
    if not ffmpeg:
        send_fn(job_id, {"type": "log", "message": "FFmpeg is required for mute video."})
        return None
    mute_path = video_path.with_name(video_path.stem + "_mute.mp4")
    cmd = [ffmpeg, "-y", "-i", str(video_path), "-c:v", "copy", "-an", str(mute_path)]
    result = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if result.returncode != 0:
        send_fn(job_id, {"type": "log", "message": "Failed to remove audio from RedNote video."})
        return None
    return mute_path


def download_rednote_with_progress(url, output, cookies, crop, quality, job_id, send_fn, download_mode="video_audio"):
    try:
        mode_labels = {
            "video_audio": "Video + Audio",
            "audio_mp3": "Audio only MP3",
            "video_mute": "Video only mute",
        }
        send_fn(job_id, {"type": "log", "message": f"RedNote direct link detected. Mode: {mode_labels.get(download_mode, 'Video + Audio')}"})
        from urllib.parse import urlparse as _urlparse
        from urllib.parse import parse_qs
        parsed_url = _urlparse(url)
        note_id_dl = extract_note_id(url)
        qs = parse_qs(parsed_url.query)
        send_fn(job_id, {
            "type": "log",
            "message": (
                f"[RedNote] id={note_id_dl or 'N/A'}, "
                f"platform=RedNote, url={url}, "
                f"has_xsec_token={'xsec_token' in qs}, "
                f"has_xsec_source={'xsec_source' in qs}"
            )
        })

        folder = output / "RedNote"
        folder.mkdir(parents=True, exist_ok=True)
        with httpx.Client(http2=False, cookies=cookies) as client:
            name, video_url = get_rednote_info(client, url, job_id, send_fn)
            video_path = folder / f"{name}.mp4"
            send_fn(job_id, {"type": "log", "message": f"Downloading: {name}"})
            download_rednote_file(client, video_url, video_path, job_id, send_fn)

            if download_mode == "audio_mp3":
                send_fn(job_id, {"type": "log", "message": "Extracting audio to MP3..."})
                audio_path = _rednote_extract_audio(video_path, job_id, send_fn)
                if audio_path:
                    try:
                        video_path.unlink()
                    except Exception:
                        pass
                    send_fn(job_id, {"type": "log", "message": f"MP3: {audio_path.name}"})
                    return f"RedNote MP3: {audio_path}"
                send_fn(job_id, {"type": "log", "message": "MP3 extraction failed, keeping original video."})
            elif download_mode == "video_mute":
                send_fn(job_id, {"type": "log", "message": "Removing audio track..."})
                mute_path = _rednote_mute_video(video_path, job_id, send_fn)
                if mute_path:
                    try:
                        video_path.unlink()
                    except Exception:
                        pass
                    send_fn(job_id, {"type": "log", "message": f"Video without audio: {mute_path.name}"})
                    return f"RedNote mute: {mute_path}"
                send_fn(job_id, {"type": "log", "message": "Mute processing failed, keeping original video."})

            if crop:
                send_fn(job_id, {"type": "log", "message": "Cropping watermark..."})
                clean_path = crop_watermark(video_path)
                if clean_path:
                    try:
                        video_path.unlink()
                    except Exception:
                        pass
                    send_fn(job_id, {"type": "log", "message": f"RedNote clean: {clean_path.name}"})
                    return f"RedNote clean video: {clean_path}"
                send_fn(job_id, {"type": "log", "message": "Crop failed, keeping original"})
                return f"RedNote (crop failed): {video_path}"
            send_fn(job_id, {"type": "log", "message": f"RedNote: {video_path.name}"})
            return f"RedNote: {video_path}"
    except Exception as e:
        send_fn(job_id, {"type": "log", "message": f"RedNote failed: {str(e)}"})
        return f"RedNote failed: {url}\n   {e}"


def process_url_with_progress(url, output, cookies, quality, crop, job_id, send_fn, download_mode="video_audio"):
    url_type = detect_url_type(url)
    if url_type == "youtube_video":
        return download_ytdlp_with_progress(url, output, quality, "YouTube", job_id, send_fn, download_mode)
    if url_type == "tiktok_video":
        return download_ytdlp_with_progress(url, output, quality, "TikTok", job_id, send_fn, download_mode)
    if url_type == "rednote_video":
        return download_rednote_with_progress(url, output, cookies, crop, quality, job_id, send_fn, download_mode)
    if url_type == "rednote_profile_unsupported":
        msg = get_profile_error(url)
        send_fn(job_id, {"type": "log", "message": f"❌ {msg}"})
        return f"❌ {msg}"
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


def start_download(urls, output, quality, crop, cookies, job_id, download_mode="video_audio"):
    job = get_job(job_id)
    if not job:
        return

    job["status"] = "running"

    def send(job_id, msg):
        with _jobs_lock:
            j = _jobs.get(job_id)
            if j:
                j["queue"].append(msg)

    mode_labels = {"video_audio": "Video + Audio", "audio_mp3": "Audio only MP3", "video_mute": "Video only mute"}
    send(job_id, {"type": "log", "message": f"Starting download ({len(urls)} items)"})
    send(job_id, {"type": "log", "message": f"Output: {output}"})
    send(job_id, {"type": "log", "message": f"Quality: {quality}p"})
    send(job_id, {"type": "log", "message": f"Download mode: {mode_labels.get(download_mode, 'Video + Audio')}"})

    def process_one(url):
        result = process_url_with_progress(url, output, cookies, quality, crop, job_id, send, download_mode)
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
            j["queue"].append({"type": "log", "message": "\nAll downloads finished!"})
