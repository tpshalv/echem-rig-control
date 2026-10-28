from rig_control.devices.pressure_controller import PressureController
from rig_control.display_units import DisplayUnits
from array import array
from bisect import bisect_left
from collections import deque
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from math import floor
from pathlib import Path
import json
from queue import Empty
from traceback import format_exc

from rig_control.data.annotations import (
    Annotation,
    AnnotationRecord,
    RunReview,
    append_run_review,
    deleted_annotation,
    edited_annotation,
    latest_run_review,
    new_annotation,
    read_annotations,
    resolve_annotations,
)
from rig_control.data.experiment import ExperimentMetadata
from rig_control.data.experiment_naming import (
    existing_recording,
    make_experiment_id,
    remove_recording,
)
from rig_control.devices.manager import DeviceManager
from rig_control.experiment_recording import ExperimentRecorder
from rig_control.devices.pump_calibration import PumpCalibration
from rig_control.instrument_settings.pump_calibration import PumpCalibrationService
from rig_control.models import Event, Measurement, Quality
from rig_control.polling import PollingService
from rig_control.power_supply_telemetry import state_label
from rig_control.control.service import RigControlService
from rig_control.devices.mass_flow_controller import MassFlowController
from rig_control.devices.power_supply import PowerSupply, PowerSupplyOperatingMode
from rig_control.devices.esp32_controller import Esp32Controller
from rig_control.devices.lumel_re72 import LumelRe72
from rig_control.devices.ohaus_guardian_5000.driver import OhausGuardian5000
from rig_control.devices.pump import Pump, PumpDirection
from rig_control.devices.temperature_probe import TemperatureProbe
from rig_control.models import DeviceStatus
from rig_control.rig_profile import DeviceCapability, RigProfile
from rig_control.ui.manual_control.model import ManualControlViewModel
from rig_control.ui.manual_control.types import PowerSupplyManualSafety
from rig_control.recipes.execution import RecipeRunner


@dataclass(frozen=True, slots=True)
class OperationActionResult:
    succeeded: bool
    summary: str
    technical_details: str | None = None


@dataclass(frozen=True, slots=True)
class LiveMeasurementRow:
    device_id: str
    channel: str
    value: float
    unit: str
    quality: str
    timestamp: datetime


class _Samples:
    """Compact time-ordered (timestamp, value, quality code) storage."""

    __slots__ = ("times", "values", "qualities")

    def __init__(self) -> None:
        self.times = array("d")
        self.values = array("d")
        self.qualities = bytearray()

    def __len__(self) -> int:
        return len(self.times)

    def append(self, timestamp: float, value: float, quality: int) -> None:
        self.times.append(timestamp)
        self.values.append(value)
        self.qualities.append(quality)

    def drop_oldest(self, count: int) -> None:
        del self.times[:count]
        del self.values[:count]
        del self.qualities[:count]

    def nearest_index(self, timestamp: float) -> int | None:
        if not self.times:
            return None
        index = bisect_left(self.times, timestamp)
        candidates = [i for i in (index - 1, index) if 0 <= i < len(self.times)]
        return min(candidates, key=lambda i: abs(self.times[i] - timestamp))


def _extrema_in_order(samples: _Samples, start: int, stop: int) -> tuple[int, ...]:
    """Indices of the lowest and highest value in ``[start, stop)``, oldest first."""

    chunk = samples.values[start:stop]
    lowest = start + chunk.index(min(chunk))
    highest = start + chunk.index(max(chunk))
    if lowest == highest:
        return (lowest,)
    return (lowest, highest) if lowest < highest else (highest, lowest)


def _binned_indices(
    samples: _Samples,
    start: int,
    stop: int,
    bin_seconds: float,
) -> list[int]:
    """Min and max of each fixed-width time bin over ``[start, stop)``.

    Bins sit on an absolute time grid, so a refresh only changes the bins
    new readings land in instead of reshuffling the whole trace.
    """

    times = samples.times
    selected: list[int] = []
    index = start
    current_bin = floor(times[start] / bin_seconds)
    while index < stop:
        current_bin = max(current_bin, floor(times[index] / bin_seconds))
        end = bisect_left(times, (current_bin + 1) * bin_seconds, index, stop)
        end = max(end, index + 1)  # Guards against float rounding at an edge.
        selected.extend(_extrema_in_order(samples, index, end))
        index = end
        current_bin += 1
    return selected


