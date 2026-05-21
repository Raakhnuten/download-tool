# OpenCode Autonomous Upgrade Plan — FastAPI + Pure HTML/CSS/JS Downloader

Use this file as the main instruction for OpenCode.

## Project Stack

This project uses:

- Python backend
- FastAPI
- Uvicorn
- yt_dlp
- httpx
- Jinja2 templates
- Pure HTML/CSS/JavaScript frontend
- No frontend framework
- No frontend build tools
- SSE / Server-Sent Events for real-time streaming
- ReadableStream for streaming scan results
- FFmpeg for RedNote watermark cropping

Important project files may include:

```text
core.py
web.py
download.py
templates/index.html
.gitignore
ffmpeg.exe
```

The frontend is probably mostly inside:

```text
templates/index.html
```

Do not convert this project to React, Vue, Angular, Vite, or any frontend framework.

Keep it simple: FastAPI + Jinja2 + pure HTML/CSS/JS.

---

## Mission

Act as an autonomous senior full-stack engineer.

Polish, upgrade, and fix the downloader UI and user experience while preserving the existing backend scan/download logic.

The app should feel modern, clean, professional, and smooth on laptop/desktop.

Primary goals:

1. Polish the UI.
2. Improve laptop layout.
3. Improve video cards.
4. Improve scanning progress.
5. Ensure scan results appear one-by-one if the existing SSE/streaming logic supports it.
6. Improve selection and download states.
7. Improve error/loading/empty states.
8. Fix frontend bugs.
9. Fix Python/FastAPI errors if they appear.
10. Keep the app working.

---

## Very Important Rules

1. Do not rewrite the whole app.
2. Do not add React, Vue, Angular, Svelte, or other frontend frameworks.
3. Do not add Tailwind unless the project already uses it.
4. Do not add unnecessary npm tooling.
5. Keep pure HTML/CSS/JS.
6. Keep the existing FastAPI backend.
7. Keep the existing Jinja2 template approach.
8. Keep existing scan and download logic.
9. Keep existing SSE / streaming behavior.
10. If streaming is broken, fix it carefully.
11. Do not remove yt_dlp logic.
12. Do not remove RedNote scraper logic.
13. Do not remove ffmpeg cropping logic.
14. Do not break cookie upload behavior.
15. Do not break output folder behavior.
16. Do not break quality selection.
17. Do not hardcode fake results.
18. Work autonomously.
19. Run checks after changes.
20. Fix errors until the app runs.

---

## Step 1: Inspect First

Inspect the project before editing.

Check:

```text
core.py
web.py
download.py
templates/index.html
requirements.txt
pyproject.toml
README.md
```

Also check if these exist:

```text
static/
templates/
downloads/
cookies.txt
```

Understand:

- FastAPI routes
- Scan endpoint
- Download endpoint
- SSE endpoint
- Streaming response endpoint
- How results are sent to frontend
- How video objects are shaped
- How selection works in JavaScript
- How download jobs work
- How logs/progress are displayed
- How RedNote cropping is triggered

Do not guess. Use the actual code.

---

## Step 2: Run Current App

Find the correct way to start the app.

Likely command:

```bash
python web.py
```

or:

```bash
uvicorn web:app --reload
```

Also try syntax checks:

```bash
python -m py_compile core.py web.py download.py
```

If there is a `requirements.txt`, make sure dependencies are installed:

```bash
pip install -r requirements.txt
```

If no requirements file exists, do not create a huge dependency system. Only add one if useful and safe.

---

## Step 3: Create Safe Backup Before Large Edit

Before editing `templates/index.html`, create a backup copy:

```text
templates/index.backup.html
```

Only do this once.

Do not create many backup files.

---

## Step 4: UI Design Target

Keep the current dark theme, but polish it.

Use a Catppuccin Mocha-inspired professional palette.

CSS variables should be used inside `templates/index.html` or the existing CSS location.

Suggested variables:

```css
:root {
  --bg: #11111b;
  --bg-soft: #181825;
  --surface: #1e1e2e;
  --surface-2: #242438;
  --card: #242438;
  --card-hover: #2a2b45;
  --border: #313244;
  --border-strong: #45475a;

  --text: #cdd6f4;
  --text-strong: #f5f7ff;
  --muted: #a6adc8;
  --muted-2: #7f849c;

  --primary: #89b4fa;
  --primary-strong: #74a8fc;
  --primary-soft: rgba(137, 180, 250, 0.16);

  --success: #a6e3a1;
  --warning: #f9e2af;
  --danger: #f38ba8;
  --purple: #cba6f7;

  --radius-sm: 8px;
  --radius-md: 12px;
  --radius-lg: 16px;
  --shadow-soft: 0 14px 36px rgba(0, 0, 0, 0.28);
}
```

---

## Step 5: Laptop Layout Polish

The app is used only on laptop/desktop.

