"""Download engine built on yt-dlp: metadata, retries, error mapping.

Transport- and UI-agnostic core of UniversalDownload.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import yt_dlp
from yt_dlp.utils import DownloadCancelled, DownloadError

from . import platforms
from .config import settings

log = logging.getLogger(__name__)

ProgressCallback = Callable[[dict[str, Any]], None]

# Folders yt-dlp should never try to write into.
_MEDIA_EXTS = (
    ".mp4", ".mkv", ".webm", ".mov", ".m4v", ".flv", ".mp3", ".m4a",
    ".opus", ".ogg", ".wav", ".flac", ".aac", ".jpg", ".jpeg", ".png",
    ".gif", ".webp", ".part",
)

# Ordered from best to worst; yt-dlp picks the first that resolves.
# H.264 is preferred first: Telegram clients play H.264 far more reliably than HEVC/VP9.
_FORMAT_STEMS = (
    "bv*[height<=%(h)d][ext=mp4][vcodec^=avc]+ba[ext=m4a]/b[height<=%(h)d][ext=mp4][vcodec^=avc]",
    "bv*[height<=%(h)d][ext=mp4]+ba[ext=m4a]/b[height<=%(h)d][ext=mp4]",
    "bv*[height<=%(h)d]+ba/b[height<=%(h)d]",
    "wv*[height<=%(h)d]+wa/w[height<=%(h)d]",
    "b[height<=%(h)d]",
    "bv*+ba/b",
)


class Cancelled(Exception):
    """Raised when a job is cancelled by the user."""


class DownloadFailure(RuntimeError):
    """User-facing download error with an actionable hint."""

    def __init__(self, message: str, hint: str = "", raw: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint
        self.raw = raw or message


@dataclass
class VideoMeta:
    url: str = ""
    canonical_url: str = ""
    platform: str = "Web"
    emoji: str = "\U0001F310"
    extractor: str = ""
    video_id: str = ""
    title: str = ""
    description: str = ""
    uploader: str = ""
    uploader_url: str = ""
    channel: str = ""
    duration: float | None = None
    upload_date: str = ""
    timestamp: int | None = None
    view_count: int | None = None
    like_count: int | None = None
    comment_count: int | None = None
    repost_count: int | None = None
    thumbnail: str | None = None
    width: int | None = None
    height: int | None = None
    fps: float | None = None
    ext: str = ""
    filesize: int | None = None
    is_live: bool = False
    wasm_removed: bool = False
    tags: list[str] = field(default_factory=list)

    @property
    def formatted_duration(self) -> str:
        total = int(self.duration or 0)
        if total <= 0:
            return "unknown"
        hours, rem = divmod(total, 3600)
        minutes, secs = divmod(rem, 60)
        return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


@dataclass
class DownloadResult:
    meta: VideoMeta
    path: Path
    workdir: Path

    def cleanup(self) -> None:
        shutil.rmtree(self.workdir, ignore_errors=True)


class _Cancel:
    """Thread-safe cancel flag shared with the job runner."""

    __slots__ = ("flag",)

    def __init__(self) -> None:
        self.flag = False

    def set_flag(self) -> None:
        self.flag = True

    def is_set(self) -> bool:
        return self.flag


def free_disk_bytes(path: Path) -> int:
    try:
        return shutil.disk_usage(path).free
    except OSError:
        return 1 << 62


# Uploading is not a constant-memory operation: python-telegram-bot hands the
# multipart body to httpx, which buffers it in RAM when the socket drains slower
# than the file is read. Measured on a 153 MB container: a 30 MB file cost
# +16 MB at 4 MB/s but +92 MB at 1.6 MB/s, i.e. up to ~3x the file size.
_UPLOAD_MEMORY_FACTOR = 3

_CGROUP_LIMITS = (
    (Path("/sys/fs/cgroup/memory.max"), Path("/sys/fs/cgroup/memory.current")),
    (Path("/sys/fs/cgroup/memory/memory.limit_in_bytes"),
     Path("/sys/fs/cgroup/memory/memory.usage_in_bytes")),
)


def memory_headroom() -> int | None:
    """Bytes left before the cgroup OOM-kills us, or None when unlimited."""
    for limit_path, usage_path in _CGROUP_LIMITS:
        try:
            limit = int(limit_path.read_text().strip())
            used = int(usage_path.read_text().strip())
        except (OSError, ValueError):
            continue
        if limit <= 0 or limit > (1 << 62):
            return None
        return max(0, limit - used)
    return None


def upload_fits(size_bytes: int, headroom: int | None = None) -> bool:
    """Whether a file of this size can realistically be uploaded right now."""
    free = memory_headroom() if headroom is None else headroom
    if free is None:
        return True
    return size_bytes * _UPLOAD_MEMORY_FACTOR <= free


def impersonation_target():
    """Resolve the configured browser fingerprint into a yt-dlp ImpersonateTarget.

    Returns None when impersonation is disabled or curl_cffi is unavailable,
    so the caller can simply skip the option instead of crashing.
    """
    if not settings.impersonate:
        return None
    try:
        from yt_dlp.networking.impersonate import ImpersonateTarget

        return ImpersonateTarget.from_str(settings.impersonate)
    except Exception as exc:  # noqa: BLE001
        log.warning("impersonation disabled (%s): %s", settings.impersonate, exc)
        return None


def build_ytdlp_opts(
    workdir: Path,
    height: int,
    progress: ProgressCallback | None,
    cancel: "_Cancel | None",
    audio_only: bool = False,
) -> dict[str, Any]:
    fmt = "ba[ext=m4a]/ba/b" if audio_only else "/".join(stem % {"h": height} for stem in _FORMAT_STEMS)
    opts: dict[str, Any] = {
        "outtmpl": {"default": str(workdir / "media.%(ext)s")},
        "format": fmt,
        "format_sort": ["res", "ext:mp4:m4a", "vcodec:h264", "size", "br"],
        "merge_output_format": "mp4",
        "noplaylist": True,
        "playlistend": 1,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "nocheckcertificate": False,
        "max_filesize": settings.max_download_bytes or None,
        "retries": 10,
        "fragment_retries": 10,
        "socket_timeout": 30,
        "concurrent_fragment_downloads": 4,
        "overwrites": True,
        "continuedl": True,
        "nopart": False,
        "ignoreerrors": False,
        "writethumbnail": False,
        "writeinfojson": False,
        "cachedir": str(settings.temp_dir / "ytcache"),
        "restrictfilenames": False,
        "windowsfilenames": True,
        "postprocessors": ([{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192",
        }] if audio_only else []),
        "progress_hooks": [lambda d: _on_hook(d, progress, cancel)],
    }

    if settings.cookies_file:
        opts["cookiefile"] = str(settings.cookies_file)
    elif settings.cookies_from_browser:
        opts["cookiesfrombrowser"] = (settings.cookies_from_browser,)

    target = impersonation_target()
    if target is not None:
        opts["impersonate"] = target

    return opts


def _on_hook(status: dict[str, Any], progress: ProgressCallback | None, cancel: _Cancel) -> None:
    if cancel.is_set():
        raise DownloadCancelled("cancelled by user")
    if progress is not None:
        progress(status)


def _free_space_check(workdir: Path, needed: int | None) -> None:
    available = free_disk_bytes(workdir)
    if available < settings.min_free_disk_bytes:
        raise DownloadFailure(
            "Server is out of disk space.",
            "The owner needs to free up space before new downloads can run.",
        )
    if needed and needed > available - (32 * 1024 * 1024):
        raise DownloadFailure(
            "Not enough space on the server for this video.",
            f"The file needs about {needed // (1024 * 1024)} MB but only "
            f"{available // (1024 * 1024)} MB is free.",
        )


def _pick_file(workdir: Path) -> Path:
    """Return the largest media file produced by yt-dlp (survives post-processing)."""
    candidates = [
        p for p in workdir.rglob("*")
        if p.is_file() and p.suffix.lower() in _MEDIA_EXTS and not p.name.endswith(".part")
    ]
    if not candidates:
        raise DownloadFailure(
            "Could not get the video file.",
            "The link may be broken, or the platform may be blocking the download.",
        )
    return max(candidates, key=lambda p: p.stat().st_size)


def _first(*values: Any) -> Any:
    for value in values:
        if value not in (None, "", [], {}):
            return value
    return None


def _to_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _to_float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result


def _normalise_date(raw: dict[str, Any]) -> tuple[str, int | None]:
    stamp = _to_int(_first(raw.get("timestamp"), raw.get("release_timestamp")))
    upload_date = str(raw.get("upload_date") or "")
    if not stamp and len(upload_date) == 8:
        try:
            import datetime as _dt

            stamp = int(_dt.datetime.strptime(upload_date, "%Y%m%d")
                        .replace(tzinfo=_dt.timezone.utc).timestamp())
        except ValueError:
            stamp = None
    if not upload_date and stamp:
        import datetime as _dt

        upload_date = _dt.datetime.fromtimestamp(stamp, _dt.timezone.utc).strftime("%Y-%m-%d")
    return upload_date, stamp


def build_meta(raw: dict[str, Any], url: str) -> VideoMeta:
    platform = platforms.detect_platform(url)
    extractor = str(raw.get("extractor_key") or raw.get("extractor") or "")
    if extractor:
        pretty = {
            "Youtube": "YouTube", "TikTok": "TikTok", "Twitter": "X (Twitter)",
            "Instagram": "Instagram", "Reddit": "Reddit", "Soundcloud": "SoundCloud",
            "Facebook": "Facebook",
        }.get(extractor.replace(":", ""), extractor.split(":")[0])
        if pretty:
            platform = platforms.Platform(pretty, platform.emoji, platform.domain)

    upload_date, timestamp = _normalise_date(raw)
    duration = _to_float(raw.get("duration"))

    tags_raw = raw.get("tags") or raw.get("categories") or []
    tags = [str(t) for t in tags_raw][:8] if isinstance(tags_raw, (list, tuple)) else []

    return VideoMeta(
        url=url,
        canonical_url=str(_first(raw.get("webpage_url"), raw.get("original_url"), url)),
        platform=platform.label,
        emoji=platform.emoji,
        extractor=extractor,
        video_id=str(raw.get("id") or ""),
        title=str(_first(raw.get("title"), raw.get("track"), raw.get("description"), "Untitled")).strip(),
        description=str(raw.get("description") or "").strip(),
        uploader=str(_first(raw.get("uploader"), raw.get("channel"), raw.get("creator"), "")),
        uploader_url=str(_first(raw.get("uploader_url"), raw.get("channel_url"), "")),
        channel=str(_first(raw.get("channel"), raw.get("uploader"), "")),
        duration=duration,
        upload_date=upload_date,
        timestamp=timestamp,
        view_count=_to_int(raw.get("view_count")),
        like_count=_to_int(raw.get("like_count")),
        comment_count=_to_int(raw.get("comment_count")),
        repost_count=_to_int(raw.get("repost_count")),
        thumbnail=raw.get("thumbnail"),
        width=_to_int(raw.get("width")),
        height=_to_int(raw.get("height")),
        fps=_to_float(raw.get("fps")),
        ext=str(raw.get("ext") or ""),
        filesize=_to_int(_first(raw.get("filesize"), raw.get("filesize_approx"))),
        is_live=bool(raw.get("is_live")),
        wasm_removed=bool(_first(raw.get("has_watermark") is False and raw.get("extractor") == "TikTok", False)),
        tags=tags,
    )


def enrich_tiktok(meta: VideoMeta, timeout: float = 8.0) -> VideoMeta:
    """Fill in TikTok author/title via the public oEmbed endpoint (best effort)."""
    if "tiktok" not in meta.extractor.lower() and "tiktok" not in meta.platform.lower():
        return meta
    try:
        import httpx

        url = meta.canonical_url or meta.url
        response = httpx.get(
            "https://www.tiktok.com/oembed",
            params={"url": url},
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        if response.status_code != 200:
            return meta
        data = response.json()
    except Exception as exc:  # noqa: BLE001 - metadata enrichment must never break a download
        log.debug("TikTok oembed enrichment failed: %s", exc)
        return meta

    author = str(data.get("author_name") or "").strip()
    if author:
        if not meta.uploader or meta.uploader.lower() in {"tiktok", "unknown"}:
            meta.uploader = author
            meta.channel = meta.channel or author
        if not meta.uploader_url:
            meta.uploader_url = str(data.get("author_url") or "").strip()
    title = str(data.get("title") or "").strip()
    if title and (not meta.description or len(meta.description) < 8):
        meta.description = title
        if not meta.title or meta.title.lower() in {"tiktok", "tik tok", "untitled"}:
            meta.title = title
    return meta


ERROR_HINTS: tuple[tuple[str, str, str], ...] = (
    ("impersonation", "The platform requires a browser fingerprint.",
     "Install curl_cffi and set IMPERSONATE in .env (Dailymotion needs this)."),
    ("po token", "This platform needs a proof-of-origin token.",
     "Add cookies (COOKIES_FILE / COOKIES_FROM_BROWSER) to the .env."),
    ("oauth token", "This platform needs authentication to hand out media.",
     "Add cookies (COOKIES_FILE / COOKIES_FROM_BROWSER) to the .env."),
    ("confirm you're not a bot", "YouTube is rate-limiting the bot.",
     "Add cookies (COOKIES_FILE) to the .env to bypass this."),
    ("unsupported url", "This link is not supported.", "Send a direct link to a post, not a profile or search page."),
    ("larger than max-filesize", "This video is too big to download.",
     "Full-length or ultra-HD files are rejected to keep the bot's disk under control."),
    ("skipping: playlist", "That link points to a playlist, not one video.",
     "Send the link to a single video or post."),
    ("requested format is not available", "No downloadable format found.",
     "Try a different quality or send the original link."),
    ("sign in to confirm your age", "This video is age-restricted.",
     "The bot owner can add cookies to unlock age-restricted content."),
    ("private video", "This video is private.", "Only the owner can watch private videos."),
    ("members-only", "This video is members-only.", "A membership is required to view it."),
    ("video unavailable", "This video is unavailable.",
     "It may have been deleted, or the link may be region-locked."),
    ("removed by the uploader", "The uploader removed this video.", "Nothing left to download."),
    ("account associated with this video has been terminated",
     "The uploader's account was terminated.", "Nothing left to download."),
    ("drm", "This video is DRM-protected and cannot be downloaded.",
     "DRM-protected media is not supported by design."),
    ("copyright", "This video was blocked for copyright reasons.", "Nothing left to download."),
    ("http error 404", "The video no longer exists (404).", "Double-check the link."),
    ("http error 403", "The platform refused the request (403).",
     "This usually needs cookies or a fresh link."),
    ("not available in your country", "This video is geo-blocked.", "The uploader limited it to some regions."),
    ("available in your country", "This video is geo-blocked.", "The uploader limited it to some regions."),
    ("geo restricted", "This video is geo-blocked.", "The uploader limited it to some regions."),
    ("not available in your location", "This video is geo-blocked.", "The uploader limited it to some regions."),
    ("login required", "This content requires an account.", "Add cookies to the .env to unlock it."),
    ("login", "This content requires an account.", "Add cookies to the .env to unlock it."),
    ("unable to download", "The platform blocked the download.", "Try again in a few minutes."),
    ("no video formats found", "No downloadable video found at this link.",
     "It may be a photo-only post or an unsupported page."),
    ("unable to extract", "Could not read that page.", "Make sure the link opens in a browser."),
    ("incompleteyoutube id", "That YouTube link is incomplete.", "Copy the full link from the app."),
    ("premieres in", "This video is an upcoming premiere.", "Come back after it has started."),
    ("this live event will begin", "The livestream has not started yet.", "Try again once it is live."),
    ("404", "Link not found (404).", "Double-check the link."),
)


# Platforms hand back typographic quotes and dashes ("you’re", "—"); without
# normalising them the needles below silently stop matching.
_SMART_CHARS = str.maketrans({
    "\u2018": "'", "\u2019": "'", "\u02bc": "'",
    "\u201c": '"', "\u201d": '"',
    "\u2013": "-", "\u2014": "-", "\u2212": "-",
    "\u00a0": " ",
})


def _normalise_error(text: str) -> str:
    return re.sub(r"\s+", " ", text.translate(_SMART_CHARS)).strip()


def friendly_error(exc: Exception) -> DownloadFailure:
    if isinstance(exc, DownloadFailure):
        return exc
    text = str(exc)
    lowered = _normalise_error(text).lower()
    for needle, message, hint in ERROR_HINTS:
        if needle in lowered:
            return DownloadFailure(message, hint, raw=text)
    log.warning("Unmapped download error: %s", text[:500])
    tail = text.strip().splitlines()[-1] if text.strip() else exc.__class__.__name__
    return DownloadFailure(
        "Download failed.",
        f"<code>{_escape(tail)[:300]}</code>",
        raw=text or exc.__class__.__name__,
    )


def _escape(text: str) -> str:
    return (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def download(
    url: str,
    *,
    progress: ProgressCallback | None = None,
    max_height: int | None = None,
    cancel: "CancelFlag | None" = None,
    attempts: int = 3,
    audio_only: bool = False,
) -> DownloadResult:
    """Download a single item, retrying transient CDN/network failures.

    Each retry re-extracts metadata so it picks up freshly signed media URLs.
    """
    last: Exception | None = None
    for attempt in range(attempts):
        if attempt:
            delay = _RETRY_BACKOFF[min(attempt, len(_RETRY_BACKOFF) - 1)]
            log.warning("Retrying %s in %.0fs (attempt %d/%d): %s",
                        url, delay, attempt + 1, attempts, str(last)[:160])
            time.sleep(delay)
        if cancel is not None and cancel.is_set():
            raise Cancelled("Download cancelled.")

        try:
            return _download_once(url, progress=progress, max_height=max_height,
                                  cancel=cancel, audio_only=audio_only)
        except Cancelled:
            raise
        except DownloadFailure as exc:
            last = exc
            if attempt == attempts - 1 or not _is_retryable(exc.raw or str(exc)):
                raise
        except Exception as exc:  # noqa: BLE001
            last = exc
            if attempt == attempts - 1 or not _is_retryable(str(exc)):
                raise friendly_error(exc) from exc
    raise friendly_error(last or RuntimeError("download failed"))


def _drop_page_cache(path: Path) -> None:
    """Evict a finished download from the page cache.

    Under a hard memory cgroup (containers cap RAM at ~150 MB here) the freshly
    written file stays resident and is charged to the same budget as the process,
    so uploading it on top of that cache is what gets the app OOM-killed.
    """
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
    except (AttributeError, OSError):
        pass
    finally:
        os.close(fd)


def _download_once(
    url: str,
    *,
    progress: ProgressCallback | None,
    max_height: int | None,
    cancel: "CancelFlag | None",
    audio_only: bool = False,
) -> DownloadResult:
    height = max_height or settings.max_height
    url = platforms.normalize_url(url)

    settings.temp_dir.mkdir(parents=True, exist_ok=True)
    workdir = Path(tempfile.mkdtemp(dir=settings.temp_dir, prefix="dl_"))
    cancel = cancel if cancel is not None else _Cancel()

    try:
        check_reachable(url)
        _free_space_check(workdir, None)
        opts = build_ytdlp_opts(workdir, height, progress, cancel, audio_only=audio_only)
        started = time.monotonic()

        with yt_dlp.YoutubeDL(opts) as ydl:
            try:
                raw = ydl.extract_info(url, download=True)
            except DownloadCancelled as exc:
                raise Cancelled("Download cancelled.") from exc
            except DownloadError as exc:
                raise friendly_error(exc) from exc

        if not isinstance(raw, dict):
            raise DownloadFailure("Could not read anything from that link.",
                                   "Make sure it points to a single video or post.")

        if raw.get("_type") in {"playlist", "multi_video"}:
            entries = [e for e in (raw.get("entries") or []) if e]
            if not entries:
                raise DownloadFailure("That playlist is empty.", "Nothing to download.")
            raw = entries[0]

        meta = build_meta(raw, url)
        if "tiktok" in meta.extractor.lower() or "tiktok" in meta.platform.lower():
            meta = enrich_tiktok(meta)

        path = _pick_file(workdir)
        _drop_page_cache(path)
        elapsed = time.monotonic() - started
        log.info(
            "Downloaded %s (%s) -> %s in %.1fs", meta.platform, meta.video_id, path.name, elapsed,
        )
        return DownloadResult(meta=meta, path=path, workdir=workdir)

    except (Cancelled, DownloadFailure):
        shutil.rmtree(workdir, ignore_errors=True)
        raise
    except Exception as exc:  # noqa: BLE001 - convert anything unexpected into a user error
        shutil.rmtree(workdir, ignore_errors=True)
        raise friendly_error(exc) from exc


# Errors that are worth retrying: transient network problems and CDN hiccups.
# Signed media URLs (TikTok especially) expire quickly, so a retry must re-extract.
_RETRYABLE = (
    "curl: (28)", "timed out", "timeout", "connection reset", "connection aborted",
    "remote end closed", "too many requests", "temporarily", "bad gateway",
    "service unavailable", "503", "502", "504", "429", "incomplete read",
    "expected more data", "http error 403", "403: forbidden",
)
_RETRY_BACKOFF = (0.0, 3.0, 8.0, 20.0)


def _is_retryable(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in _RETRYABLE)


def check_reachable(url: str) -> None:
    """Cheap pre-flight: shape-check the URL before any network work starts."""
    if not url.lower().startswith(("http://", "https://")):
        raise DownloadFailure("That is not a valid link.", "Links must start with http:// or https://")
    if len(url) > 2048:
        raise DownloadFailure("That link is too long.", "Send just the link to the video or post.")

    reason = platforms.classify_url(url)
    if reason:
        raise DownloadFailure(reason, "Send the link to one video or post instead.")


__all__ = [
    "Cancelled", "DownloadFailure", "DownloadResult", "VideoMeta",
    "build_meta", "check_reachable", "detect", "download", "friendly_error",
    "free_disk_bytes", "memory_headroom", "upload_fits",
]


def detect(url: str) -> platforms.Platform:
    return platforms.detect_platform(url)