class _ChannelHistory:
    """Bounded history for one live signal.

    Every reading from the last ``_RAW_SECONDS`` is kept as-is, which covers
    every fixed graph period at full detail. A second, fixed-size copy spans
    the whole run at an even resolution that halves whenever it fills, so a
    run of any length costs the same memory. Graph queries take the slice
    for the selected period and reduce it to the min and max of fixed-width
    time bins, so the point count never depends on which store was used.
    """

    _RAW_SECONDS = 28_800  # The longest fixed graph period ("Last 8 hours").
    _RAW_LIMIT = 100_000  # Backstop for very fast polling.
    _RUN_LIMIT = 10_000
    _TRIM_CHUNK = 1_024

    def __init__(self, recent_limit: int) -> None:
        self._recent_limit = recent_limit
        self._raw = _Samples()
        self._run = _Samples()
        # Readings per group feeding the whole-run store; 1 keeps them all.
        self._group_size = 1
        self._group: list[tuple[float, float, int]] = []
        self._qualities: list[str] = []
        self._quality_codes: dict[str, int] = {}
        self._identity: tuple[str, str, str] | None = None
        self._timezone = None
        self._first_time: float | None = None

    def append(self, row: LiveMeasurementRow) -> None:
        try:
            value = float(row.value)
        except (TypeError, ValueError):
            return  # Not something a graph can draw.
        timestamp = row.timestamp.timestamp()
        if self._raw and timestamp < self._raw.times[-1]:
            return  # Keep both stores time-ordered for bisection.
        self._identity = (row.device_id, row.channel, row.unit)
        if self._first_time is None:
            self._first_time = timestamp
        self._timezone = row.timestamp.tzinfo
        quality = self._quality_codes.get(row.quality)
        if quality is None:
            quality = self._quality_codes[row.quality] = len(self._qualities)
            self._qualities.append(row.quality)

        self._raw.append(timestamp, value, quality)
        self._trim_raw()
        self._append_to_run(timestamp, value, quality)

    def _trim_raw(self) -> None:
        times = self._raw.times
        expired = bisect_left(times, times[-1] - self._RAW_SECONDS)
        excess = max(expired, len(times) - self._RAW_LIMIT)
        if excess >= self._TRIM_CHUNK:
            self._raw.drop_oldest(excess)

    def _append_to_run(self, timestamp: float, value: float, quality: int) -> None:
        if self._group_size == 1:
            self._run.append(timestamp, value, quality)
        else:
            self._group.append((timestamp, value, quality))
            if len(self._group) >= self._group_size:
                lowest = min(self._group, key=lambda sample: sample[1])
                highest = max(self._group, key=lambda sample: sample[1])
                for sample in sorted({lowest, highest}):
                    self._run.append(*sample)
                self._group.clear()
        if len(self._run) >= self._RUN_LIMIT:
            self._halve_run()

    def _halve_run(self) -> None:
        """Keep the min and max of every four samples: half the points, same span."""

        old = self._run
        self._run = _Samples()
        for start in range(0, len(old), 4):
            for index in _extrema_in_order(old, start, min(start + 4, len(old))):
                self._run.append(
                    old.times[index], old.values[index], old.qualities[index]
                )
        # New readings must arrive at the density the halved store now has:
        # one sample per reading becomes two per four, then two per eight...
        self._group_size = 4 if self._group_size == 1 else self._group_size * 2

    def recent_rows(self) -> tuple[LiveMeasurementRow, ...]:
        count = min(self._recent_limit, len(self._raw))
        return tuple(
            self._row(self._raw, index)
            for index in range(len(self._raw) - count, len(self._raw))
        )

    def rows_for_period(
        self,
        seconds: int | None,
        limit: int = 500,
    ) -> tuple[LiveMeasurementRow, ...]:
        if not self._raw:
            return ()
        latest = self._raw.times[-1]
        run_start = self._first_time
        start = run_start if seconds is None else max(latest - seconds, run_start)
        # Size bins from the data actually shown: a 15-minute run in the
        # 8-hour view must not get 8-hour-wide bins.
        span = latest - start

        raw_start = self._raw.times[0]
        sources: list[tuple[_Samples, int, int]] = []
        if start < raw_start:
            # Older than the raw store: only the whole-run store has it.
            sources.append(
                (
                    self._run,
                    bisect_left(self._run.times, start),
                    bisect_left(self._run.times, raw_start),
                )
            )
        sources.append(
            (self._raw, bisect_left(self._raw.times, start), len(self._raw))
        )
        sources = [source for source in sources if source[2] > source[1]]

        if sum(stop - begin for _samples, begin, stop in sources) <= limit:
            return tuple(
                self._row(samples, index)
                for samples, begin, stop in sources
                for index in range(begin, stop)
            )

        # Two points per bin, one extra bin for grid alignment, one split at
        # the junction between stores, plus both endpoints keeps us in limit.
        bin_seconds = max(span, 1e-6) / max(1, (limit - 6) // 2)
        rows: list[LiveMeasurementRow] = []
        last = len(sources) - 1
        for position, (samples, begin, stop) in enumerate(sources):
            indices = _binned_indices(samples, begin, stop, bin_seconds)
            if position == 0:
                indices.insert(0, begin)
            if position == last:
                indices.append(stop - 1)
            rows.extend(
                self._row(samples, index) for index in dict.fromkeys(indices)
            )
        return tuple(rows)

    def nearest(self, timestamp: float) -> LiveMeasurementRow | None:
        """The retained reading closest in time; every candidate is a real reading."""

        candidates = [
            (samples, index)
            for samples in (self._raw, self._run)
            if (index := samples.nearest_index(timestamp)) is not None
        ]
        if not candidates:
            return None
        samples, index = min(
            candidates,
            key=lambda candidate: abs(candidate[0].times[candidate[1]] - timestamp),
        )
        return self._row(samples, index)

    def set_recent_limit(self, limit: int) -> None:
        self._recent_limit = limit

    def retained_count(self) -> int:
        return len(self._raw) + len(self._run) + len(self._group)

    def _row(self, samples: _Samples, index: int) -> LiveMeasurementRow:
        device_id, channel, unit = self._identity
        return LiveMeasurementRow(
            device_id=device_id,
            channel=channel,
            value=samples.values[index],
            unit=unit,
            quality=self._qualities[samples.qualities[index]],
            timestamp=datetime.fromtimestamp(samples.times[index], self._timezone),
        )


@dataclass(frozen=True, slots=True)
class RunReviewContext:
    """What the end-of-run review shows: the plan, what happened, any review so far."""

    directory: Path
    experiment_id: str
    start_notes: str
    annotations: tuple[Annotation, ...]
    review: RunReview | None


# A note may be placed at most this far ahead of the clock, to absorb the
# moment between capturing a time and saving it.
_ANNOTATION_CLOCK_SLACK = timedelta(seconds=5)


@dataclass(frozen=True, slots=True)
class OperationChannelRow:
    """One measured or adjustable value shown on the Operation screen."""

    device_id: str
    device_name: str
    channel: str
    channel_name: str
    value: float | bool | str | None
    unit: str
    quality: str
    timestamp: datetime | None
    system: str
    writable: bool = False
    editor: str = "number"
    minimum: float | None = None
    maximum: float | None = None

    @property
    def key(self) -> tuple[str, str]:
        return (self.device_id, self.channel)


class OperationViewModel:
    """GUI-independent coordination for monitoring and recording."""

    def __init__(
        self,
        device_manager: DeviceManager,
        polling_service: PollingService,
        experiment_recorder: ExperimentRecorder,
        control_service: RigControlService,
        *,
        profile_id: str,
        profile: RigProfile | None = None,
        history_limit: int = 120,
        event_limit: int = 1_000,
        power_supply_safety: PowerSupplyManualSafety | None = None,
        pump_calibration_service: PumpCalibrationService | None = None,
        display_units: DisplayUnits | None = None,
        recipe_runner: RecipeRunner | None = None,
    ) -> None:
        self._validate_history_limit(history_limit)
        self._validate_event_limit(event_limit)
        self._device_manager = device_manager
        self._display_units = display_units or DisplayUnits()
        self._polling_service = polling_service
        self._experiment_recorder = experiment_recorder
        self._pump_calibration_service = pump_calibration_service or PumpCalibrationService()
        self.manual_control = ManualControlViewModel(
            device_manager,
            control_service,
            power_supply_safety=power_supply_safety,
        )
        self._profile_id = profile_id
        self._profile = profile
        self.recipe_runner = recipe_runner
        self._measurements: dict[
            tuple[str, str], LiveMeasurementRow
        ] = {}
        self._history_limit = history_limit
        self._measurement_histories: dict[
            tuple[str, str], _ChannelHistory
        ] = {}
        self._warnings: dict[str, str] = {}
        self._events: deque[Event] = deque(maxlen=event_limit)
        self._annotation_records: list[AnnotationRecord] = []
        self._annotations: tuple[Annotation, ...] = ()

    @property
    def profile(self) -> RigProfile | None:
        """Active profile, used by the recipe editor for friendly device labels."""
        return self._profile

    @property
    def display_units(self) -> DisplayUnits:
        """Units the screens show and accept, e.g. mA for recipe currents."""
        return self._display_units

    @property
    def is_monitoring(self) -> bool:
        return self._polling_service.is_running

    @property
    def is_recording(self) -> bool:
        return self._experiment_recorder.is_recording

    @property
    def events(self) -> tuple[Event, ...]:
        return tuple(self._events)

    @property
    def history_limit(self) -> int:
        return self._history_limit

    def measurement_rows(self) -> tuple[LiveMeasurementRow, ...]:
        return tuple(
            self._measurements[key]
            for key in sorted(self._measurements)
        )

    def systems(self) -> tuple[str, ...]:
        """Return only systems represented by configured devices."""

        systems = {
            self._system_for_device(device_id)
            for device_id in self._device_manager.device_ids
        }
        preferred = ("Electrical", "Thermal", "Gas flow", "Fluid handling", "Other")
        return tuple(
            sorted(
                systems,
                key=lambda value: (
                    preferred.index(value) if value in preferred else len(preferred),
                    value.casefold(),
                ),
            )
        )

    def channel_rows(self, system: str | None = None) -> tuple[OperationChannelRow, ...]:
        """Join polled readings and typed controls into unified UI rows."""

        rows: dict[tuple[str, str], OperationChannelRow] = {}
        for reading in self.measurement_rows():
            device = self._device_manager.get(reading.device_id)
            writable = (
                isinstance(device, MassFlowController)
                and reading.channel == "setpoint"
                and getattr(device, "control_ready", True)
            ) or (
                isinstance(device, LumelRe72)
                and reading.channel == "target_setpoint"
            )
            maximum = None
            channel_name = self._labelled_channel_name(reading.device_id, reading.channel)
            if isinstance(device, MassFlowController) and reading.channel == "setpoint":
                maximum = device.limits.maximum_flow
            value, unit = self._for_display(reading.channel, reading.value, reading.unit)
            if reading.channel == "regulation_mode":
                value, unit = state_label(reading.value, reading.unit), ""
            if maximum is not None:
                maximum = self._for_display(reading.channel, maximum, reading.unit)[0]
            row = OperationChannelRow(
                device_id=reading.device_id,
                device_name=self._device_name(reading.device_id),
                channel=reading.channel,
                channel_name=channel_name,
                value=value,
                unit=unit,
                quality=reading.quality,
                timestamp=reading.timestamp,
                system=self._system_for_device(reading.device_id),
                writable=writable,
                minimum=0.0 if writable else None,
                maximum=maximum,
            )
            rows[row.key] = row

        for device_id in self._device_manager.device_ids:
            device = self._device_manager.get(device_id)
            if device.status is DeviceStatus.DISCONNECTED:
                continue
            device_name = self._device_name(device_id)
            device_system = self._system_for_device(device_id)
            supply_mode = (
                self.manual_control.power_supply_mode(device_id)
                if isinstance(device, PowerSupply)
                else None
            )
            for channel, channel_name, unit in self._expected_measurements(
                device_id, device
            ):
                if (device_id, channel) not in rows:
                    rows[(device_id, channel)] = OperationChannelRow(
                        device_id, device_name, channel, channel_name,
                        None,
                        unit, "", None, device_system,
                    )
            if hasattr(device, "verification_message"):
                rows[(device_id, "control_verification")] = OperationChannelRow(
                    device_id, device_name, "control_verification", "Alicat control mode",
                    device.verification_message, "", "good" if device.control_ready else "bad", None, device_system)
            if isinstance(device, PressureController):
                policy = device.pressure_policy
                ready = device.status is DeviceStatus.READY and device.control_ready
                actual = device.pressure_setpoint_pa
                maximum = device.maximum_pressure_pa / 100_000
                # Alicat pressure control is absolute only, so the operator
                # enters and reads one absolute value in the instrument's own
                # frame of reference.
                setpoint_value, setpoint_unit = self._display_units.show_pressure(
                    0.0 if actual is None else actual / 100_000, "bara"
                )
                rows[(device_id, "pressure_setpoint_absolute")] = OperationChannelRow(
                    device_id, device_name, "pressure_setpoint_absolute",
                    f"BPR pressure setpoint (absolute, {setpoint_unit})",
                    None if actual is None else setpoint_value, setpoint_unit,
                    "good" if ready else "bad", None, device_system, ready,
                    "number", 0.0,
                    self._display_units.show_pressure(maximum, "bara")[0])
                rows[(device_id, "resume_pressure_control")] = OperationChannelRow(
                    device_id, device_name, "resume_pressure_control", "Resume BPR regulation",
                    "Resume (may discharge gas downstream)", "", "good" if ready else "bad", None, device_system,
                    ready and device.valve_hold is not False, "action")
                ceiling, ceiling_unit = self._display_units.show_pressure(maximum, "bara")
                rows[(device_id, "pressure_ceiling")] = OperationChannelRow(
                    device_id, device_name, "pressure_ceiling",
                    "HIGH PRESSURE OVERRIDE ACTIVE" if policy.overridden else "Maximum permitted pressure",
                    ceiling, ceiling_unit,
                    "warning" if policy.overridden else "good", None, device_system)
            if isinstance(device, PowerSupply):
                voltage_name = (
                    "Voltage setpoint"
                    if supply_mode is PowerSupplyOperatingMode.CONSTANT_VOLTAGE
                    else "Voltage limit"
                )
                rows[(device_id, "voltage_setpoint")] = OperationChannelRow(
                    device_id, device_name, "voltage_setpoint", voltage_name,
                    device.voltage_setpoint, "V", "good", None, device_system,
                    True, "number", 0.0, device.limits.maximum_voltage,
                    )
            if isinstance(device, MassFlowController):
                key = (device_id, "setpoint")
                if key not in rows:
                    native = device.limits.flow_unit
                    setpoint, flow_unit = self._display_units.show_flow(
                        device.flow_setpoint, native
                    )
                    rows[key] = OperationChannelRow(
                        device_id, device_name, "setpoint", "Flow setpoint",
                        setpoint, flow_unit, "good", None,
                        device_system, getattr(device, "control_ready", True), "number", 0.0,
                        self._display_units.show_flow(
                            device.limits.maximum_flow, native
                        )[0],
                    )
            if isinstance(device, PowerSupply):
                mode = supply_mode
                current_limit_name = (
                    "Current setpoint"
                    if mode is PowerSupplyOperatingMode.CONSTANT_CURRENT
                    else "Current limit"
                )
                rows[(device_id, "current_limit")] = OperationChannelRow(
                    device_id, device_name, "current_limit", current_limit_name,
                    device.current_limit, "A", "good", None, device_system,
                    True, "number", 0.0, device.limits.maximum_current,
                )
                rows[(device_id, "output_enabled")] = OperationChannelRow(
                    device_id, device_name, "output_enabled", "Output",
                    device.output_enabled, "", "good", None, device_system,
                    True, "boolean",
                )
                rows[(device_id, "operating_mode")] = OperationChannelRow(
                    device_id, device_name, "operating_mode", "Requested operating mode",
                    mode.value, "", "good", None, device_system, True, "mode",
                )
            if isinstance(device, Pump):
                rows[(device_id, "speed_setpoint")] = OperationChannelRow(
                    device_id, device_name, "speed_setpoint", "Speed setpoint",
                    device.speed_setpoint_rpm, "rpm", "good", None, device_system,
                    True, "number", 0.0, device.limits.maximum_speed_rpm,
                )
                calibration = self._active_pump_calibration(device_id)
                if calibration is not None:
                    # Derived from the speed setpoint through the active
                    # calibration; entering a flow converts it back to rpm.
                    fit = calibration.fit
                    rows[(device_id, "flow_setpoint")] = OperationChannelRow(
                        device_id, device_name, "flow_setpoint", "Flow setpoint (calibrated)",
                        None if device.speed_setpoint_rpm is None
                        else round(fit.flow_for_speed(device.speed_setpoint_rpm), 3),
                        "mL/min", "good", None, device_system, True, "number",
                        max(fit.intercept, 0.0),
                        fit.flow_for_speed(device.limits.maximum_speed_rpm),
                    )
                rows[(device_id, "direction")] = OperationChannelRow(
                    device_id, device_name, "direction", "Direction",
                    device.direction.value if device.direction is not None else None,
                    "", "good", None, device_system, True, "direction",
                )
                rows[(device_id, "running")] = OperationChannelRow(
                    device_id, device_name, "running", "Running",
                    device.running, "", "good", None, device_system, True, "running",
                )
            if isinstance(device, OhausGuardian5000):
                rows.update(self._hotplate_rows(device_id, device_name, device_system, device))
            if isinstance(device, Esp32Controller):
                status = device.controller_status
                rows[(device_id, "watchdog_tripped")] = OperationChannelRow(
                    device_id, device_name, "watchdog_tripped", "Watchdog tripped",
                    status.get("watchdog_tripped") is True, "", "good", None,
                    device_system,
                )
                rows[(device_id, "watchdog_rearm")] = OperationChannelRow(
                    device_id, device_name, "watchdog_rearm", "Watchdog",
                    "Rearm", "", "good", None, device_system, True, "action",
                )
                rows[(device_id, "safe_state_active")] = OperationChannelRow(
                    device_id, device_name, "safe_state_active", "Safe state active",
                    status.get("safe_state_active") is True, "", "good", None,
                    device_system,
                )

        selected = (
            row for row in rows.values()
            if system is None or row.system == system
        )
        return tuple(sorted(selected, key=lambda row: (
            row.system.casefold(), row.device_name.casefold(), row.channel_name.casefold()
        )))

    @staticmethod
    def _hotplate_rows(
        device_id: str, device_name: str, system: str, device: OhausGuardian5000,
    ) -> dict[tuple[str, str], OperationChannelRow]:
        spec = device.model_spec
        if spec is None:
            return {}
        # The editor range is the lowest of the model rating and any rig or
        # run ceiling; the driver re-checks all of them on every write.
        ready = device.status is DeviceStatus.READY
        quality = "good" if ready else "bad"
        rows: dict[tuple[str, str], OperationChannelRow] = {}

        def ceiling(rating: float, *limits: float | None) -> float:
            return min([rating, *(limit for limit in limits if limit is not None)])

        def add(channel: str, name: str, value, unit: str, editor: str = "number",
                minimum: float | None = None, maximum: float | None = None) -> None:
            rows[(device_id, channel)] = OperationChannelRow(
                device_id, device_name, channel, name, value, unit, quality, None,
                system, ready, editor, minimum, maximum,
            )

        if spec.heating:
            add("target_temperature", "Target temperature", device.target_temperature,
                "degC", minimum=0.0, maximum=ceiling(
                    spec.maximum_temperature, device.limits.maximum_temperature,
                    device.run_limits.maximum_temperature))
            add("heating_enabled", "Heating", device.heating_enabled, "", "boolean")
        if spec.stirring:
            add("target_speed", "Target stir speed", device.target_speed, "rpm",
                minimum=float(spec.minimum_speed), maximum=ceiling(
                    spec.maximum_speed, device.limits.maximum_speed,
                    device.run_limits.maximum_speed))
            add("stirring_enabled", "Stirring", device.stirring_enabled, "", "boolean")
        mode = device.operating_mode
        rows[(device_id, "operating_mode_readback")] = OperationChannelRow(
            device_id, device_name, "operating_mode_readback", "Hotplate mode",
            None if mode is None else mode.name.replace("_", " ").lower(), "",
            "bad" if mode is None or mode.name == "ERROR" else "good", None, system,
        )
        return rows

    #: Channels this preference covers. Volumetric flow and the totaliser
    #: are different quantities with their own units and are left alone.
    _PRESSURE_CHANNELS = frozenset({"absolute_pressure", "pressure_setpoint_absolute"})
    _FLOW_CHANNELS = frozenset({"mass_flow", "setpoint"})

    def _for_display(self, channel: str, value, unit: str):
        """Convert one reading into the unit this rig is set to show."""

        if value is None or not isinstance(value, (int, float)) or isinstance(value, bool):
            return value, unit
        if channel in self._PRESSURE_CHANNELS:
            return self._display_units.show_pressure(float(value), unit)
        if channel in self._FLOW_CHANNELS:
            return self._display_units.show_flow(float(value), unit)
        return value, unit

    def apply_channel_value(
        self, device_id: str, channel: str, value: float | bool | str
    ) -> OperationActionResult:
        """Apply one editable channel through existing typed commands."""

        if channel == "flow_setpoint":
            return self._set_pump_flow(device_id, float(value))
        if channel == "pressure_setpoint_absolute":
            # The unit shown on the row is the unit the value is sent in, so a
            # number can never be separated from what it means.
            result = self.manual_control.set_pressure_setpoint(
                device_id, float(value), self._display_units.entry_pressure_unit("bara")
            )
        elif channel == "resume_pressure_control":
            result = self.manual_control.resume_pressure_control(device_id)
        elif channel == "setpoint":
            device = self._device_manager.get(device_id)
            unit = ""
            if isinstance(device, MassFlowController):
                # A device that is not an MFC is refused by the command
                # itself, which is where that check belongs.
                native = device.limits.flow_unit
                entered = self._display_units.entry_flow_unit(native)
                unit = "" if entered == native else entered
            result = self.manual_control.set_mfc_flow(device_id, float(value), unit)
        elif channel == "voltage_setpoint":
            result = self.manual_control.set_power_supply_voltage(device_id, float(value))
        elif channel == "current_limit":
            result = self.manual_control.set_power_supply_current(device_id, float(value))
        elif channel == "output_enabled":
            result = self.manual_control.set_power_supply_output(device_id, bool(value))
        elif channel == "operating_mode":
            result = self.manual_control.set_power_supply_operating_mode(
                device_id, PowerSupplyOperatingMode(str(value))
            )
        elif channel == "watchdog_rearm":
            result = self.manual_control.rearm_controller(device_id)
        elif channel == "target_setpoint":
            result = self.manual_control.set_temperature_setpoint(
                device_id, float(value)
            )
        elif channel == "speed_setpoint":
            result = self.manual_control.set_pump_speed(device_id, float(value))
        elif channel == "direction":
            result = self.manual_control.set_pump_direction(
                device_id, PumpDirection(str(value))
            )
        elif channel == "running":
            result = self.manual_control.set_pump_running(device_id, bool(value))
        elif channel == "target_temperature":
            result = self.manual_control.set_hotplate_temperature(device_id, float(value))
        elif channel == "target_speed":
            result = self.manual_control.set_hotplate_speed(device_id, float(value))
        elif channel == "heating_enabled":
            result = self.manual_control.set_hotplate_heating(device_id, bool(value))
        elif channel == "stirring_enabled":
            result = self.manual_control.set_hotplate_stirring(device_id, bool(value))
        else:
            return OperationActionResult(False, f"Channel {channel!r} is read-only.")
        return OperationActionResult(
            result.succeeded, result.summary, result.technical_details
        )

    def _device_name(self, device_id: str) -> str:
        if self._profile is None:
            return device_id
        try:
            role = self._profile.get_role(device_id)
            return role.friendly_name + (" [BPR]" if role.capability is DeviceCapability.BACK_PRESSURE_CONTROLLER else "")
        except KeyError:
            return device_id

    def _expected_measurements(self, device_id: str, device: object) -> tuple[tuple[str, str, str], ...]:
        if isinstance(device, TemperatureProbe):
            return tuple((channel, self._labelled_channel_name(device_id, channel), "degC")
                         for channel in device.channels)
        if isinstance(device, PowerSupply):
            return (("voltage", "Voltage", "V"), ("current", "Current draw", "A"))
        if isinstance(device, MassFlowController):
            return (("mass_flow", "Measured mass flow", device.limits.flow_unit),)
        if isinstance(device, OhausGuardian5000):
            capabilities = device.capabilities
            return (
                (("temperature", "Plate temperature", "degC"),) if "heating" in capabilities else ()
            ) + (
                (("stir_speed", "Stir speed", "rpm"),) if "stirring" in capabilities else ()
            )
        if self._profile is None:
            return (("measurement", "Measurement", ""),)
        try:
            role = self._profile.get_role(device_id)
        except KeyError:
            return ()
        capability = role.capability
        if capability is DeviceCapability.MASS_FLOW_METER:
            unit = str(role.settings.get("flow_unit", ""))
            return (("mass_flow", "Measured mass flow", unit),)
        if capability is DeviceCapability.TEMPERATURE_SENSOR:
            return (("temperature", "Temperature", ""),)
        if capability is DeviceCapability.HUMIDITY_SENSOR:
            return (("humidity", "Humidity", ""),)
        if capability in {
            DeviceCapability.PRESSURE_SENSOR_ABSOLUTE,
            DeviceCapability.PRESSURE_SENSOR_RELATIVE,
            DeviceCapability.PRESSURE_SENSOR_DIFFERENTIAL,
        }:
            return (("pressure", "Pressure", ""),)
        return ()

    def _system_for_device(self, device_id: str) -> str:
        if self._profile is None:
            return "Other"
        try:
            role = self._profile.get_role(device_id)
        except KeyError:
            return "Other"
        if role.system is not None:
            return role.system.strip()
        capability = role.capability
        if capability in {
            DeviceCapability.DC_POWER_SUPPLY,
            DeviceCapability.POTENTIOSTAT,
        }:
            return "Electrical"
        if capability in {
            DeviceCapability.TEMPERATURE_SENSOR,
            DeviceCapability.HUMIDITY_SENSOR,
            DeviceCapability.TEMPERATURE_CONTROLLER,
            DeviceCapability.HOTPLATE_STIRRER,
        }:
            return "Thermal"
        if capability in {
            DeviceCapability.MASS_FLOW_CONTROLLER,
            DeviceCapability.MASS_FLOW_METER,
            DeviceCapability.BACK_PRESSURE_CONTROLLER,
            DeviceCapability.PRESSURE_SENSOR_ABSOLUTE,
            DeviceCapability.PRESSURE_SENSOR_RELATIVE,
            DeviceCapability.PRESSURE_SENSOR_DIFFERENTIAL,
        }:
            return "Gas flow"
        if capability is DeviceCapability.PERISTALTIC_PUMP:
            return "Fluid handling"
        return "Other"

    def _labelled_channel_name(self, device_id: str, channel: str) -> str:
        if self._profile is not None:
            try:
                label = self._profile.get_role(device_id).channel_labels.get(channel)
            except KeyError:
                label = None
            if label:
                return f"{label} ({channel})"
        return self._channel_name(channel)

    @staticmethod
    def _channel_name(channel: str) -> str:
        names = {
            "mass_flow": "Mass flow",
            "volumetric_flow": "Volumetric flow",
            "absolute_pressure": "Absolute pressure",
            "gas_temperature": "Gas temperature",
            "totalized_flow": "Totalized flow",
            "setpoint": "Flow setpoint",
            "current": "Current draw",
            "voltage": "Voltage",
            "regulation_mode": "Actual regulation mode",
        }
        return names.get(channel, channel.replace("_", " ").capitalize())

    def measurement_history(
        self,
        device_id: str,
        channel: str,
    ) -> tuple[LiveMeasurementRow, ...]:
        history = self._measurement_histories.get((device_id, channel))
        return () if history is None else history.recent_rows()

    def measurement_history_for_period(
        self,
        device_id: str,
        channel: str,
        seconds: int | None,
    ) -> tuple[LiveMeasurementRow, ...]:
        """Return at most 500 representative points over a selected period."""

        history = self._measurement_histories.get((device_id, channel))
        return () if history is None else history.rows_for_period(seconds)

    def latest_measurement(
        self,
        device_id: str,
        channel: str,
    ) -> Measurement | None:
        row = self._measurements.get((device_id, channel))
        if row is None:
            return None
        return Measurement(
            value=row.value,
            unit=row.unit,
            timestamp=row.timestamp,
            quality=Quality(row.quality),
        )

    def set_history_limit(self, history_limit: int) -> None:
        """Change retained points while preserving the newest readings."""

        self._validate_history_limit(history_limit)
        if history_limit == self._history_limit:
            return

        self._history_limit = history_limit
        for history in self._measurement_histories.values():
            history.set_recent_limit(history_limit)

    def warnings(self) -> tuple[tuple[str, str], ...]:
        return tuple(sorted(self._warnings.items()))

    def diagnostic_metrics(self) -> dict[str, object]:
        """Describe UI-retained state for the runtime health journal."""

        heartbeat_diagnostics = {
            device_id: device.heartbeat_diagnostics()
            for device_id in self._device_manager.device_ids
            if isinstance(
                (device := self._device_manager.get(device_id)),
                Esp32Controller,
            )
        }
        return {
            "latest_measurement_count": len(self._measurements),
            "history_channel_count": len(self._measurement_histories),
            "history_point_count": sum(
                history.retained_count()
                for history in self._measurement_histories.values()
            ),
            "history_limit_per_channel": self._history_limit,
            "warning_count": len(self._warnings),
            "retained_event_count": len(self._events),
            "event_limit": self._events.maxlen,
            "esp32_heartbeats": heartbeat_diagnostics,
        }

    def connect_all(self) -> tuple[OperationActionResult, ...]:
        results: list[OperationActionResult] = []
        for device_id in self._device_manager.device_ids:
            if self._device_manager.get(device_id).status is not DeviceStatus.DISCONNECTED:
                # Pressing Connect again must not re-open ports already in use.
                results.append(OperationActionResult(True, f"{device_id!r} is already connected."))
                continue
            try:
                self._device_manager.connect(device_id)
            except Exception as error:
                results.append(
                    OperationActionResult(
                        False,
                        f"Could not connect {device_id!r}: "
                        f"{type(error).__name__}: {error}",
                        format_exc(),
                    )
                )
                continue
            results.append(
                OperationActionResult(
                    True,
                    f"Connected {device_id!r}.",
                )
            )
        for default_result in self.manual_control.initialize_manual_power_supply_defaults():
            results.append(
                OperationActionResult(
                    default_result.succeeded,
                    default_result.summary,
                    default_result.technical_details,
                )
            )
        connected = any(
            self._device_manager.get(device_id).status is not DeviceStatus.DISCONNECTED
            for device_id in self._device_manager.device_ids
        )
        if connected and not self.is_monitoring:
            # Connected devices that nobody polls look exactly like broken
            # sensors, so monitoring follows connection rather than waiting
            # for a separate button press.
            results.append(self.start_monitoring())
        return tuple(results)

    def start_monitoring(self) -> OperationActionResult:
        try:
            self._polling_service.start()
        except Exception as error:
            return self._failure("start monitoring", error)
        return OperationActionResult(True, "Background monitoring started.")

    def stop_monitoring(self) -> OperationActionResult:
        if self.is_recording:
            return OperationActionResult(
                False,
                "Stop experiment recording before stopping monitoring.",
            )
        try:
            self._polling_service.stop()
        except Exception as error:
            return self._failure("stop monitoring", error)
        return OperationActionResult(True, "Background monitoring stopped.")

    def _active_pump_calibration(self, device_id: str) -> PumpCalibration | None:
        try:
            return self._pump_calibration_service.active_calibration(device_id)
        except Exception:
            # An unreadable history only hides the flow row; rpm still works.
            return None

    def _set_pump_flow(self, device_id: str, flow_ml_per_min: float) -> OperationActionResult:
        """Convert a flow to rpm with the active calibration and send that speed."""

        calibration = self._active_pump_calibration(device_id)
        if calibration is None:
            return OperationActionResult(
                False,
                f"Pump {device_id!r} has no readable calibration; set its speed in rpm instead.",
            )
        fit = calibration.fit
        rpm = round(fit.speed_for_flow(flow_ml_per_min), 2)
        if rpm < 0:
            return OperationActionResult(
                False,
                f"{flow_ml_per_min:g} mL/min is below what calibration "
                f"{calibration.calibration_id} reaches: its line gives "
                f"{fit.intercept:g} mL/min at 0 rpm.",
            )
        result = self.manual_control.set_pump_speed(device_id, rpm)
        if not result.succeeded:
            return OperationActionResult(False, result.summary, result.technical_details)
        speeds = [point.speed_rpm for point in calibration.points]
        note = (
            "" if min(speeds) <= rpm <= max(speeds)
            else f" That is outside the calibrated {min(speeds):g}-{max(speeds):g} rpm, "
            "so the flow is extrapolated."
        )
        return OperationActionResult(
            True,
            f"Set pump {device_id!r} to {flow_ml_per_min:g} mL/min ({rpm:g} rpm, "
            f"calibration {calibration.calibration_id}).{note}",
        )

    def _pump_calibration_metadata(self) -> dict[str, str | float]:
        """Record which calibration was active for each pump at run start.

        Stores the calibration ID rather than a bare file path: each pump's
        calibration history file is append-only, so a path alone would not
        say which entry inside it was active for this particular run. The
        fitted slope/intercept are also copied in directly, so a run's
        recorded flow stays self-describing even if the history file is
        later edited or lost. A pump with no accepted calibration, or a
        calibration history that cannot be read, is simply omitted - this
        is traceability metadata, not something that should block starting
        a recording.
        """

        extra: dict[str, str | float] = {}
        for device_id in self._device_manager.device_ids:
            if not isinstance(self._device_manager.get(device_id), Pump):
                continue
            try:
                calibration = self._pump_calibration_service.active_calibration(device_id)
            except Exception:
                continue
            if calibration is None:
                continue
            extra[f"{device_id}_calibration_id"] = calibration.calibration_id
            extra[f"{device_id}_calibration_slope"] = calibration.fit.slope
            extra[f"{device_id}_calibration_intercept"] = calibration.fit.intercept
        return extra

    def start_recording(
        self,
        *,
        operator: str,
        output_directory: str | Path,
        description: str = "",
        electrolyser_id: str | None = None,
        notes: str | None = None,
        started_at: datetime | None = None,
        replace_existing: bool = False,
        experiment_id: str | None = None,
    ) -> OperationActionResult:
        """Start recording under ``INITIALS_date_time_description``.

        ``started_at`` lets the screen fix the time it previewed and checked
        for a clash. ``replace_existing`` deletes a previous recording of the
        same name, which the screen only passes after asking. An explicit
        ``experiment_id`` skips naming altogether.
        """
        if not self.is_monitoring:
            return OperationActionResult(
                False,
                "Start monitoring before starting experiment recording.",
            )
        if not str(output_directory).strip():
            return OperationActionResult(
                False,
                "Choose an experiment output folder before recording.",
            )
        try:
            started_at = started_at or datetime.now(UTC)
            if experiment_id is None:
                experiment_id = make_experiment_id(operator, started_at, description)
            existing = existing_recording(output_directory, experiment_id)
            if existing is not None:
                if not replace_existing:
                    return OperationActionResult(
                        False,
                        f"A recording named {experiment_id!r} already exists "
                        f"in {output_directory}.",
                    )
                remove_recording(existing)
            metadata = ExperimentMetadata(
                experiment_id=experiment_id,
                operator=operator,
                started_at=started_at,
                notes=notes or None,
                electrolyser_id=(electrolyser_id or "").strip() or None,
                extra={
                    "rig_profile_id": self._profile_id,
                    **({"channel_labels_json": json.dumps({
                        role.device_id: dict(role.channel_labels)
                        for role in self._profile.enabled_roles if role.channel_labels
                    }, ensure_ascii=False)} if self._profile is not None else {}),
                    **self._pump_calibration_metadata(),
                },
            )
            self._experiment_recorder.start(
                metadata=metadata,
                root_directory=output_directory,
            )
        except Exception as error:
            return self._failure("start experiment recording", error)
        self._annotation_records.clear()
        self._annotations = ()
        return OperationActionResult(
            True,
            f"Recording experiment {experiment_id!r}.",
        )

    def stop_recording(self) -> OperationActionResult:
        try:
            self._experiment_recorder.stop()
        except Exception as error:
            return self._failure("stop experiment recording", error)
        return OperationActionResult(True, "Experiment recording completed.")

    @property
    def experiment_directory(self) -> Path | None:
        return self._experiment_recorder.experiment_directory

    def export_excel(self, directory: str | Path | None = None) -> OperationActionResult:
        try:
            path = self._experiment_recorder.export_excel(directory)
        except Exception as error:
            return self._failure("export Excel", error)
        return OperationActionResult(True, f"Excel exported to {path}")

    @property
    def run_started_at(self) -> datetime | None:
        """Start of the current run, or of the last one once stopped."""
        metadata = self._experiment_recorder.metadata
        return None if metadata is None else metadata.started_at

    @property
    def annotations(self) -> tuple[Annotation, ...]:
        """Notes for the current run (or the last one, once stopped), by time."""
        return self._annotations

    def annotations_between(
        self,
        start: float,
        end: float,
    ) -> tuple[Annotation, ...]:
        return tuple(
            note for note in self._annotations
            if start <= note.timestamp.timestamp() <= end
        )

    def nearest_reading(
        self,
        device_id: str,
        channel: str,
        timestamp: datetime,
    ) -> LiveMeasurementRow | None:
        history = self._measurement_histories.get((device_id, channel))
        return None if history is None else history.nearest(timestamp.timestamp())

    def add_annotation(
        self,
        kind: str,
        text: str,
        timestamp: datetime,
        *,
        origin: str = "typed",
        uncertainty_seconds: float | None = None,
        signal: tuple[str, str] | None = None,
    ) -> OperationActionResult:
        problem = self._annotation_time_problem(timestamp)
        if problem is not None:
            return OperationActionResult(False, problem)
        try:
            record = new_annotation(
                kind=kind, text=text, timestamp=timestamp, origin=origin,
                uncertainty_seconds=uncertainty_seconds, signal=signal,
            )
        except (TypeError, ValueError) as error:
            return OperationActionResult(False, str(error))
        return self._write_annotation(record, "Note added.")

    def edit_annotation(
        self,
        annotation_id: str,
        *,
        kind: str | None = None,
        text: str | None = None,
        timestamp: datetime | None = None,
        origin: str | None = None,
        uncertainty_seconds: float | None = None,
        signal: tuple[str, str] | None = None,
    ) -> OperationActionResult:
        current = self._annotation(annotation_id)
        if current is None:
            return OperationActionResult(False, "That note no longer exists.")
        if timestamp is not None:
            problem = self._annotation_time_problem(timestamp)
            if problem is not None:
                return OperationActionResult(False, problem)
        try:
            record = edited_annotation(
                current, kind=kind, text=text, timestamp=timestamp, origin=origin,
                uncertainty_seconds=uncertainty_seconds, signal=signal,
            )
        except (TypeError, ValueError) as error:
            return OperationActionResult(False, str(error))
        return self._write_annotation(
            record, "Note moved." if timestamp is not None else "Note updated."
        )

    def delete_annotation(self, annotation_id: str) -> OperationActionResult:
        if self._annotation(annotation_id) is None:
            return OperationActionResult(False, "That note no longer exists.")
        return self._write_annotation(deleted_annotation(annotation_id), "Note deleted.")

    def run_review_context(
        self,
        directory: str | Path | None = None,
    ) -> RunReviewContext:
        """Read a run's start notes, notes and review back from its folder."""
        folder = Path(directory) if directory is not None else self.experiment_directory
        if folder is None:
            raise RuntimeError("No experiment folder is available to review")
        metadata_path = folder / "metadata.json"
        if not metadata_path.is_file():
            raise ValueError("Choose an experiment folder containing metadata.json")
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        return RunReviewContext(
            directory=folder,
            experiment_id=str(metadata.get("experiment_id", folder.name)),
            start_notes=metadata.get("notes") or "",
            annotations=read_annotations(folder),
            review=latest_run_review(folder),
        )

    def save_run_review(
        self,
        directory: str | Path,
        overall_qc: str,
        end_notes: str,
    ) -> OperationActionResult:
        try:
            append_run_review(directory, RunReview(
                overall_qc=overall_qc,
                end_notes=end_notes.strip(),
                reviewed_at=datetime.now(UTC),
            ))
        except Exception as error:
            return self._failure("save the run review", error)
        return OperationActionResult(True, "Run review saved.")

    def _annotation(self, annotation_id: str) -> Annotation | None:
        return next(
            (note for note in self._annotations if note.annotation_id == annotation_id),
            None,
        )

    def _annotation_time_problem(self, timestamp: datetime) -> str | None:
        started_at = self._experiment_recorder.recording_started_at
        if started_at is None:
            return "Notes can only be added while recording."
        if timestamp.tzinfo is None:
            return "A note's time must include a timezone."
        if timestamp < started_at:
            return "That time is before this recording started."
        if timestamp > datetime.now(UTC) + _ANNOTATION_CLOCK_SLACK:
            return "A note cannot be placed in the future."
        return None

    def _write_annotation(
        self,
        record: AnnotationRecord,
        summary: str,
    ) -> OperationActionResult:
        if not self.is_recording:
            return OperationActionResult(
                False, "Notes can only be added or changed while recording."
            )
        try:
            self._experiment_recorder.record_annotation(record)
        except Exception as error:
            return self._failure("save the note", error)
        self._annotation_records.append(record)
        self._annotations = resolve_annotations(self._annotation_records)
        return OperationActionResult(True, summary)

    def collect_polling_results(self) -> int:
        """Drain queued batches; intended for a Tk ``after`` callback."""

        count = 0
        while True:
            try:
                batch = self._polling_service.results.get_nowait()
            except Empty:
                return count

            count += 1
            successful_ids: set[str] = set()
            for record in batch.measurements:
                measurement = record.measurement
                key = (record.device_id, record.channel)
                row = LiveMeasurementRow(
                    device_id=record.device_id,
                    channel=record.channel,
                    value=measurement.value,
                    unit=measurement.unit,
                    quality=measurement.quality.value,
                    timestamp=measurement.timestamp,
                )
                self._measurements[key] = row
                history = self._measurement_histories.setdefault(
                    key,
                    _ChannelHistory(self._history_limit),
                )
                history.append(row)
                successful_ids.add(record.device_id)

            # An output-off snapshot has no new V/I reading. Retain the last
            # reading visibly as stale, without inventing zero measurements.
            for record in batch.measurements:
                if record.channel == "output_enabled" and not record.measurement.value:
                    for channel in ("voltage", "current"):
                        key = (record.device_id, channel)
                        if key in self._measurements:
                            self._measurements[key] = replace(
                                self._measurements[key], quality=Quality.STALE.value)

            for device_id in successful_ids:
                self._warnings.pop(device_id, None)
            for failure in batch.failures:
                self._warnings[failure.device_id] = (
                    f"{failure.error_type}: {failure.message}"
                )
                for key, row in tuple(self._measurements.items()):
                    if row.device_id == failure.device_id:
                        self._measurements[key] = replace(
                            row,
                            quality=Quality.STALE.value,
                        )
            self._events.extend(batch.events)

    def shutdown(self, *, wait_for_exports: bool = True) -> tuple[str, ...]:
        """Stop recording and polling for this screen.

        ``wait_for_exports=False`` is the operator's "close anyway": a
        stopped run's final exports are then left to finish in the
        background (or are cut short if the application exits).

        Does not disconnect devices - Operation and Diagnostics share one
        device manager, so device disconnection is only ever done centrally
        by ApplicationSession.close() (see HomeViewModel.close()).
        """

        failures: list[str] = []
        if self.recipe_runner is not None and self.recipe_runner.is_running:
            self.recipe_runner.stop()
            if self.recipe_runner.join(10.0) is None:
                failures.append("Recipe did not stop within 10 seconds.")
        if self.is_recording:
            try:
                self._experiment_recorder.stop()
            except Exception as error:
                failures.append(
                    f"Could not stop recording: {type(error).__name__}: {error}"
                )
        # A run stopped just before closing may still be writing its exports.
        if wait_for_exports:
            self._experiment_recorder.wait_for_finalisation()
        if self.is_monitoring:
            try:
                self._polling_service.stop()
            except Exception as error:
                failures.append(
                    f"Could not stop monitoring: {type(error).__name__}: {error}"
                )
        return tuple(failures)

    @staticmethod
    def _failure(operation: str, error: Exception) -> OperationActionResult:
        return OperationActionResult(
            False,
            f"Could not {operation}: {type(error).__name__}: {error}",
            format_exc(),
        )

    @staticmethod
    def _validate_history_limit(value: int) -> None:
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError("Trend history limit must be an integer")
        if value < 2:
            raise ValueError("Trend history limit must be at least 2")

    @staticmethod
    def _validate_event_limit(value: int) -> None:
        if not isinstance(value, int) or isinstance(value, bool):
            raise TypeError("Event history limit must be an integer")
        if value <= 0:
            raise ValueError("Event history limit must be positive")
