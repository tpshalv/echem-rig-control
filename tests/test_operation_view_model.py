from collections import deque
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from rig_control.data.in_memory_writer import InMemoryExperimentWriter
from rig_control.data.records import MeasurementRecord
from rig_control.devices.manager import DeviceManager
from rig_control.devices.simulated_sensor import SimulatedSensor
from rig_control.devices.simulated_mfc import SimulatedMassFlowController
from rig_control.devices.simulated_power_supply import SimulatedPowerSupply
from rig_control.devices.mass_flow_controller import MassFlowControllerLimits
from rig_control.devices.power_supply import (
    PowerSupplyLimits,
    PowerSupplyOperatingMode,
)
from rig_control.experiment_recording import ExperimentRecorder
from rig_control.models import Event, Measurement, Quality
from rig_control.polling import PollingBatch, PollingFailure, PollingService
from rig_control.control.service import RigControlService
from rig_control.ui.operation.model import LiveMeasurementRow, OperationViewModel
from rig_control.rig_profile import DeviceBackend, DeviceCapability, DeviceRole, RigProfile


FIXED_TIME = datetime(2026, 8, 18, 15, 0, tzinfo=UTC)


def make_model() -> tuple[
    OperationViewModel,
    DeviceManager,
    PollingService,
    InMemoryExperimentWriter,
]:
    manager = DeviceManager()
    manager.register(SimulatedSensor("temperature", 25.0, "degC"))
    writer = InMemoryExperimentWriter()
    recorder = ExperimentRecorder(writer_factory=lambda _root: writer)
    polling = PollingService(
        manager,
        interval_seconds=60.0,
        batch_handler=recorder.record_batch,
    )
    return (
        OperationViewModel(
            manager,
            polling,
            recorder,
            RigControlService(manager),
            profile_id="simulation",
        ),
        manager,
        polling,
        writer,
    )


def measurement_batch(
    value: float = 25.0,
    timestamp: datetime = FIXED_TIME,
) -> PollingBatch:
    return PollingBatch(
        started_at=FIXED_TIME,
        finished_at=FIXED_TIME,
        measurements=(
            MeasurementRecord(
                device_id="temperature",
                channel="temperature",
                measurement=Measurement(
                    value,
                    "degC",
                    timestamp=timestamp,
                    quality=Quality.GOOD,
                ),
            ),
        ),
        failures=(),
        events=(),
    )


def test_connect_all_connects_configured_devices() -> None:
    model, manager, _, _ = make_model()

    results = model.connect_all()

    assert all(result.succeeded for result in results)
    assert manager.summaries()[0].status.value == "ready"


def test_monitoring_has_explicit_start_and_stop() -> None:
    model, _, _, _ = make_model()

    started = model.start_monitoring()
    stopped = model.stop_monitoring()

    assert started.succeeded is True
    assert stopped.succeeded is True
    assert model.is_monitoring is False


def test_recording_requires_monitoring_and_output_folder() -> None:
    model, _, _, _ = make_model()

    inactive = model.start_recording(
        experiment_id="EXP-1",
        operator="operator",
        output_directory="data",
    )
    model.start_monitoring()
    missing_folder = model.start_recording(
        experiment_id="EXP-1",
        operator="operator",
        output_directory="",
    )
    model.stop_monitoring()

    assert inactive.succeeded is False
    assert "monitoring" in inactive.summary
    assert missing_folder.succeeded is False
    assert "output folder" in missing_folder.summary


def test_start_and_stop_recording_use_existing_recorder() -> None:
    model, _, _, writer = make_model()
    model.start_monitoring()

    started = model.start_recording(
        experiment_id="EXP-1",
        operator="operator",
        output_directory="unused",
        notes="test notes",
    )

    assert started.succeeded is True
    assert model.is_recording is True
    assert writer.metadata is not None
    assert writer.metadata.extra["rig_profile_id"] == "simulation"
    assert writer.metadata.notes == "test notes"

    stopped = model.stop_recording()
    model.stop_monitoring()
    assert stopped.succeeded is True
    assert model.is_recording is False


