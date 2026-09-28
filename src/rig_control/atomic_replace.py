"""Finish an atomic file write on Windows, where the rename can be refused.

Every saved file here is written to a temporary file and renamed over the
real one, so a crash mid-write leaves either the old complete file or the new
one, never a truncated file. On Windows that rename intermittently raises
PermissionError because antivirus or the search indexer still holds the
existing file open for a moment.

Retrying keeps the atomic rename rather than deleting the destination first,
which would open exactly the window the rename exists to close.
"""

from pathlib import Path
from time import sleep

ATTEMPTS = 10
RETRY_DELAY_SECONDS = 0.05


def replace_atomically(
    temporary_path: Path,
    destination: Path,
    *,
    attempts: int = ATTEMPTS,
    delay_seconds: float = RETRY_DELAY_SECONDS,
) -> None:
    """Rename one temporary file over another, retrying a held destination.

    The final attempt lets PermissionError propagate: a file that stays
    locked is a real failure the caller must report, not one to hide.
    """

    if attempts < 1:
        raise ValueError("Atomic replace needs at least one attempt")
    for attempt in range(attempts):
        try:
            temporary_path.replace(destination)
            return
        except PermissionError:
            if attempt == attempts - 1:
                raise
            sleep(delay_seconds)
