"""UniversalDownload core: yt-dlp engine, media inspection, platform parsing."""

from __future__ import annotations

from .config import settings
from .downloader import (
    Cancelled,
    DownloadFailure,
    DownloadResult,
    VideoMeta,
    build_meta,
    build_ytdlp_opts,
    check_reachable,
    detect,
    download,
    enrich_tiktok,
    friendly_error,
)
from .jobs import CancelFlag, Job, JobStore, Progress, State
from .media import MediaInfo, fit_audio, fit_video, human_size, probe

__all__ = [
    "CancelFlag", "Cancelled", "DownloadFailure", "DownloadResult", "Job",
    "JobStore", "MediaInfo", "Progress", "State", "VideoMeta", "build_meta",
    "build_ytdlp_opts", "check_reachable", "detect", "download", "enrich_tiktok",
    "fit_audio", "fit_video", "friendly_error", "human_size", "probe",
    "settings",
]
