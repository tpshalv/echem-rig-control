"""Operator notes made during a run, and the optional review written after it.

Notes record what the software cannot see for itself (a line reconnected, a
probe refilled). They live in their own append-only journal, next to the
measurements, and are linked to the data only by timestamp: a note never
changes a reading. Corrections are new records that point back to the note
they replace, so the journal keeps what was changed and when. See decision
0019 for the reasoning.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
import json
import os
from pathlib import Path
from typing import Any
from uuid import uuid4


ANNOTATIONS_JOURNAL = "annotations.journal.jsonl"
RUN_REVIEW_JOURNAL = "run-review.journal.jsonl"

ANNOTATION_KINDS = ("lab_note", "qc_flag")
ANNOTATION_KIND_LABELS = {"lab_note": "Lab note", "qc_flag": "QC flag"}
ANNOTATION_ORIGINS = ("typed", "graph")
ANNOTATION_ACTIONS = ("create", "edit", "delete")
MAX_ANNOTATION_TEXT = 2_000

OVERALL_QC_LEVELS = ("not_assessed", "none", "minor", "severe", "failure")
OVERALL_QC_LABELS = {
    "not_assessed": "Not assessed",
    "none": "None",
    "minor": "Minor",
    "severe": "Severe",
    "failure": "Failure",
}

# Steps used to round a click's time resolution up to a readable figure.
UNCERTAINTY_STEPS = (
    1, 2, 5, 10, 20, 30,
    60, 120, 300, 600, 1_200, 1_800,
    3_600, 7_200, 18_000, 36_000, 86_400,
)


@dataclass(frozen=True, slots=True)
class AnnotationRecord:
    """One line of the annotations journal: a note created, edited or deleted.

    Create and edit records carry the note's whole state, so resolving the
    journal never has to merge partial changes.
    """

    annotation_id: str
    action: str
    recorded_at: datetime
    timestamp: datetime | None = None
    kind: str | None = None
    text: str | None = None
    origin: str | None = None
    uncertainty_seconds: float | None = None
    device_id: str | None = None
    channel: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.annotation_id, str) or not self.annotation_id.strip():
            raise ValueError("Note ID must be non-empty text")
        if self.action not in ANNOTATION_ACTIONS:
            raise ValueError(f"Unknown note action {self.action!r}")
        _require_aware(self.recorded_at, "Note record time")
        if self.action == "delete":
            return
        _require_aware(self.timestamp, "Note time")
        if self.kind not in ANNOTATION_KINDS:
            raise ValueError(f"Unknown note type {self.kind!r}")
        if self.origin not in ANNOTATION_ORIGINS:
            raise ValueError(f"Unknown note origin {self.origin!r}")
        if not isinstance(self.text, str) or not self.text.strip():
            raise ValueError("A note needs some text")
        if len(self.text) > MAX_ANNOTATION_TEXT:
            raise ValueError(
                f"A note can be at most {MAX_ANNOTATION_TEXT} characters"
            )
        if self.uncertainty_seconds is not None and (
            isinstance(self.uncertainty_seconds, bool)
            or not isinstance(self.uncertainty_seconds, (int, float))
            or self.uncertainty_seconds < 0
        ):
            raise ValueError("Note uncertainty must be a non-negative number")
        if (self.device_id is None) != (self.channel is None):
            raise ValueError("A note's signal needs both a device and a channel")


@dataclass(frozen=True, slots=True)
class Annotation:
    """The current state of one note after applying its corrections."""

    annotation_id: str
    timestamp: datetime
    kind: str
    text: str
    origin: str
    uncertainty_seconds: float | None
    device_id: str | None
    channel: str | None
    created_at: datetime
    edited: bool

    @property
    def kind_label(self) -> str:
        return ANNOTATION_KIND_LABELS[self.kind]

    @property
    def signal(self) -> tuple[str, str] | None:
        if self.device_id is None or self.channel is None:
            return None
        return (self.device_id, self.channel)


@dataclass(frozen=True, slots=True)
class RunReview:
    """The operator's overall judgement of a run, written after it stops."""

    overall_qc: str
    end_notes: str
    reviewed_at: datetime

    def __post_init__(self) -> None:
        if self.overall_qc not in OVERALL_QC_LEVELS:
            raise ValueError(f"Unknown overall QC level {self.overall_qc!r}")
        if not isinstance(self.end_notes, str):
            raise TypeError("End notes must be text")
        _require_aware(self.reviewed_at, "Review time")

    @property
    def overall_qc_label(self) -> str:
        return OVERALL_QC_LABELS[self.overall_qc]


