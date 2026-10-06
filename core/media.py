"""Media inspection and ffmpeg post-processing (size fitting, kind detection)."""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .config import settings

log = logging.getLogger(__name__)

VIDEO_EXTS = {".mp4", ".mkv", ".webm", ".mov", ".m4v", ".flv", ".ts", ".avi", ".wmv", ".mpg", ".mpeg"}
AUDIO_EXTS = {".mp3", ".m4a", ".aac", ".opus", ".ogg", ".wav", ".flac", ".wma", ".aiff"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".heic", ".tiff", ".avif"}
# Video codecs that are really still images; ffprobe reports them as video streams.
IMAGE_CODECS = {"mjpeg", "png", "webp", "bmp", "gif", "tiff", "heif", "heic", "avif", "qoi"}

# (max height, crf, audio bitrate) -- first entry is the highest quality attempt.
LADDER: tuple[tuple[int, int, str], ...] = (
    (1080, 26, "160k"),
    (1080, 30, "128k"),
    (720, 28, "128k"),
    (720, 32, "96k"),
    (480, 30, "96k"),
    (480, 34, "64k"),
    (360, 32, "64k"),
    (360, 36, "48k"),
    (240, 34, "48k"),
    (144, 34, "32k"),
)


class TranscodeError(RuntimeError):
    pass


@dataclass
class MediaInfo:
    path: Path
    size: int
    has_video: bool
    has_audio: bool
    duration: float | None = None
    width: int | None = None
    height: int | None = None
    kind: str = "unknown"

    @property
    def size_mb(self) -> float:
        return self.size / (1024 * 1024)


def _run(cmd: list[str], timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL,
        timeout=timeout,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def probe(path: Path) -> MediaInfo:
    """Inspect a media file. Falls back to extension sniffing if ffprobe is unavailable."""
    try:
        size = path.stat().st_size
    except OSError:
        size = 0

    ext = path.suffix.lower()
    fallback_kind = (
        "video" if ext in VIDEO_EXTS
        else "audio" if ext in AUDIO_EXTS
        else "image" if ext in IMAGE_EXTS
        else "unknown"
    )

    cmd = [
        settings.ffprobe, "-v", "quiet", "-print_format", "json",
        "-show_format", "-show_streams", str(path),
    ]
    try:
        proc = _run(cmd, timeout=90)
        data = json.loads(proc.stdout.decode("utf-8", "replace") or "{}")
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError, FileNotFoundError) as exc:
        log.warning("ffprobe failed for %s: %s", path.name, exc)
        return MediaInfo(path=path, size=size, has_video=False, has_audio=False, kind=fallback_kind)

    streams = data.get("streams") or []
    fmt = data.get("format") or {}

    video_streams = [s for s in streams if s.get("codec_type") == "video"]
    audio_streams = [s for s in streams if s.get("codec_type") == "audio"]
    # A single attached picture (cover art) is not a real video track.
    video_streams = [
        s for s in video_streams
        if not (s.get("disposition") or {}).get("attached_pic")
    ]

    duration = None
    for candidate in (fmt.get("duration"), *(s.get("duration") for s in streams)):
        try:
            duration = float(candidate)
            if duration > 0:
                break
            duration = None
        except (TypeError, ValueError):
            duration = None

    width = height = None
    if video_streams:
        main = max(video_streams, key=lambda s: (s.get("height") or 0) * (s.get("width") or 0))
        width, height = main.get("width"), main.get("height")

    # ffprobe reports PNG/GIF/WEBP as one-frame "video" streams; those are images.
    still_image = bool(video_streams) and all(
        (s.get("codec_name") or "").lower() in IMAGE_CODECS for s in video_streams
    )

    has_video, has_audio = bool(video_streams) and not still_image, bool(audio_streams)
    if has_video:
        kind = "video"
    elif still_image or ext in IMAGE_EXTS:
        kind = "image"
    elif has_audio:
        kind = "audio"
    else:
        kind = fallback_kind

    if not size and fmt.get("size"):
        try:
            size = int(fmt["size"])
        except (TypeError, ValueError):
            pass

    return MediaInfo(
        path=path, size=size, has_video=has_video, has_audio=has_audio,
        duration=duration, width=width, height=height, kind=kind,
    )


