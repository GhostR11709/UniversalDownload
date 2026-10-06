"""Local HTTP server for the UniversalDownload browser extension.

The extension talks to this process over 127.0.0.1 only:

    GET    /api/health                  liveness + capability probe
    POST   /api/detect                  metadata for a pasted/clicked link
    POST   /api/downloads               queue a download, returns a job
    GET    /api/jobs                    recent jobs, newest first
    GET    /api/jobs/{id}               one job with progress
    POST   /api/jobs/{id}/cancel        cooperative cancel
    DELETE /api/jobs/{id}/file          drop the server-side temp copy
    GET    /files/{id}                  stream the file (Range aware)
    POST   /api/reveal                  open the containing folder
    POST   /api/settings                change live limits
    POST   /api/update                  pull a newer extension from GitHub
    GET    /                             small dashboard for humans
"""

from __future__ import annotations

import json
import logging
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from core import (
    Cancelled,
    DownloadFailure,
    JobStore,
    State,
    build_meta,
    check_reachable,
    detect,
    download,
    friendly_error,
    human_size,
    probe,
    settings,
)
from core import media as media_mod
from core.jobs import Job, Progress
from core.media import MediaInfo

log = logging.getLogger("ud.server")

APP_NAME = "UniversalDownload"
PROJECT_ROOT = Path(__file__).resolve().parent.parent
VERSION_FILE = PROJECT_ROOT / "VERSION"
APP_VERSION = VERSION_FILE.read_text(encoding="utf-8").strip() if VERSION_FILE.exists() else "1.0.0"
REPO_URL = "https://github.com/GhostR11709/UniversalDownload"
READY_DIR = settings.temp_dir / "ready"
JOBS = JobStore(max_history=settings.max_history)

