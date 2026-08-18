import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from rig_control.event_logging import TechnicalEventLogger
from rig_control.models import Event, EventSeverity


FIXED_TIME = datetime(2026, 8, 18, 14, 30, tzinfo=UTC)


def event(message: str = "Device communication recovered") -> Event:
    return Event(
        source="mfc_a",
        message=message,
        severity=EventSeverity.WARNING,
        timestamp=FIXED_TIME,
    )


def test_event_is_written_as_one_json_line(tmp_path: Path) -> None:
    path = tmp_path / "logs" / "rig-control.log"
    logger = TechnicalEventLogger(path)

    logger.record(event(), "serial response: A 0.0")
    logger.close()

    stored = json.loads(path.read_text(encoding="utf-8"))
    assert stored == {
        "source": "mfc_a",
        "timestamp": "2026-08-18T14:30:00+00:00",
        "severity": "warning",
        "message": "Device communication recovered",
        "technical_details": "serial response: A 0.0",
    }


def test_unicode_is_preserved(tmp_path: Path) -> None:
    path = tmp_path / "rig-control.log"

    with TechnicalEventLogger(path) as logger:
        logger.record(event("Temperature is 25 °C — stable"))

    assert "25 °C — stable" in path.read_text(encoding="utf-8")


def test_log_rotates_when_size_limit_is_reached(tmp_path: Path) -> None:
    path = tmp_path / "rig-control.log"

    with TechnicalEventLogger(
        path,
        max_bytes=150,
        backup_count=2,
    ) as logger:
        for index in range(10):
            logger.record(event(f"Operational event number {index}"))

    assert path.exists()
    assert path.with_name("rig-control.log.1").exists()


def test_close_is_idempotent_and_prevents_more_writes(
    tmp_path: Path,
) -> None:
    logger = TechnicalEventLogger(tmp_path / "rig-control.log")

    logger.close()
    logger.close()

    assert logger.is_open is False
    with pytest.raises(RuntimeError, match="closed"):
        logger.record(event())


def test_invalid_rotation_settings_are_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="maximum size"):
        TechnicalEventLogger(tmp_path / "log", max_bytes=0)
    with pytest.raises(ValueError, match="backup count"):
        TechnicalEventLogger(tmp_path / "log", backup_count=-1)


def test_logger_validates_event_and_details(tmp_path: Path) -> None:
    with TechnicalEventLogger(tmp_path / "log") as logger:
        with pytest.raises(TypeError, match="requires an Event"):
            logger.record(object())  # type: ignore[arg-type]
        with pytest.raises(TypeError, match="details"):
            logger.record(event(), 42)  # type: ignore[arg-type]