def new_annotation(
    *,
    kind: str,
    text: str,
    timestamp: datetime,
    origin: str,
    uncertainty_seconds: float | None = None,
    signal: tuple[str, str] | None = None,
    recorded_at: datetime | None = None,
) -> AnnotationRecord:
    device_id, channel = signal if signal is not None else (None, None)
    return AnnotationRecord(
        annotation_id=uuid4().hex,
        action="create",
        recorded_at=recorded_at or datetime.now(UTC),
        timestamp=timestamp,
        kind=kind,
        text=text.strip(),
        origin=origin,
        uncertainty_seconds=uncertainty_seconds,
        device_id=device_id,
        channel=channel,
    )


def edited_annotation(
    current: Annotation,
    *,
    kind: str | None = None,
    text: str | None = None,
    timestamp: datetime | None = None,
    origin: str | None = None,
    uncertainty_seconds: float | None = None,
    signal: tuple[str, str] | None = None,
    recorded_at: datetime | None = None,
) -> AnnotationRecord:
    """Return an edit record; a new time also replaces how it was placed."""

    moved = timestamp is not None
    device_id, channel = (
        signal if signal is not None
        else (None, None) if moved
        else (current.device_id, current.channel)
    )
    return AnnotationRecord(
        annotation_id=current.annotation_id,
        action="edit",
        recorded_at=recorded_at or datetime.now(UTC),
        timestamp=timestamp or current.timestamp,
        kind=kind or current.kind,
        text=(text if text is not None else current.text).strip(),
        origin=origin or current.origin,
        uncertainty_seconds=(
            uncertainty_seconds if moved else current.uncertainty_seconds
        ),
        device_id=device_id,
        channel=channel,
    )


def deleted_annotation(
    annotation_id: str,
    *,
    recorded_at: datetime | None = None,
) -> AnnotationRecord:
    return AnnotationRecord(
        annotation_id=annotation_id,
        action="delete",
        recorded_at=recorded_at or datetime.now(UTC),
    )


def resolve_annotations(
    records: Iterable[AnnotationRecord],
) -> tuple[Annotation, ...]:
    """Apply records in journal order and return the live notes by time.

    A correction for an unknown or already-deleted note is ignored rather
    than failing, so one bad line never hides the rest of a run's notes.
    """

    notes: dict[str, Annotation] = {}
    deleted: set[str] = set()
    for record in records:
        key = record.annotation_id
        if key in deleted:
            continue
        if record.action == "delete":
            notes.pop(key, None)
            deleted.add(key)
            continue
        existing = notes.get(key)
        if record.action == "create" and existing is not None:
            continue
        if record.action == "edit" and existing is None:
            continue
        assert record.timestamp is not None
        assert record.kind is not None and record.text is not None
        assert record.origin is not None
        notes[key] = Annotation(
            annotation_id=key,
            timestamp=record.timestamp,
            kind=record.kind,
            text=record.text,
            origin=record.origin,
            uncertainty_seconds=record.uncertainty_seconds,
            device_id=record.device_id,
            channel=record.channel,
            created_at=(
                record.recorded_at if existing is None else existing.created_at
            ),
            edited=existing is not None,
        )
    return tuple(sorted(
        notes.values(), key=lambda note: (note.timestamp, note.created_at)
    ))


def nice_uncertainty(seconds: float) -> float:
    """Round a time resolution up to the next readable step."""

    if seconds <= 0:
        return 0.0
    for step in UNCERTAINTY_STEPS:
        if seconds <= step:
            return float(step)
    return float(UNCERTAINTY_STEPS[-1])