def test_monitoring_cannot_stop_while_recording() -> None:
    model, _, _, _ = make_model()
    model.start_monitoring()
    model.start_recording(
        experiment_id="EXP-1",
        operator="operator",
        output_directory="unused",
    )

    result = model.stop_monitoring()

    assert result.succeeded is False
    assert "Stop experiment recording" in result.summary
    model.shutdown()


def test_polling_queue_updates_live_measurements() -> None:
    model, _, polling, _ = make_model()
    polling.results.put(measurement_batch(27.5))

    count = model.collect_polling_results()

    assert count == 1
    assert model.measurement_rows()[0].value == 27.5
    assert model.measurement_rows()[0].quality == "good"


def test_measurement_history_has_configurable_bounded_default() -> None:
    model, _, polling, _ = make_model()

    assert model.history_limit == 120
    for value in range(125):
        polling._enqueue_result(measurement_batch(float(value)))
    model.collect_polling_results()

    history = model.measurement_history("temperature", "temperature")
    assert len(history) == 120
    assert history[0].value == 5.0
    assert history[-1].value == 124.0


def test_reducing_history_limit_keeps_newest_readings() -> None:
    model, _, polling, _ = make_model()
    for value in range(5):
        polling.results.put(measurement_batch(float(value)))
    model.collect_polling_results()

    model.set_history_limit(3)

    assert model.history_limit == 3
    assert [
        row.value
        for row in model.measurement_history(
            "temperature",
            "temperature",
        )
    ] == [2.0, 3.0, 4.0]


def test_changed_history_limit_applies_to_future_readings() -> None:
    model, _, polling, _ = make_model()
    model.set_history_limit(2)
    for value in range(3):
        polling.results.put(measurement_batch(float(value)))
    model.collect_polling_results()

    assert [
        row.value
        for row in model.measurement_history(
            "temperature",
            "temperature",
        )
    ] == [1.0, 2.0]


def test_period_history_spans_time_range_and_preserves_spike() -> None:
    model, _, polling, _ = make_model()
    for second in range(700):
        value = 500.0 if second == 250 else float(second % 10)
        polling.results.put(
            measurement_batch(value, FIXED_TIME + timedelta(seconds=second))
        )
        model.collect_polling_results()

    recent = model.measurement_history("temperature", "temperature")
    period = model.measurement_history_for_period(
        "temperature", "temperature", 600
    )

    assert len(recent) == 120
    assert len(period) <= 500
    assert period[0].timestamp <= FIXED_TIME + timedelta(seconds=109)
    assert period[-1].timestamp == FIXED_TIME + timedelta(seconds=699)
    assert any(row.value == 500.0 for row in period)


def test_whole_run_history_is_bounded_and_keeps_endpoints() -> None:
    model, _, polling, _ = make_model()
    for second in range(1_100):
        polling.results.put(
            measurement_batch(
                float(second), FIXED_TIME + timedelta(seconds=second)
            )
        )
        model.collect_polling_results()

    period = model.measurement_history_for_period(
        "temperature", "temperature", None
    )

    assert len(period) <= 500
    assert period[0].value == 0.0
    assert period[-1].value == 1_099.0


def test_long_selected_period_keeps_raw_resolution_for_short_run() -> None:
    model, _, polling, _ = make_model()
    model.set_history_limit(500)
    for second in range(200):
        polling.results.put(
            measurement_batch(
                float(second), FIXED_TIME + timedelta(seconds=second)
            )
        )
        model.collect_polling_results()

    eight_hours = model.measurement_history_for_period(
        "temperature", "temperature", 28_800
    )

    assert len(eight_hours) == 200
    assert [row.value for row in eight_hours] == [float(i) for i in range(200)]


