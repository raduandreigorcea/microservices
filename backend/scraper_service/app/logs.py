"""How this service talks to its container log.

Everything a job does goes to stdout as one line per step, tagged with the
job it belongs to, because several jobs share the process and would otherwise
be impossible to tell apart.
"""

from __future__ import annotations

import logging
from contextvars import ContextVar
from urllib.parse import urlsplit

# Set for the duration of one job. Every line logged inside that job's task
# picks it up, however deep in the call stack it was written.
job_tag: ContextVar[str] = ContextVar("job_tag", default="")

LOG_FORMAT = "%(asctime)s %(levelname)-7s %(jobtag)s%(message)s"
DATE_FORMAT = "%H:%M:%S"

HEALTH_PATHS = frozenset({"/health", "/health/ready"})

# Longer than this and the line stops being readable.
MAX_URL_CHARS = 120


class JobTagFilter(logging.Filter):
    """Puts the current job's tag on every record the handler formats."""

    def filter(self, record: logging.LogRecord) -> bool:
        tag = job_tag.get()
        record.jobtag = f"[{tag}] " if tag else ""
        return True


class HealthAccessFilter(logging.Filter):
    """Drops the access line the container's health check writes every 10s.

    A health check that fails is still worth seeing, so only successful ones
    are dropped.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if not isinstance(args, tuple) or len(args) < 5:
            return True
        path, status = args[2], args[4]
        if not isinstance(path, str) or path.split("?")[0] not in HEALTH_PATHS:
            return True
        return not (isinstance(status, int) and 200 <= status < 400)


def configure_logging(level: str = "INFO") -> None:
    """Point the root logger at stdout in our format. Safe to call twice."""
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(LOG_FORMAT, DATE_FORMAT))
    handler.addFilter(JobTagFilter())

    root = logging.getLogger()
    for existing in root.handlers[:]:
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(level)

    # uvicorn keeps its own handlers and does not propagate, so the filter has
    # to go on its logger rather than on ours.
    access = logging.getLogger("uvicorn.access")
    if not any(isinstance(f, HealthAccessFilter) for f in access.filters):
        access.addFilter(HealthAccessFilter())


def short_url(url: str) -> str:
    """Host and path, without the scheme that is the same on every line."""
    parts = urlsplit(url)
    out = f"{parts.netloc}{parts.path}"
    if parts.query:
        out = f"{out}?{parts.query}"
    if len(out) > MAX_URL_CHARS:
        out = f"{out[: MAX_URL_CHARS - 1]}…"
    return out


def kb(size: int) -> str:
    return f"{size / 1024:.1f}kb"


def tag_for(job_id) -> str:
    return str(job_id)[:8]
