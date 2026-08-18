import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from rig_control.data.serialization import event_to_dict
from rig_control.models import Event, EventSeverity


_LOG_LEVELS = {
    EventSeverity.INFO: logging.INFO,
    EventSeverity.WARNING: logging.WARNING,
    EventSeverity.ERROR: logging.ERROR,
    EventSeverity.CRITICAL: logging.CRITICAL,
}


class TechnicalEventLogger:
    """Write low-volume operational events to rotating JSON-lines files."""

    def __init__(
        self,
        path: str | Path,
        *,
        max_bytes: int = 1_000_000,
        backup_count: int = 5,
    ) -> None:
        if max_bytes <= 0:
            raise ValueError("Technical log maximum size must be positive")
        if backup_count < 0:
            raise ValueError("Technical log backup count cannot be negative")

        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._handler = RotatingFileHandler(
            self._path,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
        self._handler.setFormatter(logging.Formatter("%(message)s"))
        self._logger = logging.getLogger(
            f"rig_control.technical.{id(self)}"
        )
        self._logger.setLevel(logging.INFO)
        self._logger.propagate = False
        self._logger.addHandler(self._handler)
        self._is_open = True

    @property
    def path(self) -> Path:
        return self._path

    @property
    def is_open(self) -> bool:
        return self._is_open

    def record(
        self,
        event: Event,
        technical_details: str | None = None,
    ) -> None:
        """Write one event with optional troubleshooting detail."""

        if not self.is_open:
            raise RuntimeError("Technical event log is closed")
        if not isinstance(event, Event):
            raise TypeError("Technical event logger requires an Event")
        if technical_details is not None and not isinstance(
            technical_details,
            str,
        ):
            raise TypeError("Technical event details must be text or None")

        data = event_to_dict(event)
        if technical_details:
            data["technical_details"] = technical_details

        self._logger.log(
            _LOG_LEVELS[event.severity],
            json.dumps(data, ensure_ascii=False, separators=(",", ":")),
        )

    def close(self) -> None:
        if not self.is_open:
            return
        self._handler.flush()
        self._handler.close()
        self._logger.removeHandler(self._handler)
        self._is_open = False

    def __enter__(self) -> "TechnicalEventLogger":
        return self

    def __exit__(self, *_error: object) -> None:
        self.close()
