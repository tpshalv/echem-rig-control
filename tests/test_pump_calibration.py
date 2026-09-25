from datetime import UTC, datetime
from pathlib import Path
from threading import Barrier, Thread
from unittest.mock import patch

import pytest

from rig_control.devices.base import Device
from rig_control.devices.manager import DeviceManager
from rig_control.devices.pump import Pump, PumpDirection, PumpLimits
from rig_control.devices.pump_calibration import (
    CalibrationPoint,
    LinearFit,
    PumpCalibration,
    fit_linear,
)
from rig_control.instrument_settings.pump_calibration import (
    PumpCalibrationService,
    PumpCalibrationSettingsService,
)
from rig_control.models import DeviceStatus, Event
from rig_control.rig_profile import RigProfile


class _StubPump(Pump):
    """Minimal Pump for settings-service tests -- no real I/O."""

    def __init__(self, device_id: str) -> None:
        self._device_id = device_id

    @property
    def device_id(self) -> str:
        return self._device_id

    @property
    def status(self) -> DeviceStatus:
        return DeviceStatus.READY

    def connect(self) -> None:
        pass

    def disconnect(self) -> None:
        pass

    @property
    def limits(self) -> PumpLimits:
        return PumpLimits(100.0)

    @property
    def speed_setpoint_rpm(self) -> float | None:
        return None

    def set_speed_rpm(self, rpm: float) -> None:
        pass

    @property
    def direction(self) -> PumpDirection | None:
        return None

    def set_direction(self, direction: PumpDirection) -> None:
        pass

    @property
    def running(self) -> bool | None:
        return None

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def enter_safe_state(self) -> None:
        pass


# --- Fitting math ---

def test_fit_linear_recovers_an_exact_line():
    points = [CalibrationPoint(rpm, 2.0 * rpm + 1.0) for rpm in (10.0, 20.0, 30.0, 40.0)]
    fit = fit_linear(points)
    assert fit.slope == pytest.approx(2.0)
    assert fit.intercept == pytest.approx(1.0)
    assert fit.r_squared == pytest.approx(1.0)


def test_fit_linear_scores_noisy_points_below_perfect():
    points = [
        CalibrationPoint(10.0, 20.5),
        CalibrationPoint(20.0, 39.0),
        CalibrationPoint(30.0, 61.5),
        CalibrationPoint(40.0, 78.0),
    ]
    fit = fit_linear(points)
    assert 0.0 < fit.r_squared < 1.0


def test_fit_linear_round_trips_speed_and_flow():
    fit = LinearFit(slope=1.5, intercept=2.0, r_squared=1.0)
    assert fit.flow_for_speed(10.0) == pytest.approx(17.0)
    assert fit.speed_for_flow(17.0) == pytest.approx(10.0)


def test_fit_linear_requires_at_least_two_points():
    with pytest.raises(ValueError):
        fit_linear([CalibrationPoint(10.0, 20.0)])


def test_fit_linear_requires_more_than_one_distinct_speed():
    with pytest.raises(ValueError):
        fit_linear([CalibrationPoint(10.0, 20.0), CalibrationPoint(10.0, 22.0)])


def test_fit_linear_rejects_a_non_positive_slope():
    points = [CalibrationPoint(10.0, 50.0), CalibrationPoint(40.0, 10.0)]
    with pytest.raises(ValueError):
        fit_linear(points)


def test_calibration_point_rejects_negative_or_non_finite_values():
    with pytest.raises(ValueError):
        CalibrationPoint(-1.0, 5.0)
    with pytest.raises(ValueError):
        CalibrationPoint(10.0, float("nan"))


def test_calibration_requires_at_least_two_retained_points():
    fit = LinearFit(1.0, 0.0, 1.0)
    with pytest.raises(ValueError):
        PumpCalibration("id1", "pump1", "operator", (CalibrationPoint(10.0, 10.0),), fit)


@pytest.mark.parametrize("slope", [0.0, -1.0])
def test_linear_fit_rejects_non_positive_slope_even_off_the_fitting_path(slope):
    # Guards a calibration history file that was hand-edited or corrupted:
    # this must fail at load time, not divide by zero in speed_for_flow().
    with pytest.raises(ValueError):
        LinearFit(slope, 0.0, 1.0)


