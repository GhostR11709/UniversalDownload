"""In-memory job registry with progress, cancellation and a bounded history.

One job == one download request coming from the browser extension.
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class State(str, Enum):
    queued = "queued"
    running = "running"
    done = "done"
    error = "error"
    cancelled = "cancelled"


@dataclass
class Progress:
    percent: float = 0.0
    downloaded: int = 0
    total: int | None = None
    speed: float | None = None
    eta: float | None = None
    stage: str = ""

    @property
    def label(self) -> str:
        if self.stage:
            return self.stage
        if self.total:
            return f"{self.percent:.0f}%"
        return "starting…"


class CancelFlag:
    """Flag the download worker polls; thread safe via the GIL for a bool."""

    __slots__ = ("_event",)

    def __init__(self) -> None:
        self._event = threading.Event()

    def set_flag(self) -> None:
        self._event.set()

    def cancel(self) -> None:
        self._event.set()

    def is_set(self) -> bool:
        return self._event.is_set()

    def wait(self, timeout: float) -> bool:
        return self._event.wait(timeout)


@dataclass
class Job:
    url: str
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    title: str = ""
    page_url: str = ""
    platform: str = ""
    emoji: str = ""
    quality: str = "best"
    requested_filename: str = ""
    folder: str = ""
    metadata_enabled: bool = False
    embed_tags: bool = True
    state: State = State.queued
    progress: Progress = field(default_factory=Progress)
    error: str = ""
    hint: str = ""
    filename: str = ""
    size: int | None = None
    duration: float | None = None
    uploader: str = ""
    description: str = ""
    upload_date: str = ""
    thumbnail: str | None = None
    width: int | None = None
    height: int | None = None
    tags_embedded: bool = False
    created_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    saved: bool = False
    path: Path | None = None
    saved_path: str = ""
    metadata_path: Path | None = None
    metadata_filename: str = ""
    cancel: CancelFlag = field(default_factory=CancelFlag)

    def to_json(self) -> dict[str, Any]:
        # Do not call asdict(self): it recursively deep-copies CancelFlag and
        # threading.Event, which contains an unpicklable _thread.lock.
        data = {
            "id": self.id,
            "url": self.url,
            "title": self.title,
            "page_url": self.page_url,
            "platform": self.platform,
            "emoji": self.emoji,
            "quality": self.quality,
            "requested_filename": self.requested_filename,
            "folder": self.folder,
            "metadata_enabled": self.metadata_enabled,
            "embed_tags": self.embed_tags,
            "state": self.state.value,
            "progress": asdict(self.progress),
            "error": self.error,
            "hint": self.hint,
            "filename": self.filename,
            "size": self.size,
            "duration": self.duration,
            "uploader": self.uploader,
            "description": self.description,
            "upload_date": self.upload_date,
            "thumbnail": self.thumbnail,
            "width": self.width,
            "height": self.height,
            "tags_embedded": self.tags_embedded,
            "created_at": self.created_at,
            "finished_at": self.finished_at,
            "saved": self.saved,
            "saved_path": self.saved_path,
            "metadata_filename": self.metadata_filename,
        }
        data["age"] = round(time.time() - self.created_at, 1)
        return data


class JobStore:
    """Thread-safe registry. Jobs stay queryable so the popup can show history."""

    def __init__(self, max_history: int = 50) -> None:
        self._lock = threading.Lock()
        self._jobs: dict[str, Job] = {}
        self._order: list[str] = []
        self._max_history = max_history

    def create(self, url: str, **kwargs: Any) -> Job:
        job = Job(url=url, **kwargs)
        with self._lock:
            self._jobs[job.id] = job
            self._order.append(job.id)
            self._trim_locked()
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def list(self, states: set[State] | None = None) -> list[Job]:
        with self._lock:
            jobs = [self._jobs[i] for i in self._order if i in self._jobs]
        if states:
            jobs = [j for j in jobs if j.state in states]
        return list(reversed(jobs))

    def active(self) -> list[Job]:
        return self.list({State.queued, State.running})

    def recent(self, limit: int = 8) -> list[Job]:
        return self.list()[:limit]

    def remove(self, job_id: str) -> None:
        with self._lock:
            self._jobs.pop(job_id, None)
            if job_id in self._order:
                self._order.remove(job_id)

    def clear_finished(self) -> None:
        with self._lock:
            for job_id in [i for i in self._order
                           if self._jobs.get(i) and self._jobs[i].state in
                           {State.done, State.error, State.cancelled}]:
                self._jobs.pop(job_id, None)
                self._order.remove(job_id)

    def _trim_locked(self) -> None:
        while len(self._order) > self._max_history:
            oldest = self._order[0]
            self._order.pop(0)
            self._jobs.pop(oldest, None)
