from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from collections.abc import Callable


class Quality(StrEnum):
    GOOD = "good"
    UNCERTAIN = "uncertain"
    BAD = "bad"
    STALE = "stale"


class DeviceStatus(StrEnum):
    """Current communication and operational state of a device."""

    UNKNOWN = "unknown"              # Status has not been checked yet
    CONNECTING = "connecting"        # A connection attempt is underway
    CONNECTED = "connected"          # Communication works; readiness is unconfirmed
    READY = "ready"                  # Checked and ready to operate
    DEGRADED = "degraded"            # Operational, but with a non-critical problem
    FAULTED = "faulted"              # Unable or unsafe to operate normally
    DISCONNECTED = "disconnected"    # Communication is unavailable


class EventSeverity(StrEnum):
    """Importance of an event reported by the control system."""

    INFO = "info"            # Normal activity worth recording
    WARNING = "warning"      # Operator attention is needed; operation may continue
    ERROR = "error"          # Operation is impaired and may need to pause
    CRITICAL = "critical"    # Immediate protective action may be required


@dataclass(frozen=True, slots=True)
class Measurement:
    value: float
    unit: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
    quality: Quality = Quality.GOOD


@dataclass(frozen=True, slots=True)
class Event:
    """Something noteworthy reported by the control system."""

    source: str
    message: str
    severity: EventSeverity = EventSeverity.INFO
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))


EventSink = Callable[[Event, str | None], None]