def _encode(
    src: Path,
    dst: Path,
    height: int | None,
    crf: int | None,
    audio_bitrate: str,
    duration: float | None,
    on_progress: Callable[[float], None] | None,
) -> bool:
    cmd = [
        settings.ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
        "-i", str(src),
    ]

    if height:
        cmd += ["-map", "0:v:0", "-map", "0:a:0?", "-vf", f"scale=-2:{height}"]
        cmd += [
            "-c:v", "libx264", "-preset", "veryfast", "-crf", str(crf),
            "-pix_fmt", "yuv420p", "-profile:v", "high",
        ]
    else:
        cmd += ["-vn"]

    if audio_bitrate and audio_bitrate != "none":
        cmd += ["-c:a", "aac", "-b:a", audio_bitrate, "-ac", "2", "-ar", "48000"]
    else:
        cmd += ["-an"]

    cmd += ["-map_metadata", "-1", "-movflags", "+faststart", "-max_muxing_queue_size", "4096"]

    if on_progress and duration:
        cmd += ["-progress", "pipe:1", "-nostats"]

    cmd.append(str(dst))

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except OSError as exc:
        raise TranscodeError(f"cannot start ffmpeg: {exc}") from exc

    try:
        assert proc.stdout is not None
        for raw in proc.stdout:
            if not on_progress or not duration:
                continue
            line = raw.decode("utf-8", "replace").strip()
            if line.startswith("out_time_ms="):
                try:
                    micros = int(line.split("=", 1)[1])
                except ValueError:
                    continue
                on_progress(max(0.0, min(1.0, (micros / 1_000_000) / duration)))
        proc.wait(timeout=1800)
    except subprocess.TimeoutExpired:
        proc.kill()
        raise TranscodeError("ffmpeg timed out") from None
    finally:
        for stream in (proc.stdout, proc.stderr):
            try:
                if stream is not None:
                    stream.close()
            except OSError:
                pass

    if proc.returncode != 0 or not dst.exists() or dst.stat().st_size == 0:
        dst.unlink(missing_ok=True)
        return False
    return True


def fit_video(
    info: MediaInfo,
    limit_bytes: int,
    max_height: int,
    on_progress: Callable[[float], None] | None = None,
) -> tuple[MediaInfo | None, list[tuple[int, int]]]:
    """Re-encode until the file fits under `limit_bytes`.

    Returns the new MediaInfo (or None if impossible) and the attempted ladder steps.
    The fitted file is written next to the source so it survives the scratch workdir
    and is removed by the caller's cleanup of that directory.
    """
    attempts: list[tuple[int, int]] = []

    if info.size <= limit_bytes:
        return info, attempts

    ladder = [step for step in LADDER if step[0] <= max(max_height, 144)]
    if not ladder:
        ladder = list(LADDER)
    ladder.sort(key=lambda step: (step[1], -step[0]))  # gentle quality first

    workdir = Path(tempfile.mkdtemp(dir=settings.temp_dir, prefix="fit_"))
    try:
        for height, crf, abr in ladder:
            attempts.append((height, crf))
            target = workdir / f"{height}p{crf}.mp4"
            fraction = attempts.index((height, crf)) / max(1, len(ladder))
            span = 1.0 / max(1, len(ladder))
            base = fraction * span
            cb = (
                (lambda p, _b=base, _s=span: on_progress(_b + p * _s))
                if on_progress else None
            )

            if not _encode(info.path, target, height, crf, abr, info.duration, cb):
                continue
            size = target.stat().st_size
            if size <= limit_bytes:
                final = info.path.with_name(f"fitted_{info.path.stem}.mp4")
                target.replace(final)
                return probe(final), attempts
            target.unlink(missing_ok=True)

        # Last resort: audio-only stream, always tiny enough to be useful.
        if info.has_audio:
            target = workdir / "audio.m4a"
            if _encode(info.path, target, None, None, "128k", info.duration, None):
                final = info.path.with_name(f"fitted_{info.path.stem}.m4a")
                target.replace(final)
                return probe(final), attempts
            target.unlink(missing_ok=True)

        return None, attempts
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def fit_audio(info: MediaInfo, limit_bytes: int) -> MediaInfo | None:
    if info.size <= limit_bytes:
        return info
    workdir = Path(tempfile.mkdtemp(dir=settings.temp_dir, prefix="aud_"))
    try:
        for abr in ("192k", "128k", "96k", "64k", "48k"):
            target = workdir / f"a{abr}.m4a"
            if _encode(info.path, target, None, None, abr, info.duration, None):
                if target.stat().st_size <= limit_bytes:
                    final = info.path.with_name(f"fitted_{info.path.stem}.m4a")
                    target.replace(final)
                    return probe(final)
                target.unlink(missing_ok=True)
        return None
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def human_size(num_bytes: float) -> str:
    units = ("B", "KB", "MB", "GB", "TB")
    value = float(num_bytes)
    for unit in units:
        if abs(value) < 1024 or unit == units[-1]:
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.2f} {unit}"
        value /= 1024
    return f"{value:.2f} TB"