@pytest.mark.parametrize("invalid", [0, 1, -1])
def test_history_limit_validation_is_clear(invalid: int) -> None:
    model, _, _, _ = make_model()

    with pytest.raises(ValueError, match="at least 2"):
        model.set_history_limit(invalid)

def test_history_limit_rejects_boolean() -> None:
    model, _, _, _ = make_model()

    with pytest.raises(TypeError, match="integer"):
        model.set_history_limit(True)  # type: ignore[arg-type]


def test_device_warning_persists_until_a_successful_read() -> None:
    model, _, polling, _ = make_model()
    polling.results.put(measurement_batch())
    model.collect_polling_results()
    polling.results.put(
        PollingBatch(
            started_at=FIXED_TIME,
            finished_at=FIXED_TIME,
            measurements=(),
            failures=(
                PollingFailure(
                    device_id="temperature",
                    operation="read measurements",
                    error_type="OSError",
                    message="connection lost",
                ),
            ),
            events=(Event("temperature", "Polling failed"),),
        )
    )
    model.collect_polling_results()

    assert model.warnings() == (
        ("temperature", "OSError: connection lost"),
    )
    assert model.measurement_rows()[0].quality == "stale"
    assert len(model.measurement_history("temperature", "temperature")) == 1
    assert len(model.events) == 1

    polling.results.put(measurement_batch())
    model.collect_polling_results()
    assert model.warnings() == ()


def test_retained_events_are_bounded() -> None:
    model, _, polling, _ = make_model()
    model._events = deque(maxlen=3)
    for index in range(5):
        polling.results.put(
            PollingBatch(
                started_at=FIXED_TIME,
                finished_at=FIXED_TIME,
                measurements=(),
                failures=(),
                events=(Event("test", f"event {index}"),),
            )
        )
    model.collect_polling_results()

    assert [event.message for event in model.events] == [
        "event 2",
        "event 3",
        "event 4",
    ]


def test_shutdown_stops_feature_services_but_leaves_session_devices_connected() -> None:
    model, manager, _, _ = make_model()
    model.connect_all()
    model.start_monitoring()

    failures = model.shutdown()

    assert failures == ()
    assert model.is_monitoring is False
    assert manager.summaries()[0].status.value == "ready"


def test_unified_channels_separate_measurements_from_limits_and_setpoints() -> None:
    manager = DeviceManager()
    manager.register(SimulatedPowerSupply("supply", PowerSupplyLimits(30, 5, 100)))
    manager.register(SimulatedMassFlowController(
        "mfc", MassFlowControllerLimits(2000, "sccm")
    ))
    writer = InMemoryExperimentWriter()
    recorder = ExperimentRecorder(writer_factory=lambda _root: writer)
    polling = PollingService(manager, interval_seconds=60, batch_handler=recorder.record_batch)
    profile = RigProfile("rig", "Rig", (
        DeviceRole("supply", "Main supply", DeviceCapability.DC_POWER_SUPPLY,
                   "simulated", DeviceBackend.SIMULATED),
        DeviceRole("mfc", "Hydrogen MFC", DeviceCapability.MASS_FLOW_CONTROLLER,
                   "simulated", DeviceBackend.SIMULATED),
    ))
    model = OperationViewModel(
        manager, polling, recorder, RigControlService(manager),
        profile_id="rig", profile=profile,
    )
    model.connect_all()
    model._measurements[("supply", "voltage")] = LiveMeasurementRow(
        "supply", "voltage", 4.9, "V", "good", FIXED_TIME
    )
    model._measurements[("supply", "current")] = LiveMeasurementRow(
        "supply", "current", 0.4, "A", "good", FIXED_TIME
    )
    model._measurements[("mfc", "mass_flow")] = LiveMeasurementRow(
        "mfc", "mass_flow", 95, "sccm", "good", FIXED_TIME
    )

    rows = {(row.device_id, row.channel): row for row in model.channel_rows()}

    assert model.systems() == ("Electrical", "Gas flow")
    assert rows[("supply", "voltage")].writable is True
    assert rows[("supply", "current")].writable is False
    assert rows[("supply", "current_limit")].writable is True
    assert rows[("mfc", "mass_flow")].writable is False
    assert rows[("mfc", "setpoint")].writable is True


