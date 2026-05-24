#!/usr/bin/env python3

import json
import re
import html
import subprocess
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import tkinter as tk
from tkinter import filedialog

import click
import httpx
import yt_dlp
from tqdm import tqdm
import sys
import os

def get_ffmpeg_path():
    if getattr(sys, 'frozen', False):
        return os.path.join(sys._MEIPASS, "ffmpeg.exe")
    return "ffmpeg"
DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

NOTE_ID_RE = re.compile(
    r"/(?:explore|discovery/item|item)/([0-9a-fA-F]{16,32})"
)

REDNOTE_NOTE_HREF_RE = re.compile(
    r'href\s*=\s*["\'](?:https?://(?:www\.)?(?:rednote\.com|xiaohongshu\.com))?/explore/([0-9a-fA-F]{16,32})["\']'
)

REDNOTE_NOTE_ID_IN_JSON_RE = re.compile(
    r'"note_id"\s*:\s*"([0-9a-fA-F]{16,32})"'
)

REDNOTE_DISPLAY_TITLE_RE = re.compile(
    r'"display_title"\s*:\s*"((?:[^"\\]|\\.)*)"'
)

REDNOTE_COVER_URL_RE = re.compile(
    r'"cover"\s*:\s*\{[^}]*"url"\s*:\s*"((?:[^"\\]|\\.)*)"'
)

REDNOTE_NOTE_TYPE_RE = re.compile(
    r'"type"\s*:\s*"(video|normal|audio)"'
)

INITIAL_STATE_RE = re.compile(
    r"window\.__INITIAL_STATE__\s*=\s*(\{.*?\})\s*</script>",
    re.DOTALL
)

INVALID_FS = re.compile(r'[\\/:*?"<>|\r\n\t]+')


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


def is_rednote(url):
    return "xiaohongshu" in url.lower() or "xhslink" in url.lower() or "rednote.com" in url.lower()


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


def is_youtube(url):
    return "youtube.com" in url.lower() or "youtu.be" in url.lower()


def is_tiktok(url):
    return "tiktok.com" in url.lower()


def choose_output_folder():
    try:
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)

        folder = filedialog.askdirectory(
            title="Select Download Folder",
            parent=root
        )

        root.destroy()

        if folder:
            return Path(folder)

    except Exception:
        pass

    print("\nEnter output folder path or press Enter for default downloads:")
    path = input("> ").strip()

    if path:
        return Path(path)

    return Path("downloads")


def choose_quality():
    print("\nChoose quality:")
    print("1. 480p")
    print("2. 720p")
    print("3. 1080p")
    print("4. Best")

    choice = input("Enter: ").strip()

    if choice == "1":
        return 480
    if choice == "2":
        return 720
    if choice == "3":
        return 1080

    return None


def get_urls_from_cli():
    print("\nPaste links below.")
    print("Supports RedNote, YouTube, TikTok.")
    print("Paste one per line or many separated by space.")
    print("Type DONE when finished.\n")

    urls = []

    while True:
        line = input("> ").strip()

        if line.upper() == "DONE":
            break

        if line:
            urls.extend(line.split())

    return urls


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
                    "url": f"https://www.youtube.com/watch?v={video_id}"
                })

    collect_entries(info)
    return videos


def select_videos(videos):
    total = len(videos)
    page_size = 20
    page = 0

    while True:
        start = page * page_size
        end = start + page_size
        chunk = videos[start:end]

        print(f"\nShowing {start + 1} - {min(end, total)} of {total}\n")

        for i, video in enumerate(chunk, start=start + 1):
            print(f"[{i}] {video['title'][:70]}")
            print(f"    {video['url']}")

        print("\nOptions:")
        print("next  → next page")
        print("prev  → previous page")
        print("all   → download all")
        print("1,3,5 or 1-10 → select videos")

        choice = input("Your choice: ").strip().lower()

        if choice == "next":
            if end < total:
                page += 1
            else:
                print("Already last page.")
            continue

        if choice == "prev":
            if page > 0:
                page -= 1
            else:
                print("Already first page.")
            continue

        if choice == "all":
            return [video["url"] for video in videos]

        selected = set()

        for part in choice.split(","):
            part = part.strip()

            if "-" in part:
                try:
                    start_i, end_i = map(int, part.split("-"))
                    selected.update(range(start_i, end_i + 1))
                except Exception:
                    pass

            elif part.isdigit():
                selected.add(int(part))

        if selected:
            return [
                videos[i - 1]["url"]
                for i in sorted(selected)
                if 1 <= i <= total
            ]

        print("Invalid input.")