def test_linear_fit_rejects_non_finite_values():
    with pytest.raises(ValueError):
        LinearFit(float("nan"), 0.0, 1.0)


# --- Storage service: fit, review, accept/decline ---

def make_service(tmp_path: Path) -> PumpCalibrationService:
    return PumpCalibrationService(directory=tmp_path)


def points() -> list[CalibrationPoint]:
    return [CalibrationPoint(rpm, 2.0 * rpm + 1.0) for rpm in (10.0, 20.0, 30.0, 40.0)]


def test_new_pump_has_no_history_or_active_calibration(tmp_path):
    service = make_service(tmp_path)
    assert service.history("pump1") == ()
    assert service.active_calibration("pump1") is None


def test_accepted_candidate_becomes_active_and_is_persisted(tmp_path):
    service = make_service(tmp_path)
    candidate = service.fit_candidate("pump1", "operator", points())
    result = service.accept(candidate)
    assert result.succeeded
    active = service.active_calibration("pump1")
    assert active is not None
    assert active.fit.slope == pytest.approx(2.0)
    assert active.operator == "operator"
    assert service.history("pump1") == (active,)

    # A fresh service instance reading the same directory sees the same history.
    reloaded = make_service(tmp_path)
    assert reloaded.active_calibration("pump1").calibration_id == active.calibration_id


def test_declining_a_candidate_writes_nothing(tmp_path):
    service = make_service(tmp_path)
    service.fit_candidate("pump1", "operator", points())
    # Never call accept() -- simulates the operator declining after review.
    assert service.history("pump1") == ()
    assert service.active_calibration("pump1") is None


def test_most_recently_accepted_calibration_is_active(tmp_path):
    service = make_service(tmp_path)
    first = service.fit_candidate("pump1", "operator", points())
    service.accept(first)
    second_points = [CalibrationPoint(rpm, 3.0 * rpm) for rpm in (10.0, 20.0, 30.0)]
    second = service.fit_candidate("pump1", "operator", second_points)
    service.accept(second)

    history = service.history("pump1")
    assert len(history) == 2
    active = service.active_calibration("pump1")
    assert active.fit.slope == pytest.approx(3.0)
    assert active.calibration_id == history[-1].calibration_id


def test_fit_candidate_rejects_bad_points_before_anything_is_saved(tmp_path):
    service = make_service(tmp_path)
    with pytest.raises(ValueError):
        service.fit_candidate("pump1", "operator", [CalibrationPoint(10.0, 10.0)])
    assert service.history("pump1") == ()


def test_calibrations_for_different_pumps_do_not_collide(tmp_path):
    service = make_service(tmp_path)
    service.accept(service.fit_candidate("pump1", "operator", points()))
    assert service.history("pump2") == ()
    assert service.active_calibration("pump1") is not None


