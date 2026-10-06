"""Configuration for the UniversalDownload local server.

Everything is read from the environment, with an optional .env file next to the
project root. Defaults are chosen so the server works with zero configuration.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(os.getenv("UD_PROJECT_ROOT") or Path(__file__).resolve().parent.parent)
load_dotenv(PROJECT_ROOT / ".env")

MB = 1024 * 1024


def _str(key: str, default: str = "") -> str:
    value = os.getenv(key)
    return default if value is None else value.strip()


def _int(key: str, default: int) -> int:
    raw = _str(key)
    if not raw:
        return default
    try:
        return int(float(raw))
    except ValueError:
        return default


def _bool(key: str, default: bool) -> bool:
    raw = _str(key).lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "y", "on", "enable", "enabled"}


def _path(key: str, default: Path) -> Path:
    raw = _str(key)
    candidate = Path(raw) if raw else default
    if not candidate.is_absolute():
        candidate = PROJECT_ROOT / candidate
    return candidate


def _resolve_binary(explicit: str, name: str) -> str:
    if explicit:
        return explicit
    found = shutil.which(name)
    if found:
        return found
    # ffmpeg is often unpacked into a user folder rather than installed on PATH.
    for folder in (Path.home() / "Documents" / "ffmpeg" / "bin",
                   Path.home() / "Downloads" / "ffmpeg" / "bin",
                   Path("C:/ffmpeg/bin")):
        candidate = folder / f"{name}.exe"
        if candidate.exists():
            return str(candidate)
    return name


@dataclass(frozen=True)
class Settings:
    host: str
    port: int

    temp_dir: Path
    keep_temp_seconds: int
    max_history: int

    max_height: int
    max_download_bytes: int
    max_concurrent_jobs: int
    job_timeout: int
    retries: int

    cookies_file: Path | None = None
    cookies_from_browser: str | None = None
    impersonate: str | None = None
    ffmpeg: str = "ffmpeg"
    ffprobe: str = "ffprobe"
    min_free_disk_bytes: int = 500 * MB

    tray: bool = True
    open_browser_on_start: bool = False
    log_level: str = "INFO"
    extra_ytdlp_args: list[str] = field(default_factory=list)


def load_settings() -> Settings:
    cookies_raw = _str("COOKIES_FILE")
    cookies = _path("COOKIES_FILE", PROJECT_ROOT / "cookies.txt") if cookies_raw else None
    if cookies is not None and not cookies.exists():
        cookies = None

    browser_cookies = _str("COOKIES_FROM_BROWSER") or None
    if browser_cookies and browser_cookies.lower() in {"none", "off", "false", "0"}:
        browser_cookies = None

    impersonate = _str("IMPERSONATE") or "chrome"
    if impersonate.lower() in {"none", "off", "false", "0"}:
        impersonate = None
    else:
        try:
            import curl_cffi  # noqa: F401
        except ImportError:
            impersonate = None

    extra = [a for a in _str("EXTRA_YTDLP_ARGS").split() if a]

    return Settings(
        host=_str("HOST", "127.0.0.1"),
        port=_int("PORT", 8756),
        temp_dir=_path("TEMP_DIR", PROJECT_ROOT / "temp"),
        keep_temp_seconds=_int("KEEP_TEMP_SECONDS", 900),
        max_history=_int("MAX_HISTORY", 50),
        max_height=_int("MAX_HEIGHT", 1080),
        max_download_bytes=_int("MAX_DOWNLOAD_MB", 2000) * MB,
        max_concurrent_jobs=max(1, _int("MAX_CONCURRENT_JOBS", 2)),
        job_timeout=_int("JOB_TIMEOUT_SECONDS", 1800),
        retries=max(1, _int("RETRIES", 3)),
        cookies_file=cookies,
        cookies_from_browser=browser_cookies,
        impersonate=impersonate,
        ffmpeg=_resolve_binary(_str("FFMPEG_BIN"), "ffmpeg"),
        ffprobe=_resolve_binary(_str("FFPROBE_BIN"), "ffprobe"),
        min_free_disk_bytes=_int("MIN_FREE_DISK_MB", 500) * MB,
        tray=_bool("TRAY", True),
        open_browser_on_start=_bool("OPEN_BROWSER_ON_START", False),
        log_level=_str("LOG_LEVEL", "INFO").upper(),
        extra_ytdlp_args=extra,
    )


settings = load_settings()
