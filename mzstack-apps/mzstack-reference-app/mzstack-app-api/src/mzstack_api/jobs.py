"""Background jobs for work that outlives a request.

Converting a directory of mzML takes minutes; a MetaboLights study downloads
over FTP and can take much longer. Neither fits in a request, so both run on a
small thread pool and the client polls for the outcome.

Jobs live in memory and are lost on restart. The artifact is the dataset
directory on disk; a job record is a progress report about producing it.
"""

from __future__ import annotations

import contextlib
import io
import logging
import threading
import traceback
import uuid
from collections import OrderedDict, deque
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .errors import NotFound

__all__ = ["Job", "JobRegistry"]

log = logging.getLogger(__name__)

QUEUED = "queued"
RUNNING = "running"
SUCCEEDED = "succeeded"
FAILED = "failed"

#: Log lines retained per job. Ingest is verbose -- one line per file -- and
#: only the tail is ever useful.
MAX_LOG_LINES = 500


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Job:
    """One unit of background work, and what became of it."""

    id: str
    kind: str
    params: dict[str, Any]
    status: str = QUEUED
    created_at: str = field(default_factory=_now)
    started_at: str | None = None
    finished_at: str | None = None
    result: dict[str, Any] | None = None
    error: str | None = None
    log: deque[str] = field(default_factory=lambda: deque(maxlen=MAX_LOG_LINES))

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "params": self.params,
            "status": self.status,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "result": self.result,
            "error": self.error,
            "log": list(self.log),
        }


class _LogStream(io.TextIOBase):
    """Collects what an ingest prints, a line at a time, into a job."""

    def __init__(self, job: Job, lock: threading.Lock) -> None:
        self._job = job
        self._lock = lock
        self._buffer = ""

    def write(self, text: str) -> int:  # noqa: D102 - TextIOBase
        self._buffer += text
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            if line.strip():
                with self._lock:
                    self._job.log.append(line.rstrip())
        return len(text)

    def flush(self) -> None:
        if self._buffer.strip():
            with self._lock:
                self._job.log.append(self._buffer.rstrip())
            self._buffer = ""


class JobRegistry:
    """Runs jobs on a small pool and remembers the last `history` of them."""

    def __init__(self, max_workers: int = 2, history: int = 50) -> None:
        self._pool = ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="mzstack-job"
        )
        self._jobs: OrderedDict[str, Job] = OrderedDict()
        self._history = history
        self._lock = threading.Lock()

    def submit(
        self,
        kind: str,
        params: dict[str, Any],
        work: Callable[[Job], dict[str, Any] | None],
    ) -> Job:
        """Queue `work`, which receives the job so it can report progress."""
        job = Job(id=uuid.uuid4().hex, kind=kind, params=params)
        with self._lock:
            self._jobs[job.id] = job
            while len(self._jobs) > self._history:
                # Never evict something still in flight.
                for id_, candidate in list(self._jobs.items()):
                    if candidate.status in (SUCCEEDED, FAILED):
                        del self._jobs[id_]
                        break
                else:
                    break
        self._pool.submit(self._run, job, work)
        return job

    def get(self, job_id: str) -> Job:
        with self._lock:
            job = self._jobs.get(job_id)
        if job is None:
            raise NotFound(f"No such job: {job_id!r}")
        return job

    def all(self) -> list[Job]:
        with self._lock:
            return list(reversed(self._jobs.values()))

    def append_log(self, job: Job, line: str) -> None:
        with self._lock:
            job.log.append(line)

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)

    # -- internals ------------------------------------------------------------

    def _run(self, job: Job, work: Callable[[Job], dict[str, Any] | None]) -> None:
        with self._lock:
            job.status = RUNNING
            job.started_at = _now()

        stream = _LogStream(job, self._lock)
        try:
            # mzstack's ingest functions report progress on stdout; capture it
            # rather than letting it disappear into the server's console.
            with contextlib.redirect_stdout(stream):
                result = work(job)
            stream.flush()
            with self._lock:
                job.result = result or {}
                job.status = SUCCEEDED
        except Exception as exc:  # noqa: BLE001 - the job records every failure
            stream.flush()
            log.exception("job %s (%s) failed", job.id, job.kind)
            with self._lock:
                job.error = str(exc) or exc.__class__.__name__
                job.status = FAILED
                job.log.append(traceback.format_exc().strip().splitlines()[-1])
        finally:
            with self._lock:
                job.finished_at = _now()