def ytdlp_format(quality):
    quality = str(quality or "720")
    if quality == "1080":
        return "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[height<=1080]/best"
    if quality == "720":
        return "bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/best[height<=720]/best"
    if quality == "480":
        return "bestvideo[height<=480][ext=mp4]+bestaudio[ext=m4a]/best[height<=480]/best"
    return "bestvideo*+bestaudio/best"


def download_ytdlp(url, output, quality, platform_name):
    try:
        folder = output / platform_name
        folder.mkdir(parents=True, exist_ok=True)

        opts = {
            "format": ytdlp_format(quality),
            "outtmpl": f"{folder}/%(title).80s [%(id)s].%(ext)s",
            "merge_output_format": "mp4",
            "quiet": True,
            "continuedl": True,
            "retries": 10,
            "fragment_retries": 10,
        }
        if Path("cookies.txt").exists():
            opts["cookiefile"] = "cookies.txt"

        from core import find_ffmpeg
        ffmpeg_path = find_ffmpeg()
        if ffmpeg_path:
            opts["ffmpeg_location"] = ffmpeg_path

        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([url])

        return f"✅ {platform_name}: {url}"
    except Exception as e:
        return f"❌ {platform_name} failed: {url}\n   {e}"


def extract_note_id(url):
    match = NOTE_ID_RE.search(url)
    return match.group(1) if match else None


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

    # Strategy 5: imageList with video type
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

    # Strategy 6: noteCard.videoUrl
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


def find_rednote_video(state, note_id):
    # Try to find the note in noteDetailMap
    note = None
    note_map = state.get("note", {}).get("noteDetailMap", {})

    if note_id and note_id in note_map:
        entry = note_map[note_id]
        note = entry.get("note") if isinstance(entry, dict) else None
    elif note_map:
        first_key = next(iter(note_map))
        entry = note_map[first_key]
        note = entry.get("note") if isinstance(entry, dict) else None

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

    if not note:
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

    # Extract video URLs using multiple strategies
    candidates = _extract_video_urls_from_note(note)

    if candidates:
        for codec, url in candidates:
            if codec in ("h264", "h265", "av1", "direct", "media_videoUrl"):
                return title, url
        return title, candidates[0][1]

    return None


def get_rednote_info(client, url):
    note_id = extract_note_id(url)

    if not note_id:
        raise Exception(f"Invalid RedNote URL. Could not extract note_id from: {url}")

    headers = _rednote_headers(url)

    response = client.get(
        url,
        headers=headers,
        follow_redirects=True,
        timeout=30
    )

    if response.status_code != 200:
        raise Exception(f"HTTP error: {response.status_code}")

    text = response.text

    # Check for login-required page
    if "login" in text.lower() and "sign in" in text.lower() and len(text) < 50000:
        pass  # Will likely fail below, but let the normal error handle it

    match = INITIAL_STATE_RE.search(text)

    if not match:
        alt_patterns = [
            re.compile(r"__INITIAL_STATE__\s*=\s*(\{.*?\})\s*;\s*</script>", re.DOTALL),
            re.compile(r"window\.__INITIAL_STATE__\s*=\s*(\{.*?\})\s*;\s*", re.DOTALL),
        ]
        for pat in alt_patterns:
            match = pat.search(text)
            if match:
                break

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


