from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
import pytest
from rig_control.models import (
    DeviceStatus,
    Event,
    EventSeverity,
    Measurement,
    Quality,
)


def test_quality_values_are_stable() -> None:
    assert Quality.GOOD == "good"
    assert Quality.UNCERTAIN == "uncertain"
    assert Quality.BAD == "bad"
    assert Quality.STALE == "stale"


def test_measurement_has_sensible_defaults() -> None:
    reading = Measurement(value=25.4, unit="degC")

    assert reading.value == 25.4
    assert reading.unit == "degC"
    assert reading.quality is Quality.GOOD
    assert reading.timestamp.tzinfo is UTC


def test_measurement_cannot_be_changed_after_creation() -> None:
    reading = Measurement(value=25.4, unit="degC")

    with pytest.raises(FrozenInstanceError):
        reading.value = 30.0


def test_event_has_sensible_defaults() -> None:
    event = Event(
        source="temperature_controller",
        message="Controller connected",
    )

    assert event.source == "temperature_controller"
    assert event.message == "Controller connected"
    assert event.severity is EventSeverity.INFO
    assert event.timestamp.tzinfo is UTC


def test_device_status_values_are_stable() -> None:
    assert DeviceStatus.UNKNOWN == "unknown"
    assert DeviceStatus.CONNECTING == "connecting"
    assert DeviceStatus.CONNECTED == "connected"
    assert DeviceStatus.READY == "ready"
    assert DeviceStatus.DEGRADED == "degraded"
    assert DeviceStatus.FAULTED == "faulted"
    assert DeviceStatus.DISCONNECTED == "disconnected"


def test_event_severity_values_are_stable() -> None:
    assert EventSeverity.INFO == "info"
    assert EventSeverity.WARNING == "warning"
    assert EventSeverity.ERROR == "error"
    assert EventSeverity.CRITICAL == "critical"


def test_measurement_accepts_explicit_quality_and_timestamp() -> None:
    recorded_at = datetime(2026, 8, 13, 12, 0, tzinfo=UTC)

    reading = Measurement(
        value=48.2,
        unit="sccm",
        timestamp=recorded_at,
        quality=Quality.UNCERTAIN,
    )

    assert reading.timestamp is recorded_at
    assert reading.quality is Quality.UNCERTAIN


def test_event_accepts_explicit_severity() -> None:
    event = Event(
        source="temperature_controller",
        message="Temperature exceeded warning limit",
        severity=EventSeverity.WARNING,
    )

    assert event.severity is EventSeverity.WARNING


def test_event_cannot_be_changed_after_creation() -> None:
    event = Event(source="system", message="Experiment started")

    with pytest.raises(FrozenInstanceError):
        event.message = "Changed"