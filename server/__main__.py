"""Entry point: python -m server  (start_hidden.vbs runs this with pythonw).

Keystrokes:
    --no-tray      skip the tray icon (plain window / service mode)
    --port N       override PORT
    --open         open the dashboard in the default browser once ready
    --install-autostart / --remove-autostart   manage "start with Windows"
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import threading
import time
import webbrowser
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.config import settings  # noqa: E402
from server import app  # noqa: E402

STARTUP_SCRIPT = f'wscript.exe "{PROJECT_ROOT / "start_hidden.vbs"}"'


def configure_logging() -> None:
    handlers: list[logging.Handler] = []
    if sys.stdout is not None and sys.stdout.isatty():
        handlers.append(logging.StreamHandler(sys.stdout))
    try:
        from logging.handlers import RotatingFileHandler

        log_dir = settings.temp_dir.parent / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        handlers.append(RotatingFileHandler(
            log_dir / "server.log", maxBytes=1_000_000, backupCount=3,
            encoding="utf-8"))
    except OSError:
        pass
    logging.basicConfig(
        level=getattr(logging, settings.log_level, logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        handlers=handlers,
        force=True,
    )


def autostart(enable: bool) -> tuple[bool, str]:
    """Toggle a Run-registry entry so the server starts with Windows."""
    if sys.platform != "win32":
        return False, "Autostart is only wired up for Windows."
    try:
        import winreg
    except ImportError:
        return False, "winreg unavailable."

    key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
    name = "UniversalDownload"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as key:
            if enable:
                winreg.SetValueEx(key, name, 0, winreg.REG_SZ, str(STARTUP_SCRIPT))
                return True, "UniversalDownload will start with Windows."
            try:
                winreg.DeleteValue(key, name)
            except FileNotFoundError:
                return True, "Autostart was already off."
            return True, "Autostart removed."
    except OSError as exc:
        return False, f"Registry write failed: {exc}"


def tray_loop(app_module: app) -> None:
    """System-tray presence so the background server is never a mystery."""
    try:
        import pystray
        from PIL import Image, ImageDraw
    except ImportError:
        logging.getLogger("ud").info("tray unavailable (pip install pystray pillow)")
        return

    image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((4, 12, 60, 52), radius=10, fill=(59, 130, 246, 255))
    draw.polygon([(20, 22), (20, 42), (42, 32)], fill=(255, 255, 255, 255))

    def on_quit(_icon, _item) -> None:
        app_module.shutdown()
        icon.stop()

    menu = pystray.Menu(
        pystray.MenuItem(
            "Open dashboard",
            lambda: webbrowser.open(f"http://{settings.host}:{settings.port}/")),
        pystray.MenuItem(
            "Show downloads folder",
            lambda: os.startfile(str(app_module.READY_DIR))
            if app_module.READY_DIR.exists() else None),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit", on_quit),
    )
    icon = pystray.Icon("universaldownload", image, "UniversalDownload", menu)
    threading.Thread(target=icon.run, daemon=True).start()
    logging.getLogger("ud").info("tray icon running")


def main() -> int:
    parser = argparse.ArgumentParser(prog="universaldownload")
    parser.add_argument("--no-tray", action="store_true", help="skip the tray icon")
    parser.add_argument("--port", type=int, default=None, help="override the listen port")
    parser.add_argument("--open", action="store_true", help="open the dashboard when ready")
    parser.add_argument("--install-autostart", action="store_true")
    parser.add_argument("--remove-autostart", action="store_true")
    args = parser.parse_args()

    if args.install_autostart or args.remove_autostart:
        ok, message = autostart(args.install_autostart)
        print(("OK: " if ok else "FAILED: ") + message)
        return 0 if ok else 1

    if args.port:
        object.__setattr__(settings, "port", args.port)

    configure_logging()
    log = logging.getLogger("ud")

    settings.temp_dir.mkdir(parents=True, exist_ok=True)
    threading.Thread(target=app.serve_forever, daemon=True).start()

    deadline = time.time() + 10
    while time.time() < deadline and app.SERVER is None:
        time.sleep(0.2)
    if app.SERVER is None:
        log.error("server failed to bind %s:%s", settings.host, settings.port)
        return 1

    log.info("ready on http://%s:%d  (temp: %s, ffprobe: %s)",
             settings.host, settings.port, settings.temp_dir, settings.ffprobe)
    if settings.open_browser_on_start or args.open:
        webbrowser.open(f"http://{settings.host}:{settings.port}/")

    if settings.tray and not args.no_tray:
        tray_loop(app)

    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        log.info("interrupted, shutting down")
    finally:
        app.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
