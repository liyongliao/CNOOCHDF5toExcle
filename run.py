"""Source launcher. Packaged releases use the desktop-only desktop_entry.py."""
from __future__ import annotations
import sys
import time
from desktop_entry import main as desktop_main

def run_web():
    """Optional browser interface retained for existing users."""
    import socket
    import threading
    import urllib.request
    import webbrowser
    import app as backend
    import uvicorn

    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    port = listener.getsockname()[1]
    url = "http://127.0.0.1:{}".format(port)
    server = uvicorn.Server(uvicorn.Config(backend.app, host="127.0.0.1", port=port,
                                         log_level="warning", log_config=None,
                                         loop="asyncio", http="h11", ws="none"))

    def open_when_ready():
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(url, timeout=0.25) as response:
                    if response.status == 200:
                        webbrowser.open(url)
                        return
            except (OSError, TimeoutError):
                time.sleep(0.05)

    threading.Thread(target=open_when_ready, daemon=True).start()
    try:
        server.run(sockets=[listener])
    finally:
        listener.close()


def main(argv=None):
    arguments = list(sys.argv[1:] if argv is None else argv)
    if "--web" in arguments and "--help" not in arguments and "-h" not in arguments:
        if arguments != ["--web"]:
            raise SystemExit("--web 不能与桌面验证参数混用")
        run_web()
        return 0
    if "--help" in arguments or "-h" in arguments:
        print("可选网页界面：--web（需安装 requirements-web.txt）")
    return desktop_main(arguments)


if __name__ == "__main__":
    sys.exit(main())