def test_channel_labels_swap_with_power_supply_operating_mode() -> None:
    manager = DeviceManager()
    manager.register(SimulatedPowerSupply("supply", PowerSupplyLimits(30, 108, 1080)))
    writer = InMemoryExperimentWriter()
    recorder = ExperimentRecorder(writer_factory=lambda _root: writer)
    polling = PollingService(manager, interval_seconds=60, batch_handler=recorder.record_batch)
    profile = RigProfile("rig", "Rig", (
        DeviceRole("supply", "Main supply", DeviceCapability.DC_POWER_SUPPLY,
                   "simulated", DeviceBackend.SIMULATED),
    ))
    model = OperationViewModel(
        manager, polling, recorder, RigControlService(manager),
        profile_id="rig", profile=profile,
    )
    model.connect_all()

    default_rows = {
        (row.device_id, row.channel): row for row in model.channel_rows()
    }
    assert default_rows[("supply", "voltage")].channel_name == "Voltage limit"
    assert default_rows[("supply", "current_limit")].channel_name == "Current setpoint"
    # connect_all() applies manual power-supply defaults itself - this was
    # previously never called anywhere, so these values never fed through.
    # Voltage is the protective limit in constant current mode, so it gets
    # the low starting default; current is the setpoint here and is left
    # untouched.
    assert default_rows[("supply", "voltage")].value == 10.0
    assert default_rows[("supply", "current_limit")].value == 0.0

    result = model.manual_control.set_power_supply_operating_mode(
        "supply", PowerSupplyOperatingMode.CONSTANT_VOLTAGE,
    )
    assert result.succeeded is True

    switched_rows = {
        (row.device_id, row.channel): row for row in model.channel_rows()
    }
    assert switched_rows[("supply", "voltage")].channel_name == "Voltage setpoint"
    assert switched_rows[("supply", "current_limit")].channel_name == "Current limit"


def test_connected_device_channels_are_visible_before_monitoring_starts() -> None:
    model, _, _, _ = make_model()

    assert model.channel_rows() == ()
    model.connect_all()
    rows = model.channel_rows()

    assert len(rows) == 1
    assert rows[0].device_id == "temperature"
    assert rows[0].channel == "measurement"
    assert rows[0].value is None
    assert rows[0].timestamp is None

    model._measurements[("temperature", "measurement")] = LiveMeasurementRow(
        "temperature", "measurement", 24.5, "degC", "good", FIXED_TIME
    )
    live_rows = model.channel_rows()

    assert len(live_rows) == 1
    assert live_rows[0].value == 24.5


def test_explicit_profile_system_overrides_inferred_system() -> None:
    model, manager, polling, _ = make_model()
    profile = RigProfile("simulation", "Simulation", (
        DeviceRole(
            "temperature", "Chamber sensor", DeviceCapability.TEMPERATURE_SENSOR,
            "simulated", DeviceBackend.SIMULATED, system="Environment",
        ),
    ))
    overridden = OperationViewModel(
        manager, polling, model._experiment_recorder,
        RigControlService(manager), profile_id="simulation", profile=profile,
    )

    assert overridden.systems() == ("Environment",)


def test_watchdog_rearm_uses_existing_manual_control_path() -> None:
    model, _, _, _ = make_model()
    calls = []
    model.manual_control = SimpleNamespace(
        rearm_controller=lambda device_id: (
            calls.append(device_id)
            or SimpleNamespace(succeeded=True, summary="Watchdog rearmed", technical_details=None)
        )
    )

    result = model.apply_channel_value("esp32", "watchdog_rearm", True)

    assert result.succeeded is True
    assert calls == ["esp32"]