Improve layout:

- Sidebar width: 260px to 280px.
- Main content fills remaining width.
- Top toolbar sticky.
- Results grid uses available width.
- Normal laptop: 4 columns.
- Large screen: 5 to 6 columns.
- Card gap: 14px to 18px.
- Main padding: 16px to 20px.
- Avoid huge empty spaces.
- Avoid cramped text.
- Avoid too-dark unreadable cards.

CSS grid suggestion:

```css
.results-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
  gap: 16px;
}
```

For very wide screens, prevent cards becoming too wide:

```css
.results-grid {
  grid-template-columns: repeat(auto-fill, minmax(220px, 240px));
}
```

Choose the best version based on the existing UI.

---

## Step 6: Add Collapsible Sidebar

Add a laptop-friendly sidebar toggle.

Requirements:

- Button in top toolbar or sidebar header.
- Hide/show settings sidebar.
- Main content expands when hidden.
- State can be kept in JavaScript memory.
- Optional: save state to `localStorage`.

Labels:

```text
Hide Settings
Show Settings
```

Do not break existing settings inputs.

---

## Step 7: Polish Top Toolbar

Improve the top URL toolbar.

Requirements:

- Sticky at top.
- Clean border bottom.
- Better spacing.
- URL input fills available space.
- Better focus ring.
- Better placeholder:

```text
Paste YouTube Shorts / TikTok / RedNote profile or video URL...
```

Buttons:

- Scan button primary.
- Download button secondary/primary.
- Scan button shows loading state while scanning:

```text
Scanning...
```

- Download button disabled when no selected videos.
- Download button text includes selected count:

```text
Download Selected (3)
```

When selected count is zero:

```text
Download Selected
```

but disabled.

---

## Step 8: Polish Sidebar

Improve sidebar sections.

Sections:

1. Download Settings
2. Authentication
3. Quick Tips

Each section should have:

- Clear uppercase or small-label heading.
- Better spacing.
- Better input/select styling.
- Better Browse button.
- Better checkbox alignment.
- Clear helper text.

Cookie status:

```text
Cookies: Loaded
```

or:

```text
Cookies: Not loaded
```

Use success color when loaded.

Use warning/muted color when not loaded.

Do not remove the cookie file input.

---

## Step 9: Polish Tabs

Current tabs:

```text
Results
Progress
Log
```

Improve them:

- Active tab underline.
- Hover state.
- Better spacing.
- Optional count badge.

Suggested:

```text
Results 176
Progress
Log
```

Only add `Errors` tab if the data already exists or can be safely derived.

Do not break current tab switching.

---

## Step 10: Results Header

Improve the result header.

Show a clean summary row:

```text
176 videos found
0 selected
0 failed
0 downloaded
```

Use small pills/badges.

Update these counts dynamically in JavaScript.

---

## Step 11: Add Search, Filter, Sort

Above the video grid, add:

Search input:

```text
Search scanned videos...
```

Filter buttons:

```text
All
Selected
Downloaded
Failed
```

Sort dropdown:

```text
Newest
Oldest
Title A-Z
```

Behavior:

- Search filters by title.
- All shows all videos.
- Selected shows selected videos.
- Downloaded shows downloaded videos if status exists.
- Failed shows failed videos if failed status exists.
- If downloaded/failed status does not exist, safely show empty state.
- Sort should not mutate original results unexpectedly.
- Keep original results array and render derived filtered results.

Use simple vanilla JS.

Do not add a frontend framework.

---

## Step 12: Video Card Polish

Improve cards.

Each video card should have:

- Thumbnail area
- Title area
- Optional platform/source badge
- Optional duration badge
- Selection state
- Failed thumbnail fallback

Card normal:

- Soft dark background
- Soft border
- Rounded corners

Card hover:

- Slight lift
- Brighter border
- Soft shadow

Card selected:

- Bright primary border
- Check icon top-right
- Subtle selected overlay

Title:

- Font size around 13px to 14px.
- Line height around 1.35 to 1.45.
- Clamp to 2 lines.

CSS example:

```css
.video-title {
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
```

Thumbnail:

- Consistent height.
- Use `object-fit: cover`.
- Handle failed image loading with JS or CSS fallback.

---

## Step 13: Streaming Scan Results

The project already uses SSE and ReadableStream.

Make sure scan results feel real-time.

Expected behavior:

- User clicks Scan.
- Progress starts immediately.
- Results appear one-by-one when each video is scanned.
- UI does not wait for all videos before showing results.
- Counts update live.
- Progress tab/log updates live.

If the backend already sends streaming events:

- Fix frontend rendering so each event appends a card immediately.
- Do not buffer all results until the end.

If the frontend uses `ReadableStream`:

- Parse chunks safely.
- Handle partial JSON lines.
- Append each valid result as soon as it arrives.

If using SSE:

- Use `EventSource` or existing SSE logic.
- Handle event types:
  - result
  - progress
  - log
  - error
  - done

Do not break existing stream protocol. Inspect current code first.

---

## Step 14: Progress UX

Improve progress display.

Show:

```text
Scanning 42 / 176
Found 40
Failed 2
Selected 0
```

Add progress bar.

Progress should update live.

If total is unknown:

```text
Scanning...
Found 40
```

Do not show fake total.

---

## Step 15: Toast Notifications

Add simple vanilla JS toast notifications if not already present.

Events:

- Scan started
- Scan completed
- Scan failed
- Download started
- Download completed
- Download failed
- Cookies loaded

Toast design:

- Top-right.
- Dark card.
- Success/error/info states.
- Auto dismiss.
- Max 3 visible at a time.

No external library.

---

## Step 16: Empty, Loading, Error States

Add polished states.

Empty:

```text
No videos scanned yet.
Paste a URL and click Scan to begin.
```

Scanning:

```text
Scanning videos...
Results will appear here one by one.
```

No search/filter result:

```text
No videos match your current filter.
```

Thumbnail failed:

```text
Could not load thumbnail
```

Download disabled helper:

```text
Select at least one video to download.
```

---

## Step 17: Python Backend Safety

Only change Python backend if needed.

Allowed backend fixes:

- SSE events not flushing.
- Streaming response buffering.
- Wrong content type for SSE.
- Missing error handling.
- Bad JSON serialization.
- Blocking scan causing UI to wait until finished.
- Download endpoint returning unclear errors.
- Progress endpoint not updating.
- CORS issue if relevant.
- File path issue if relevant.

FastAPI SSE headers should be similar to:

```python
return StreamingResponse(
    event_generator(),
    media_type="text/event-stream",
    headers={
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    },
)
```

Yield events often.

Use this format for SSE:

```text
event: result
data: {"title":"...", "url":"..."}

```

Keep compatibility with existing frontend.

Do not rewrite yt_dlp extraction unless needed.

---

## Step 18: Download UX

Improve selected download behavior.

Requirements:

- Download button disabled when no selected videos.
- Selected count updates instantly.
- Card selection is obvious.
- Select All and Deselect All buttons still work.
- Add or polish sticky selected action bar if useful:

```text
3 selected    Select All    Deselect All    Download Selected
```

Do not break existing download endpoint.

---

## Step 19: Logs

Improve Log tab readability.

Requirements:

- Monospace font.
- Better contrast.
- Auto-scroll to bottom while scanning.
- Keep existing log messages.
- Add timestamps if simple and useful.
- Error messages use danger color.

Do not spam duplicate logs.

---

## Step 20: Code Quality

For `templates/index.html`:

- Keep HTML sections organized.
- Keep CSS variables at top.
- Group CSS by layout/sidebar/toolbar/cards/tabs/toasts.
- Group JS by state/render/events/api/helpers.
- Avoid duplicate functions.
- Avoid unused variables.
- Avoid global pollution when easy.
- Keep function names readable.

For Python:

- Keep functions small.
- Do not mix unrelated concerns.
- Add try/except around risky download/scan operations if needed.
- Return clear JSON errors.

---

## Step 21: Verification Commands

Run syntax checks:

```bash
python -m py_compile core.py web.py download.py
```

Run the app:

```bash
uvicorn web:app --reload
```

or the correct command for this project.

If possible, test in browser:

```text
http://127.0.0.1:8000
```

Check:

- Page loads.
- URL input works.
- Scan button works.
- Results appear.
- Results can be selected.
- Download button state works.
- Sidebar inputs still work.
- Cookie upload still works.
- Progress/log tabs still work.
- No console errors.
- No Python traceback.

---

## Step 22: Build/Fix Loop

Repeat until clean:

1. Run Python compile check.
2. Run app.
3. Fix backend errors.
4. Test frontend.
5. Fix browser console errors.
6. Re-test.

Do not stop after first error.

---

## Step 23: Final Report

When complete, reply with this format:

```md
# Downloader UI Upgrade Report

## Completed
- Polished laptop/desktop layout
- Improved dark theme
- Improved sidebar
- Improved toolbar
- Improved tabs
- Improved video cards
- Added search/filter/sort
- Improved selected state
- Improved scan progress
- Improved toast notifications
- Improved empty/loading/error states
- Preserved FastAPI + pure HTML/CSS/JS stack
- Preserved existing scan/download logic

## Files Changed
- templates/index.html
- web.py
- core.py
- download.py

## Checks
- Python compile: passed
- App run: passed
- UI checked: passed

## Notes
- Mention anything important here
```

---

## Start Now

Inspect the project.

Upgrade the UI.

Fix scan streaming if needed.

Fix all errors.

Keep FastAPI + pure HTML/CSS/JS.

Do not stop until the app runs cleanly.
