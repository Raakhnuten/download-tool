import sys
import os
import socket
import webbrowser
import threading
from pathlib import Path


def find_free_port(start=8000):
    port = start
    while port < 65535:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return port
        port += 1
    return 8000


def open_browser(port):
    webbrowser.open(f"http://127.0.0.1:{port}")


def main():
    base = Path(__file__).parent.resolve()
    os.environ["DOWNLOADER_BASE_DIR"] = str(base)

    from web import app

    port = find_free_port(8000)
    print(f"[launcher] Starting server on http://127.0.0.1:{port}")

    threading.Thread(target=open_browser, args=(port,), daemon=True).start()

    import uvicorn
    uvicorn.run(
        app,
        host="127.0.0.1",
        port=port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
