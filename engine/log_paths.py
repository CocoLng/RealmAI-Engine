"""Single source of truth for the runtime log directory.

Everything that writes a file under ``logs/`` at runtime — the bot's
per-launch log, the narrator parse-failure dumps, the beat-progression
telemetry — resolves its path through :func:`log_dir`.

Why an indirection for one constant: the path is resolved **at call time**
from ``REALM_LOG_DIR`` (default ``logs``), which lets the test suite point
the whole tree at a temp directory. Before this, ``pytest`` appended
thousands of synthetic decisions into the very file
``scripts/review_beat_progression.py`` aggregates as production telemetry,
and left a real ``realm_*.log`` per run containing MagicMock noise and
deliberate test tracebacks. Two diagnostics were derailed by that.

This lives in ``engine/`` because it is the lowest layer that needs it —
``engine`` may never import ``bot`` or ``ai``.
"""

from __future__ import annotations

import os
from pathlib import Path

#: Environment variable that redirects every runtime log sink.
LOG_DIR_ENV = "REALM_LOG_DIR"

DEFAULT_LOG_DIR = "logs"


def log_dir() -> Path:
    """Return the directory runtime logs are written to.

    Resolved on every call so that setting ``REALM_LOG_DIR`` after import
    still takes effect.
    """
    return Path(os.environ.get(LOG_DIR_ENV, DEFAULT_LOG_DIR))
