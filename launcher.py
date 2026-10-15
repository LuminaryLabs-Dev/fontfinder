from __future__ import annotations

import multiprocessing
import socket
import threading
import time
import urllib.error
import urllib.request
import webbrowser

import uvicorn

from backend.app import app


def _free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _open_browser_when_ready(port: int) -> None:
    url = f"http://127.0.0.1:{port}/"
    for _ in range(60):
        try:
            with urllib.request.urlopen(f"{url}api/health", timeout=1) as response:
                if response.status == 200:
                    webbrowser.open(url)
                    return
        except (OSError, urllib.error.URLError):
            time.sleep(0.5)


def main() -> None:
    multiprocessing.freeze_support()
    port = _free_port()
    threading.Thread(target=_open_browser_when_ready, args=(port,), daemon=True).start()
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