def download_rednote_file(client, url, path):
    headers = _rednote_headers(url)

    temp_path = path.with_suffix(path.suffix + ".part")

    with client.stream("GET", url, headers=headers, timeout=60) as response:
        response.raise_for_status()

        total = int(response.headers.get("content-length", 0))

        with open(temp_path, "wb") as f, tqdm(
            total=total or None,
            unit="B",
            unit_scale=True,
            unit_divisor=1024,
            desc=path.name[:35],
        ) as bar:
            for chunk in response.iter_bytes(chunk_size=1024 * 256):
                if chunk:
                    f.write(chunk)
                    bar.update(len(chunk))

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

    result = subprocess.run(
        command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    if result.returncode != 0:
        return None

    return clean_path


def download_rednote(url, output, cookies, crop):
    try:
        folder = output / "RedNote"
        folder.mkdir(parents=True, exist_ok=True)

        with httpx.Client(http2=False, cookies=cookies) as client:
            name, video_url = get_rednote_info(client, url)

            video_path = folder / f"{name}.mp4"

            download_rednote_file(client, video_url, video_path)

            if crop:
                clean_path = crop_watermark(video_path)

                if clean_path:
                    try:
                        video_path.unlink()
                    except Exception:
                        pass

                    return f"✅ RedNote clean video: {clean_path}"

                return f"✅ RedNote downloaded, crop failed: {video_path}"

            return f"✅ RedNote: {video_path}"

    except Exception as e:
        return f"❌ RedNote failed: {url}\n   {e}"


def process_url(url, output, cookies, quality, crop):
    if is_youtube(url):
        if "/@" in url or "/shorts" in url or "/videos" in url:
            videos = expand_youtube_list(url)

            if not videos:
                return "❌ No YouTube videos found."

            selected_links = select_videos(videos)

            if not selected_links:
                return "Skipped YouTube list."

            results = []

            for link in selected_links:
                results.append(download_ytdlp(link, output, quality, "YouTube"))

            return "\n".join(results)

        return download_ytdlp(url, output, quality, "YouTube")

    if is_tiktok(url):
        return download_ytdlp(url, output, quality, "TikTok")

    if is_rednote(url):
        return download_rednote(url, output, cookies, crop)

    return f"❌ Unsupported link: {url}"


def menu_choose_crop():
    print("\nCrop RedNote watermark?")
    print("1. Yes")
    print("2. No")

    choice = input("Enter: ").strip()
    return choice != "2"


def run_download(output, quality, crop, cookies):
    urls = get_urls_from_cli()

    if not urls:
        print("No links found.")
        return

    print(f"\nSaving to: {output.resolve()}")
    print(f"Quality: {quality if quality else 'best'}")
    print(f"RedNote crop: {crop}")
    print(f"Links: {len(urls)}\n")

    with ThreadPoolExecutor(max_workers=3) as executor:
        results = executor.map(
            lambda url: process_url(url, output, cookies, quality, crop),
            urls,
        )

        for result in results:
            print(result)

    print("\n✅ Download finished!")


@click.command()
@click.option("-c", "--cookies", default="cookies.txt")
def main(cookies):
    print("=== All-in-one Downloader Menu ===")
    print("RedNote: download + crop")
    print("YouTube/TikTok: download only")
    print("YouTube channel/shorts: list + select\n")

    cookie_dict = load_cookies(Path(cookies))

    if cookie_dict:
        print(f"✅ Loaded cookies: {cookies}")
    else:
        print("⚠️ No cookies loaded. RedNote may fail.")

    quality = None
    output = Path("downloads")
    crop = True

    while True:
        print("\n==============================")
        print("MENU")
        print("==============================")
        print(f"Current quality: {quality if quality else 'best'}")
        print(f"Current folder : {output.resolve()}")
        print(f"RedNote crop   : {crop}")
        print()
        print("1. Download now")
        print("2. Change quality")
        print("3. Select output folder")
        print("4. Toggle RedNote crop")
        print("5. Change all settings")
        print("6. Exit")

        choice = input("Choose: ").strip()

        if choice == "1":
            run_download(output, quality, crop, cookie_dict)

        elif choice == "2":
            quality = choose_quality()

        elif choice == "3":
            output = choose_output_folder()

        elif choice == "4":
            crop = not crop
            print(f"RedNote crop set to: {crop}")

        elif choice == "5":
            quality = choose_quality()
            output = choose_output_folder()
            crop = menu_choose_crop()

        elif choice == "6":
            print("Bye 👋")
            break

        else:
            print("Invalid choice.")


if __name__ == "__main__":
    main()