_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = {
    "CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def safe_name(text: str, fallback: str = "download", max_len: int = 90) -> str:
    """Filesystem-safe, Windows-reserved-aware base name (no extension)."""
    cleaned = _UNSAFE.sub(" ", text or "")
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    cleaned = cleaned[:max_len].rstrip(" .")
    if not cleaned or cleaned.upper() in _RESERVED:
        cleaned = fallback
    return cleaned


def build_filename(meta_title: str, uploader: str, path: Path) -> str:
    stem = safe_name(meta_title or path.stem)
    if uploader and safe_name(uploader).lower() not in stem.lower():
        stem = f"{stem} - {safe_name(uploader, max_len=40)}"[:110].strip(" .")
    return f"{stem}{path.suffix.lower()}"


def _human_duration(seconds: float | None) -> str | None:
    if not seconds or seconds <= 0:
        return None
    total = int(seconds)
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def sweep_stale_files() -> None:
    """Drop finished files the browser never collected."""
    while True:
        time.sleep(120)
        cutoff = time.time() - settings.keep_temp_seconds
        for job in JOBS.list({State.done}):
            if job.finished_at and job.finished_at < cutoff and not job.saved and job.path:
                try:
                    job.path.unlink(missing_ok=True)
                    log.info("swept stale file for job %s", job.id)
                except OSError:
                    pass


# --------------------------------------------------------------------------- #
# work
# --------------------------------------------------------------------------- #
def run_job(job: Job, quality: str = "best") -> None:
    """Executed on a worker thread: fetch metadata, download, probe, name it."""
    job.state = State.running
    max_height = quality_to_height(quality, settings.max_height)

    def on_progress(status: dict) -> None:
        downloaded = int(status.get("downloaded_bytes") or 0)
        total = status.get("total_bytes") or status.get("total_bytes_estimate")
        pct = 0.0
        if total:
            pct = min(100.0, downloaded / float(total) * 100)
        elif status.get("status") == "finished":
            pct = 100.0
        job.progress = Progress(
            percent=round(pct, 1),
            downloaded=downloaded,
            total=int(total) if total else None,
            speed=status.get("speed"),
            eta=status.get("eta"),
            stage="finishing" if status.get("status") == "finished" else "",
        )

    try:
        result = download(job.url, progress=on_progress, max_height=max_height,
                          cancel=job.cancel, attempts=settings.retries)
    except Cancelled:
        job.state = State.cancelled
        job.progress = Progress(percent=0.0, stage="cancelled")
        return
    except DownloadFailure as exc:
        job.state = State.error
        job.error = exc.message
        job.hint = exc.hint
        return
    except Exception as exc:  # noqa: BLE001
        failure = friendly_error(exc)
        job.state = State.error
        job.error = failure.message
        job.hint = failure.hint
        return

    meta = result.meta
    job.progress = Progress(percent=100.0, stage="done")
    job.title = meta.title
    job.platform = meta.platform
    job.emoji = meta.emoji
    job.uploader = meta.uploader
    job.thumbnail = meta.thumbnail
    job.duration = meta.duration
    job.state = State.done
    job.finished_at = time.time()

    try:
        info: MediaInfo = probe(result.path)
        job.width, job.height = info.width, info.height
        job.size = info.size
        job.duration = info.duration or meta.duration
        job.filename = build_filename(meta.title, meta.uploader, result.path)
    except Exception as exc:  # noqa: BLE001
        log.warning("probe failed for job %s: %s", job.id, exc)
        job.size = result.path.stat().st_size
        job.filename = build_filename(meta.title, meta.uploader, result.path)

    # Park the file where the HTTP layer can stream it, then release the workdir.
    READY_DIR.mkdir(parents=True, exist_ok=True)
    target = READY_DIR / job.id / result.path.name
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.move(str(result.path), str(target))
        job.path = target
    except OSError as exc:
        log.warning("could not park file for job %s: %s", job.id, exc)
        job.path = result.path
    finally:
        result.cleanup()


def detect_url(url: str) -> dict:
    """Metadata only - used by the popup's link preview and /api/detect."""
    import yt_dlp

    check_reachable(url)
    opts = {
        "quiet": True, "no_warnings": True, "skip_download": True,
        "noplaylist": True, "socket_timeout": 30,
    }
    if settings.cookies_file:
        opts["cookiefile"] = str(settings.cookies_file)
    if settings.impersonate:
        try:
            from yt_dlp.networking.impersonate import ImpersonateTarget

            opts["impersonate"] = ImpersonateTarget.from_str(settings.impersonate)
        except Exception as exc:  # noqa: BLE001
            log.warning("impersonation disabled: %s", exc)

    with yt_dlp.YoutubeDL(opts) as ydl:
        raw = ydl.extract_info(url, download=False)
    if not isinstance(raw, dict):
        raise DownloadFailure("Could not read anything from that link.", "Try a direct media link.")
    if raw.get("_type") in {"playlist", "multi_video"}:
        entries = [e for e in (raw.get("entries") or []) if e]
        if not entries:
            raise DownloadFailure("That playlist is empty.", "Nothing to download.")
        raw = entries[0]
    meta = build_meta(raw, url)
    platform = detect(url)
    formats = [
        {"format_id": f.get("format_id"), "ext": f.get("ext"),
         "height": f.get("height"), "fps": f.get("fps"),
         "vcodec": f.get("vcodec"), "acodec": f.get("acodec"),
         "filesize": f.get("filesize") or f.get("filesize_approx"),
         "note": f.get("format_note")}
        for f in (raw.get("formats") or [])
    ]
    return {
        "url": url,
        "title": meta.title,
        "uploader": meta.uploader,
        "duration": meta.duration,
        "duration_text": meta.formatted_duration,
        "thumbnail": meta.thumbnail,
        "platform": platform.label,
        "emoji": platform.emoji,
        "extractor": meta.extractor,
        "width": raw.get("width"),
        "height": raw.get("height"),
        "is_live": meta.is_live,
        "formats": formats[:40],
        "webpage_url": meta.canonical_url,
    }


def quality_to_height(quality: str, fallback: int) -> int | None:
    if not quality or quality in {"best", "auto", ""}:
        return fallback
    if quality == "audio":
        return 144
    digits = "".join(ch for ch in quality if ch.isdigit())
    return int(digits) if digits else fallback


def pull_extension_update() -> dict:
    """Start the project updater so a fresh checkout can pull GitHub changes."""
    if os.getenv("ALLOW_REMOTE_UPDATE", "1").lower() in {"0", "false", "off", "no"}:
        return {"ok": False, "message": "Remote updates are disabled (ALLOW_REMOTE_UPDATE=0)."}
    updater = PROJECT_ROOT / "scripts" / "update.ps1"
    if updater.exists():
        try:
            subprocess.Popen(
                ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                 "-File", str(updater)],
                cwd=str(PROJECT_ROOT),
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            return {"ok": True, "message": "Update started. Restart the server when it finishes."}
        except OSError as exc:
            return {"ok": False, "message": f"Could not start updater: {exc}"}

    ext_dir = PROJECT_ROOT / "extension"
    try:
        proc = subprocess.run(
            ["git", "-C", str(PROJECT_ROOT), "pull", "--ff-only"],
            capture_output=True, text=True, timeout=120,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {"ok": False, "message": f"git pull failed: {exc}"}
    if proc.returncode != 0:
        return {"ok": False, "message": (proc.stderr or proc.stdout or "git pull failed").strip()[:300]}
    log.info("extension updated from git: %s", (proc.stdout or "").strip()[:200])
    return {"ok": True, "message": "Updated. Reloading the extension.",
            "extension_dir": str(ext_dir)}


# --------------------------------------------------------------------------- #
# dashboard (so the tool is useful without the extension too)
# --------------------------------------------------------------------------- #
def dashboard_html() -> bytes:
    jobs = "\n".join(
        f"<tr><td>{j.state.value}</td><td>{j.progress.percent:.0f}%</td>"
        f"<td>{_escape(j.title or j.url)}</td><td class='dim'>{_escape(j.platform or '')}</td>"
        f"<td class='dim'>{human_size(j.size) if j.size else ''}</td>"
        f"<td class='dim'>{_escape(j.error or '')}</td></tr>"
        for j in JOBS.recent(12)
    ) or "<tr><td colspan='6' class='dim'>No downloads yet.</td></tr>"
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{APP_NAME}</title>
<style>
 :root{{color-scheme:dark}}
 body{{margin:0;padding:32px;background:#0f1115;color:#e8eaed;
      font:14px/1.5 ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif}}
 h1{{font-size:18px;margin:0 0 4px}} .dim{{color:#9aa0a6}}
 form{{display:flex;gap:8px;margin:20px 0 24px;max-width:720px}}
 input{{flex:1;padding:10px 12px;border-radius:8px;border:1px solid #30363d;
        background:#161a20;color:inherit;font:inherit}}
 button{{padding:10px 16px;border-radius:8px;border:0;background:#3b82f6;color:#fff;
        font:inherit;font-weight:600;cursor:pointer}}
 table{{border-collapse:collapse;width:100%;max-width:900px}}
 th,td{{text-align:left;padding:8px 10px;border-bottom:1px solid #21262d;font-size:13px}}
 th{{color:#9aa0a6;font-weight:600;text-transform:uppercase;font-size:11px;letter-spacing:.06em}}
 code{{background:#161a20;padding:2px 6px;border-radius:4px}}
</style></head><body>
<h1>{APP_NAME} <span class="dim">v{APP_VERSION}</span></h1>
<div class="dim">Local server on http://{settings.host}:{settings.port} &middot;
 <a style="color:#60a5fa" href="{REPO_URL}">GitHub</a></div>
<form onsubmit="go(event)">
  <input id="u" placeholder="Paste any video / audio / image link&hellip;" autofocus>
  <button type="submit">Download</button>
</form>
<table><thead><tr><th>State</th><th>%</th><th>Title</th><th>Site</th><th>Size</th><th>Error</th></tr></thead>
<tbody>{jobs}</tbody></table>
<script>
async function go(e){{e.preventDefault();const url=document.getElementById('u').value.trim();
 if(!url)return;document.getElementById('u').value='queued…';
 await fetch('/api/downloads',{{method:'POST',headers:{{'Content-Type':'application/json'}},
   body:JSON.stringify({{url}})}});setTimeout(()=>location.reload(),900)}}
</script>
</body></html>""".encode("utf-8")


def _escape(text: str) -> str:
    return (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #
class Handler(BaseHTTPRequestHandler):
    server_version = f"{APP_NAME}/{APP_VERSION}"
    protocol_version = "HTTP/1.1"

    # -- plumbing ---------------------------------------------------------- #
    def log_message(self, fmt: str, *args) -> None:  # quieter default logging
        log.debug("%s - %s", self.address_string(), fmt % args)

    def _cors(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Max-Age", "600")

    def _send(self, code: int, body: bytes, ctype: str = "application/json",
              extra: dict[str, str] | None = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self._cors()
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, payload: dict | list, code: int = 200) -> None:
        self._send(code, json.dumps(payload, default=str).encode("utf-8"))

    def _error(self, exc: Exception, code: int = 400) -> None:
        if isinstance(exc, DownloadFailure):
            payload = {"ok": False, "error": exc.message, "hint": exc.hint}
        else:
            log.exception("request failed")
            payload = {"ok": False, "error": type(exc).__name__, "hint": str(exc)[:300]}
        self._json(payload, code)

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8")) or {}
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise DownloadFailure("Malformed request body.", str(exc)) from exc

    def do_OPTIONS(self) -> None:  # noqa: N802
        self._send(204, b"", "text/plain")

    def do_HEAD(self) -> None:  # noqa: N802
        self.do_GET()

    # -- routes ------------------------------------------------------------ #
    def do_GET(self) -> None:  # noqa: N802
        path = urllib.parse.urlparse(self.path).path
        try:
            if path in {"/", "/index.html"}:
                self._send(200, dashboard_html(), "text/html; charset=utf-8")
                return
            if path == "/api/health":
                self._json({
                    "ok": True, "name": APP_NAME, "version": APP_VERSION,
                    "port": settings.port, "active": len(JOBS.active()),
                    "ffmpeg": settings.ffmpeg, "ffprobe": settings.ffprobe,
                    "impersonate": settings.impersonate,
                    "max_height": settings.max_height,
                    "cookies": bool(settings.cookies_file),
                    "cookies_from_browser": bool(settings.cookies_from_browser),
                    "repo": REPO_URL,
                })
                return
            if path == "/api/jobs":
                self._json([j.to_json() for j in JOBS.recent(settings.max_history)])
                return
            if path.startswith("/api/jobs/"):
                job_id = path.rsplit("/", 1)[-1]
                job = JOBS.get(job_id)
                if not job:
                    self._json({"ok": False, "error": "Unknown job."}, 404)
                    return
                self._json(job.to_json())
                return
            if path.startswith("/files/"):
                self._serve_file(path.split("/")[-1])
                return
            self._json({"ok": False, "error": "Not found."}, 404)
        except Exception as exc:  # noqa: BLE001
            self._error(exc, 500)

    def do_POST(self) -> None:  # noqa: N802
        path = urllib.parse.urlparse(self.path).path
        try:
            payload = self._body()

            if path == "/api/detect":
                url = str(payload.get("url") or "").strip()
                if not url:
                    raise DownloadFailure("No URL given.", "Send a link to inspect.")
                self._json({"ok": True, "media": detect_url(url)})
                return

            if path == "/api/downloads":
                url = str(payload.get("url") or "").strip()
                if not url:
                    raise DownloadFailure("No URL given.", "Send a link to download.")
                check_reachable(url)
                if len(JOBS.active()) >= settings.max_concurrent_jobs:
                    raise DownloadFailure(
                        "The server is busy.",
                        f"Wait for a running download to finish "
                        f"({settings.max_concurrent_jobs} at a time).",
                    )
                platform = detect(url)
                job = JOBS.create(
                    url,
                    title=str(payload.get("title") or "")[:300],
                    page_url=str(payload.get("page_url") or "")[:500],
                    platform=platform.label,
                    emoji=platform.emoji,
                    quality=str(payload.get("quality") or "best")[:20],
                    progress=Progress(stage="queued"),
                )
                POOL.submit(run_job, job, job.quality)
                self._json({"ok": True, "job": job.to_json()})
                return

            if path.startswith("/api/jobs/") and path.endswith("/cancel"):
                job = JOBS.get(path.split("/")[3])
                if not job:
                    self._json({"ok": False, "error": "Unknown job."}, 404)
                    return
                job.cancel.cancel()
                job.progress = Progress(stage="cancelling")
                self._json({"ok": True, "job": job.to_json()})
                return

            if path == "/api/reveal":
                job = JOBS.get(str(payload.get("id") or ""))
                target = job.path.parent if job and job.path else None
                if target and target.exists():
                    if sys.platform == "win32":
                        os.startfile(str(target))  # noqa: S606
                    elif sys.platform == "darwin":
                        subprocess.Popen(["open", str(target)])
                    else:
                        subprocess.Popen(["xdg-open", str(target)])
                    self._json({"ok": True, "path": str(target)})
                    return
                self._json({"ok": False, "error": "File is no longer on disk."}, 404)
                return

            if path == "/api/settings":
                applied = update_settings(payload)
                self._json({"ok": True, "settings": applied})
                return

            if path == "/api/update":
                self._json(pull_extension_update())
                return

            self._json({"ok": False, "error": "Not found."}, 404)
        except Exception as exc:  # noqa: BLE001
            self._error(exc)

    def do_DELETE(self) -> None:  # noqa: N802
        path = urllib.parse.urlparse(self.path).path
        try:
            if path.startswith("/api/jobs/") and path.endswith("/file"):
                job = JOBS.get(path.split("/")[3])
                if not job:
                    self._json({"ok": False, "error": "Unknown job."}, 404)
                    return
                if job.path and job.path.exists():
                    job.path.unlink(missing_ok=True)
                    parent = job.path.parent
                    if parent.name == job.id:
                        shutil.rmtree(parent, ignore_errors=True)
                job.saved = True
                self._json({"ok": True})
                return
            if path == "/api/jobs":
                JOBS.clear_finished()
                self._json({"ok": True})
                return
            self._json({"ok": False, "error": "Not found."}, 404)
        except Exception as exc:  # noqa: BLE001
            self._error(exc, 500)

    # -- file streaming ---------------------------------------------------- #
    def _serve_file(self, job_id: str) -> None:
        job = JOBS.get(job_id)
        if not job or not job.path or not job.path.exists():
            self._json({"ok": False, "error": "That file is gone."}, 404)
            return
        path: Path = job.path
        size = path.stat().st_size
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        filename = job.filename or path.name
        quoted = urllib.parse.quote(filename)

        start, end = 0, size - 1
        partial = False
        header = self.headers.get("Range") or ""
        match = re.match(r"bytes=(\d*)-(\d*)", header)
        if match and size:
            if match.group(1):
                start = int(match.group(1))
                if match.group(2):
                    end = min(int(match.group(2)), size - 1)
            elif match.group(2):
                start = max(0, size - int(match.group(2)))
            partial = True

        length = max(0, end - start + 1)
        self.send_response(206 if partial else 200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(length))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"; filename*=UTF-8\'\'{quoted}')
        if partial:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self._cors()
        self.end_headers()
        if self.command == "HEAD":
            return

        with path.open("rb") as fh:
            fh.seek(start)
            remaining = length
            while remaining > 0:
                chunk = fh.read(min(256 * 1024, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                try:
                    self.wfile.write(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    return


def update_settings(payload: dict) -> dict:
    """Live-limit changes from the popup (process-wide, not persisted)."""
    applied: dict[str, int | str | bool] = {}
    if "max_height" in payload:
        value = int(payload["max_height"] or 0)
        if value in {144, 240, 360, 480, 720, 1080, 1440, 2160} or 0 < value <= 4320:
            object.__setattr__(settings, "max_height", value or settings.max_height)
            applied["max_height"] = settings.max_height
    if "max_download_mb" in payload:
        value = int(payload["max_download_mb"] or 0)
        if 0 < value <= 8192:
            object.__setattr__(settings, "max_download_bytes", value * 1024 * 1024)
            applied["max_download_mb"] = value
    return applied


POOL = ThreadPoolExecutor(max_workers=settings.max_concurrent_jobs,
                          thread_name_prefix="ud-dl")
SERVER: ThreadingHTTPServer | None = None


def serve_forever() -> None:
    global SERVER
    settings.temp_dir.mkdir(parents=True, exist_ok=True)
    READY_DIR.mkdir(parents=True, exist_ok=True)
    SERVER = ThreadingHTTPServer((settings.host, settings.port), Handler)
    SERVER.daemon_threads = True
    threading.Thread(target=sweep_stale_files, daemon=True).start()
    log.info("%s v%s listening on http://%s:%d", APP_NAME, APP_VERSION,
             settings.host, settings.port)
    SERVER.serve_forever()


def shutdown() -> None:
    POOL.shutdown(wait=False, cancel_futures=True)
    if SERVER is not None:
        threading.Thread(target=SERVER.shutdown, daemon=True).start()


def open_dashboard() -> None:
    webbrowser.open(f"http://{settings.host}:{settings.port}/")
