@echo off
chcp 65001 >nul
echo === Building DownloaderTool EXE ===
echo.

pyinstaller --onedir ^
  --name DownloaderTool ^
  --add-data "templates;templates" ^
  --add-data "image;image" ^
  --add-data "tools/ffmpeg/ffmpeg.exe;tools/ffmpeg" ^
  --hidden-import uvicorn.logging ^
  --hidden-import uvicorn.loops.auto ^
  --hidden-import uvicorn.protocols.http.auto ^
  --hidden-import uvicorn.protocols.websockets.auto ^
  --clean ^
  --noconfirm ^
  launcher.py

if %errorlevel% neq 0 (
    echo.
    echo [ERROR] PyInstaller build failed!
    pause
    exit /b 1
)

echo.
echo === Build complete! ===
echo Output: dist\DownloaderTool\DownloaderTool.exe
echo.
pause