def test_concurrent_accepts_do_not_lose_a_calibration(tmp_path):
    service = make_service(tmp_path)
    barrier = Barrier(2)

    def accept_one(intercept: float) -> None:
        candidate = service.fit_candidate(
            "pump1", "operator",
            [CalibrationPoint(rpm, 2.0 * rpm + intercept) for rpm in (10.0, 20.0, 30.0)],
        )
        barrier.wait()  # Maximize the chance both threads race the same read-modify-write.
        service.accept(candidate)

    threads = [Thread(target=accept_one, args=(intercept,)) for intercept in (1.0, 2.0)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(service.history("pump1")) == 2


def test_history_file_rejects_wrong_format(tmp_path):
    service = make_service(tmp_path)
    (tmp_path / "pump1.json").write_text('{"format": "something-else"}', encoding="utf-8")
    with pytest.raises(ValueError):
        service.history("pump1")


def test_write_leaves_no_temp_file_behind_on_success(tmp_path):
    service = make_service(tmp_path)
    service.accept(service.fit_candidate("pump1", "operator", points()))
    assert list(tmp_path.iterdir()) == [tmp_path / "pump1.json"]


def test_a_crash_mid_write_never_leaves_a_corrupted_history_file(tmp_path):
    # Simulates a crash/power loss partway through writing the temp file:
    # the real history file must still hold the last successfully accepted
    # calibration, not a truncated or missing one.
    service = make_service(tmp_path)
    service.accept(service.fit_candidate("pump1", "operator", points()))
    good_contents = (tmp_path / "pump1.json").read_text(encoding="utf-8")

    real_write_text = Path.write_text

    def crash_after_partial_write(self, *args, **kwargs):
        if self.name.startswith("pump1.json.tmp-"):
            raise OSError("simulated crash mid write")
        return real_write_text(self, *args, **kwargs)

    with patch.object(Path, "write_text", crash_after_partial_write):
        result = service.accept(service.fit_candidate("pump1", "operator", points()))
    assert result.succeeded is False

    assert (tmp_path / "pump1.json").read_text(encoding="utf-8") == good_contents
    assert not any(p.name.startswith("pump1.json.tmp-") for p in tmp_path.iterdir())
    assert len(service.history("pump1")) == 1


def test_duplicate_calibration_ids_are_disambiguated(tmp_path, monkeypatch):
    service = make_service(tmp_path)
    fixed_time = datetime(2026, 9, 22, 10, 0, 0, tzinfo=UTC)

    class _FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_time

    monkeypatch.setattr("rig_control.instrument_settings.pump_calibration.datetime", _FixedDatetime)
    service.accept(service.fit_candidate("pump1", "operator", points()))
    service.accept(service.fit_candidate("pump1", "operator", points()))
    history = service.history("pump1")
    assert len(history) == 2
    assert history[0].calibration_id != history[1].calibration_id


# --- Settings-layer wrapper: pump listing, write-access gating, audit log ---

def make_settings_service(tmp_path, *, allow_write=True, event_sink=None):
    manager = DeviceManager()
    manager.register(_StubPump("pump1"))
    manager.register(_StubPump("pump2"))

    def require_write_access() -> None:
        if not allow_write:
            raise RuntimeError("Close active feature screens first.")

    return PumpCalibrationSettingsService(
        manager, RigProfile("rig", "Rig", (), ()),
        PumpCalibrationService(directory=tmp_path),
        require_write_access=require_write_access,
        event_sink=event_sink,
    ), manager


def test_settings_service_lists_only_pump_devices(tmp_path):
    service, manager = make_settings_service(tmp_path)
    manager.register(_NonPumpDevice("not_a_pump"))
    assert service.device_ids() == ("pump1", "pump2")


class _NonPumpDevice(Device):
    def __init__(self, device_id: str) -> None:
        self._device_id = device_id

    @property
    def device_id(self) -> str:
        return self._device_id

    @property
    def status(self) -> DeviceStatus:
        return DeviceStatus.READY

    def connect(self) -> None:
        pass

    def disconnect(self) -> None:
        pass


def test_settings_service_accept_is_blocked_without_write_access(tmp_path):
    service, _ = make_settings_service(tmp_path, allow_write=False)
    candidate = service.fit_candidate("pump1", "operator", points())

    result = service.accept(candidate)

    assert result.succeeded is False
    assert service.history("pump1") == ()


def test_settings_service_accept_logs_an_audit_event(tmp_path):
    logged = []
    service, _ = make_settings_service(
        tmp_path, event_sink=lambda event, details: logged.append(event)
    )
    candidate = service.fit_candidate("pump1", "operator", points())

    result = service.accept(candidate)

    assert result.succeeded is True
    assert len(logged) == 1
    event = logged[0]
    assert isinstance(event, Event)
    assert event.source == "pump1"
    assert "accept_pump_calibration" in event.message


def test_settings_service_reports_a_logging_failure_without_losing_the_save(tmp_path):
    def failing_sink(event, details):
        raise RuntimeError("log unavailable")

    service, _ = make_settings_service(tmp_path, event_sink=failing_sink)
    candidate = service.fit_candidate("pump1", "operator", points())

    result = service.accept(candidate)

    assert result.succeeded is True
    assert "could not be logged" in result.summary
    assert service.active_calibration("pump1") is not None