def format_duration(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:g} s"
    if seconds < 3_600:
        return f"{seconds / 60:g} min"
    return f"{seconds / 3_600:g} h"


def format_annotation_cell(note: Annotation) -> str:
    """Compact text for a note in the wide CSV and the Excel data sheet."""

    text = note.text.replace("\r", " ").replace("\n", " ")
    if note.uncertainty_seconds:
        text += f" (±{format_duration(note.uncertainty_seconds)})"
    if note.edited:
        text += " [edited]"
    return text


# Journals ------------------------------------------------------------------


def annotation_record_to_dict(record: AnnotationRecord) -> dict[str, Any]:
    return {
        "annotation_id": record.annotation_id,
        "action": record.action,
        "recorded_at": record.recorded_at.isoformat(),
        "timestamp": (
            record.timestamp.isoformat() if record.timestamp is not None else None
        ),
        "kind": record.kind,
        "text": record.text,
        "origin": record.origin,
        "uncertainty_seconds": record.uncertainty_seconds,
        "device_id": record.device_id,
        "channel": record.channel,
    }


def annotation_record_from_dict(data: dict[str, Any]) -> AnnotationRecord:
    timestamp = data.get("timestamp")
    return AnnotationRecord(
        annotation_id=data["annotation_id"],
        action=data["action"],
        recorded_at=datetime.fromisoformat(data["recorded_at"]),
        timestamp=datetime.fromisoformat(timestamp) if timestamp else None,
        kind=data.get("kind"),
        text=data.get("text"),
        origin=data.get("origin"),
        uncertainty_seconds=data.get("uncertainty_seconds"),
        device_id=data.get("device_id"),
        channel=data.get("channel"),
    )


def run_review_to_dict(review: RunReview) -> dict[str, Any]:
    return {
        "overall_qc": review.overall_qc,
        "end_notes": review.end_notes,
        "reviewed_at": review.reviewed_at.isoformat(),
    }


def run_review_from_dict(data: dict[str, Any]) -> RunReview:
    return RunReview(
        overall_qc=data["overall_qc"],
        end_notes=data["end_notes"],
        reviewed_at=datetime.fromisoformat(data["reviewed_at"]),
    )


def read_annotation_journal(
    directory: str | Path,
) -> tuple[tuple[AnnotationRecord, ...], int]:
    """Read complete journal lines and return them with the bytes consumed.

    A run recorded before notes existed simply has no journal. A final line
    still being written is left for the next read.
    """

    path = Path(directory) / ANNOTATIONS_JOURNAL
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return (), 0
    end = data.rfind(b"\n") + 1
    records = tuple(
        annotation_record_from_dict(_json_object(line, path))
        for line in data[:end].splitlines()
        if line.strip()
    )
    return records, end


def read_annotations(directory: str | Path) -> tuple[Annotation, ...]:
    records, _size = read_annotation_journal(directory)
    return resolve_annotations(records)


def append_run_review(directory: str | Path, review: RunReview) -> None:
    """Add a review; the newest one is the run's review in every export."""

    path = Path(directory) / RUN_REVIEW_JOURNAL
    line = json.dumps(run_review_to_dict(review), ensure_ascii=False,
                      separators=(",", ":"))
    with path.open("a", encoding="utf-8") as stream:
        stream.write(f"{line}\n")
        stream.flush()
        os.fsync(stream.fileno())


def latest_run_review(directory: str | Path) -> RunReview | None:
    path = Path(directory) / RUN_REVIEW_JOURNAL
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return None
    lines = [line for line in data[: data.rfind(b"\n") + 1].splitlines()
             if line.strip()]
    if not lines:
        return None
    return run_review_from_dict(_json_object(lines[-1], path))


def _json_object(line: bytes, path: Path) -> dict[str, Any]:
    value = json.loads(line)
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} contains a non-object record")
    return value


def _require_aware(value: object, label: str) -> None:
    if not isinstance(value, datetime):
        raise TypeError(f"{label} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must include a timezone")
