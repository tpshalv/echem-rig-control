"""Experiment IDs, which are also the recording folder names.

An ID is ``INITIALS_YYYY-MM-DD_HHhMM`` plus an optional ``_description``,
for example ``TPS_2026-10-22_14h09_humidity-tests``. Underscores only ever
separate those parts, so ``experiment_id.split("_")`` always gives three
or four fields; inside a part, anything other than letters and digits
becomes a hyphen. The time is local and to the minute; the exact start
time, with seconds and time zone, stays in ``metadata.json``.
"""

import json
import re
import shutil
from datetime import datetime
from pathlib import Path

DESCRIPTION_MAX_LENGTH = 40


def clean_id_part(text: str) -> str:
    """Letters and digits kept; every other run of characters becomes '-'."""

    return re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-")


def make_experiment_id(
    operator: str,
    started_at: datetime,
    description: str = "",
) -> str:
    initials = clean_id_part(operator)
    if not initials:
        raise ValueError("Enter the operator's initials before recording.")
    local = started_at.astimezone()
    parts = [initials, local.strftime("%Y-%m-%d"), local.strftime("%Hh%M")]
    cleaned = clean_id_part(description)[:DESCRIPTION_MAX_LENGTH].rstrip("-")
    if cleaned:
        parts.append(cleaned)
    return "_".join(parts)


def existing_recording(root: str | Path, experiment_id: str) -> Path | None:
    path = Path(root) / experiment_id
    return path if path.exists() else None


def describe_recording(path: Path) -> str:
    """A short line to help decide whether an existing recording can go."""

    details = []
    try:
        metadata = json.loads((path / "metadata.json").read_text(encoding="utf-8"))
        started = datetime.fromisoformat(metadata["started_at"]).astimezone()
        details.append(f"started {started:%Y-%m-%d %H:%M:%S}")
    except (OSError, ValueError, KeyError, TypeError):
        pass
    size = sum(item.stat().st_size for item in path.rglob("*") if item.is_file())
    details.append(f"{size / 1_000_000:.1f} MB" if size >= 100_000 else f"{size / 1_000:.0f} kB")
    return ", ".join(details)


def remove_recording(path: Path) -> None:
    """Delete a previous recording folder, and refuse anything else."""

    if not (path / "metadata.json").is_file():
        raise FileExistsError(
            f"{path} already exists and is not an experiment recording; "
            "it was left untouched."
        )
    shutil.rmtree(path)
