from traceback import format_exc
from collections.abc import Callable

from rig_control.control.commands import (
    CommandSource,
    ControlCommand,
    EnterDeviceSafeState,
    SetMfcFlow,
    SetPowerSupplyCurrentLimit,
    SetPowerSupplyOutput,
    SetPowerSupplyVoltage,
    SetControllerOutput,
    RearmController,
)
from rig_control.control.service import RigControlService
from rig_control.devices.manager import DeviceManager
from rig_control.devices.mass_flow_controller import (
    MassFlowController,
)
from rig_control.devices.power_supply import (
    PowerSupply,
    PowerSupplyOperatingMode,
)
from rig_control.devices.esp32_controller import Esp32Controller
from rig_control.models import (
    DeviceStatus,
    Event,
    EventSeverity,
    Measurement,
)

from rig_control.ui.manual_control.types import (
    DEFAULT_WIRING_CURRENT_CEILING_AMPS,
    ManualActionResult,
    MeasurementReadFailure,
    MfcControlRow,
    PowerSupplyControlRow,
    PowerSupplyManualSafety,
    default_power_supply_manual_safety,
    ControllerControlRow,
)


class ManualControlViewModel:
    """GUI-independent manual control logic."""

    def __init__(
        self,
        device_manager: DeviceManager,
        control_service: RigControlService,
        measurement_provider: Callable[[str, str], Measurement | None] | None = None,
        power_supply_safety: PowerSupplyManualSafety | None = None,
    ) -> None:
        self._device_manager = device_manager
        self._control_service = control_service
        self._measurement_provider = measurement_provider
        self._power_supply_safety = (
            power_supply_safety
            if power_supply_safety is not None
            else default_power_supply_manual_safety()
        )
        self._read_failures: list[MeasurementReadFailure] = []
        self._events: list[Event] = []
        self._active_read_failures: set[
            tuple[str, str]
        ] = set()
        self._power_supply_modes: dict[
            str,
            PowerSupplyOperatingMode,
        ] = {}

        self._power_supply_targets: dict[
            tuple[str, PowerSupplyOperatingMode],
            float,
        ] = {}

        for device_id in self._device_manager.device_ids:
            device = self._device_manager.get(device_id)

            if not isinstance(device, PowerSupply):
                continue

            self._power_supply_modes[device_id] = (
                PowerSupplyOperatingMode.CONSTANT_CURRENT
            )
            self._power_supply_targets[
                (
                    device_id,
                    PowerSupplyOperatingMode.CONSTANT_CURRENT,
                )
            ] = device.current_limit
            self._power_supply_targets[
                (
                    device_id,
                    PowerSupplyOperatingMode.CONSTANT_VOLTAGE,
                )
            ] = 0.0

    @property
    def control_mode(self) -> str:
        return self._control_service.mode.value

    @property
    def high_current_mode(self) -> bool:
        return self._power_supply_safety.high_current_mode

    @property
    def read_failures(
        self,
    ) -> tuple[MeasurementReadFailure, ...]:
        """Return measurement failures recorded by this model."""

        return tuple(self._read_failures)

    @property
    def events(self) -> tuple[Event, ...]:
        """Return error and recovery events recorded by this model."""

        return tuple(self._events)

    def mfc_rows(self) -> tuple[MfcControlRow, ...]:
        rows: list[MfcControlRow] = []

        for device_id in self._device_manager.device_ids:
            device = self._device_manager.get(device_id)

            if not isinstance(device, MassFlowController):
                continue

            is_available = self._is_available(device.status)
            measurement = self._try_measure_flow(
                device,
                is_available,
            )

            rows.append(
                MfcControlRow(
                    device_id=device.device_id,
                    status=device.status.value,
                    is_available=is_available,
                    flow_setpoint=device.flow_setpoint,
                    measured_flow=(
                        measurement.value
                        if measurement is not None
                        else None
                    ),
                    measurement_time=(
                        measurement.timestamp
                        if measurement is not None
                        else None
                    ),
                    measurement_quality=(
                        measurement.quality.value
                        if measurement is not None
                        else None
                    ),
                    maximum_flow=device.limits.maximum_flow,
                    flow_unit=device.limits.flow_unit,
                )
            )

        return tuple(rows)

    def power_supply_target(
        self,
        device_id: str,
        mode: PowerSupplyOperatingMode,
    ) -> float:
        """Return the remembered target for one manual operating mode."""

        return self._power_supply_targets.get(
            (device_id, mode),
            0.0,
        )

    def effective_current_ceiling(self) -> float:
        """Return the current ceiling manual control is allowed to reach.

        Outside High current mode this is always the fixed wiring-safe
        default, regardless of what the (inaccessible in that state)
        wiring ceiling setting is stored as - see decisions/0014.
        """

        if self._power_supply_safety.high_current_mode:
            return self._power_supply_safety.wiring_current_ceiling_amps
        return DEFAULT_WIRING_CURRENT_CEILING_AMPS

    def _execute_current_limit(
        self,
        device_id: str,
        current: float,
    ) -> ManualActionResult:
        ceiling = self.effective_current_ceiling()

        if current > ceiling:
            guidance = (
                ""
                if self._power_supply_safety.high_current_mode
                else " Enable High current mode in Settings to raise this ceiling."
            )
            return ManualActionResult(
                succeeded=False,
                summary=(
                    f"Requested current {current:g} A for {device_id!r} "
                    f"exceeds the active current ceiling of {ceiling:g} A."
                    + guidance
                ),
            )

        return self._execute(
            SetPowerSupplyCurrentLimit(
                device_id=device_id,
                current=current,
                source=CommandSource.MANUAL,
            )
        )

    def initialize_manual_power_supply_defaults(
        self,
    ) -> tuple[ManualActionResult, ...]:
        """Apply safe, useful defaults before manual control begins."""

        results: list[ManualActionResult] = []

        for device_id in self._device_manager.device_ids:
            device = self._device_manager.get(device_id)

            if not isinstance(device, PowerSupply):
                continue

            if device.output_enabled:
                results.append(
                    ManualActionResult(
                        succeeded=False,
                        summary=(
                            f"Could not initialize manual defaults for "
                            f"{device_id!r} because its output is enabled."
                        ),
                    )
                )
                continue

            mode = self._power_supply_modes.get(
                device_id,
                PowerSupplyOperatingMode.CONSTANT_CURRENT,
            )

            if mode is PowerSupplyOperatingMode.CONSTANT_CURRENT:
                # Current is the setpoint in this mode (left as remembered/
                # 0.0 - untouched here). Voltage is the protective compliance
                # limit: default to the low starting value rather than
                # jumping straight to the instrument's true maximum.
                results.append(
                    self.set_power_supply_voltage(
                        device_id,
                        min(
                            self._power_supply_safety.default_voltage_volts,
                            device.limits.maximum_voltage,
                        ),
                    )
                )
            else:
                # Voltage is the setpoint here (left untouched). Current is
                # the protective compliance limit: default to the low
                # starting value, itself still bounded by whichever wiring
                # ceiling is currently active.
                results.append(
                    self.set_power_supply_current(
                        device_id,
                        min(
                            self._power_supply_safety.default_current_amps,
                            self.effective_current_ceiling(),
                        ),
                    )
                )

        return tuple(results)

    def power_supply_rows(
        self,
    ) -> tuple[PowerSupplyControlRow, ...]:
        rows: list[PowerSupplyControlRow] = []

        for device_id in self._device_manager.device_ids:
            device = self._device_manager.get(device_id)

            if not isinstance(device, PowerSupply):
                continue

            is_available = self._is_available(device.status)

            operating_mode = self._power_supply_modes.get(
                device.device_id,
                PowerSupplyOperatingMode.CONSTANT_CURRENT,
            )

            voltage_measurement = self._try_measure_voltage(
                device,
                is_available,
            )
            current_measurement = self._try_measure_current(
                device,
                is_available,
            )

            rows.append(
                PowerSupplyControlRow(
                    device_id=device.device_id,
                    status=device.status.value,
                    is_available=is_available,
                    operating_mode=operating_mode,
                                        constant_current_target=(
                        self._power_supply_targets.get(
                            (
                                device.device_id,
                                PowerSupplyOperatingMode.CONSTANT_CURRENT,
                            ),
                            0.0,
                        )
                    ),
                    constant_voltage_target=(
                        self._power_supply_targets.get(
                            (
                                device.device_id,
                                PowerSupplyOperatingMode.CONSTANT_VOLTAGE,
                            ),
                            0.0,
                        )
                    ),
                    voltage_setpoint=device.voltage_setpoint,
                    current_setting=device.current_limit,
                    measured_voltage=(
                        voltage_measurement.value
                        if voltage_measurement is not None
                        else None
                    ),
                    measured_current=(
                        current_measurement.value
                        if current_measurement is not None
                        else None
                    ),
                    voltage_measurement_time=(
                        voltage_measurement.timestamp
                        if voltage_measurement is not None
                        else None
                    ),
                    current_measurement_time=(
                        current_measurement.timestamp
                        if current_measurement is not None
                        else None
                    ),
                    voltage_quality=(
                        voltage_measurement.quality.value
                        if voltage_measurement is not None
                        else None
                    ),
                    current_quality=(
                        current_measurement.quality.value
                        if current_measurement is not None
                        else None
                    ),
                    output_enabled=device.output_enabled,
                    maximum_voltage=(
                        device.limits.maximum_voltage
                    ),
                    maximum_current=(
                        device.limits.maximum_current
                    ),
                    maximum_power=(
                        device.limits.maximum_power
                    ),
                )
            )

        return tuple(rows)

    def set_mfc_flow(
        self,
        device_id: str,
        flow: float,
    ) -> ManualActionResult:
        return self._execute(
            SetMfcFlow(
                device_id=device_id,
                flow=flow,
                source=CommandSource.MANUAL,
            )
        )

    def set_power_supply_operating_mode(
        self,
        device_id: str,
        mode: PowerSupplyOperatingMode,
    ) -> ManualActionResult:
        """Switch manual mode while output is disabled."""

        if not isinstance(mode, PowerSupplyOperatingMode):
            return ManualActionResult(
                succeeded=False,
                summary=(
                    "Power-supply operating mode must be "
                    "constant current or constant voltage."
                ),
            )

        try:
            device = self._device_manager.get(device_id)
        except Exception as error:
            return ManualActionResult(
                succeeded=False,
                summary=(
                    f"Could not select an operating mode for "
                    f"{device_id!r}. "
                    f"{type(error).__name__}: {error}"
                ),
                technical_details=format_exc(),
            )

        if not isinstance(device, PowerSupply):
            return ManualActionResult(
                succeeded=False,
                summary=(
                    f"Device {device_id!r} is not a power supply."
                ),
            )

        previous_mode = self._power_supply_modes.get(
            device_id,
            PowerSupplyOperatingMode.CONSTANT_CURRENT,
        )

        if mode is previous_mode:
            return ManualActionResult(
                succeeded=True,
                summary=(
                    f"Power supply {device_id!r} is already set "
                    f"for {mode.value.replace('_', ' ')} operation."
                ),
            )

        if device.output_enabled:
            return ManualActionResult(
                succeeded=False,
                summary=(
                    f"Cannot change operating mode for power supply "
                    f"{device_id!r} while its output is enabled. "
                    "Disable the output first."
                ),
            )

        remembered_target = self._power_supply_targets.get(
            (device_id, mode),
            0.0,
        )

        if mode is PowerSupplyOperatingMode.CONSTANT_CURRENT:
            target_result = self._execute_current_limit(
                device_id,
                remembered_target,
            )

            if not target_result.succeeded:
                return target_result

            voltage_limit = min(
                self._power_supply_safety.default_voltage_volts,
                device.limits.maximum_voltage,
            )
            limit_result = self._execute(
                SetPowerSupplyVoltage(
                    device_id=device_id,
                    voltage=voltage_limit,
                    source=CommandSource.MANUAL,
                )
            )

            target_description = (
                f"current setpoint {remembered_target} A"
            )
            limit_description = (
                f"voltage limit {voltage_limit} V"
            )
        else:
            target_result = self._execute(
                SetPowerSupplyVoltage(
                    device_id=device_id,
                    voltage=remembered_target,
                    source=CommandSource.MANUAL,
                )
            )

            if not target_result.succeeded:
                return target_result

            current_limit = min(
                self._power_supply_safety.default_current_amps,
                self.effective_current_ceiling(),
            )
            limit_result = self._execute_current_limit(
                device_id,
                current_limit,
            )

            target_description = (
                f"voltage setpoint {remembered_target} V"
            )
            limit_description = (
                f"current limit {current_limit} A"
            )

        if not limit_result.succeeded:
            return limit_result

        self._power_supply_modes[device_id] = mode

        return ManualActionResult(
            succeeded=True,
            summary=(
                f"Selected {mode.value.replace('_', ' ')} operation "
                f"for power supply {device_id!r}: "
                f"{target_description}, {limit_description}. "
                "Output remained disabled."
            ),
        )

    def set_power_supply_voltage(
        self,
        device_id: str,
        voltage: float,
    ) -> ManualActionResult:
        result = self._execute(
            SetPowerSupplyVoltage(
                device_id=device_id,
                voltage=voltage,
                source=CommandSource.MANUAL,
            )
        )

        if (
            result.succeeded
            and self._power_supply_modes.get(device_id)
            is PowerSupplyOperatingMode.CONSTANT_VOLTAGE
        ):
            self._power_supply_targets[
                (
                    device_id,
                    PowerSupplyOperatingMode.CONSTANT_VOLTAGE,
                )
            ] = voltage

        return result

    def set_power_supply_current(
        self,
        device_id: str,
        current: float,
    ) -> ManualActionResult:
        result = self._execute_current_limit(device_id, current)

        if (
            result.succeeded
            and self._power_supply_modes.get(device_id)
            is PowerSupplyOperatingMode.CONSTANT_CURRENT
        ):
            self._power_supply_targets[
                (
                    device_id,
                    PowerSupplyOperatingMode.CONSTANT_CURRENT,
                )
            ] = current

        return result
    def set_power_supply_output(
        self,
        device_id: str,
        enabled: bool,
    ) -> ManualActionResult:
        if enabled:
            try:
                device = self._device_manager.get(device_id)
            except Exception as error:
                return ManualActionResult(
                    succeeded=False,
                    summary=(
                        f"Could not enable output for "
                        f"{device_id!r}. "
                        f"{type(error).__name__}: {error}"
                    ),
                    technical_details=format_exc(),
                )

            if not isinstance(device, PowerSupply):
                return ManualActionResult(
                    succeeded=False,
                    summary=(
                        f"Device {device_id!r} is not a power supply."
                    ),
                )

            mode = self._power_supply_modes.get(
                device_id,
                PowerSupplyOperatingMode.CONSTANT_CURRENT,
            )

            if (
                mode
                is PowerSupplyOperatingMode.CONSTANT_CURRENT
                and device.voltage_setpoint <= 0
            ):
                return ManualActionResult(
                    succeeded=False,
                    summary=(
                        f"Cannot enable power supply {device_id!r}: "
                        "the voltage compliance limit is zero. "
                        "Set a voltage limit greater than zero first."
                    ),
                )

            if (
                mode
                is PowerSupplyOperatingMode.CONSTANT_VOLTAGE
                and device.current_limit <= 0
            ):
                return ManualActionResult(
                    succeeded=False,
                    summary=(
                        f"Cannot enable power supply {device_id!r}: "
                        "the current compliance limit is zero. "
                        "Set a current limit greater than zero first."
                    ),
                )

        return self._execute(
            SetPowerSupplyOutput(
                device_id=device_id,
                enabled=enabled,
                source=CommandSource.MANUAL,
            )
        )

    def enter_global_safe_state(self) -> ManualActionResult:
        """Request safe state from every controllable rig device."""

        try:
            result = self._control_service.enter_global_safe_state(
                CommandSource.MANUAL,
            )
        except Exception as error:
            return ManualActionResult(
                succeeded=False,
                summary=(
                    "The global safe-state operation could not "
                    f"be started. {type(error).__name__}: {error}"
                ),
                technical_details=format_exc(),
            )

        summary_lines = [
            "GLOBAL SAFE STATE REQUESTED",
        ]
        technical_sections: list[str] = []

        for device_result in result.device_results:
            marker = "OK" if device_result.succeeded else "FAILED"

            summary_lines.append(
                f"{marker}: {device_result.message}"
            )

            if device_result.technical_details:
                technical_sections.append(
                    device_result.technical_details
                )

        if result.all_succeeded:
            summary_lines.append(
                "All available safe-state operations succeeded. "
            )
        else:
            summary_lines.append(
                "One or more safe-state operations failed. "
                "Other devices were still attempted."
            )

        return ManualActionResult(
            succeeded=result.all_succeeded,
            summary="\n".join(summary_lines),
            technical_details=(
                "\n\n".join(technical_sections)
                if technical_sections
                else None
            ),
        )

    def enter_safe_state(
        self,
        device_id: str,
    ) -> ManualActionResult:
        return self._execute(
            EnterDeviceSafeState(
                device_id=device_id,
                source=CommandSource.MANUAL,
            )
        )

    @staticmethod
    def _is_available(status: DeviceStatus) -> bool:
        """Return whether normal commands and measurements are allowed."""

        return status in {
            DeviceStatus.READY,
            DeviceStatus.DEGRADED,
        }

    def _try_measure_flow(
        self,
        device: MassFlowController,
        is_available: bool,
    ) -> Measurement | None:
        if not is_available:
            return None

        if self._measurement_provider is not None:
            return self._measurement_provider(device.device_id, "mass_flow")

        try:
            measurement = device.measure_flow()
        except Exception as error:
            self._record_read_failure(
                device_id=device.device_id,
                measurement_name="flow",
                error=error,
                technical_details=format_exc(),
            )
            return None

        self._record_read_recovery(
            device_id=device.device_id,
            measurement_name="flow",
        )
        return measurement

    def _try_measure_voltage(
        self,
        device: PowerSupply,
        is_available: bool,
    ) -> Measurement | None:
        if not is_available:
            return None

        if self._measurement_provider is not None:
            return self._measurement_provider(device.device_id, "voltage")

        try:
            measurement = device.measure_voltage()
        except Exception as error:
            self._record_read_failure(
                device_id=device.device_id,
                measurement_name="voltage",
                error=error,
                technical_details=format_exc(),
            )
            return None

        self._record_read_recovery(
            device_id=device.device_id,
            measurement_name="voltage",
        )
        return measurement

    def _try_measure_current(
        self,
        device: PowerSupply,
        is_available: bool,
    ) -> Measurement | None:
        if not is_available:
            return None

        if self._measurement_provider is not None:
            return self._measurement_provider(device.device_id, "current")

        try:
            measurement = device.measure_current()
        except Exception as error:
            self._record_read_failure(
                device_id=device.device_id,
                measurement_name="current",
                error=error,
                technical_details=format_exc(),
            )
            return None

        self._record_read_recovery(
            device_id=device.device_id,
            measurement_name="current",
        )
        return measurement

    def _record_read_failure(
        self,
        *,
        device_id: str,
        measurement_name: str,
        error: Exception,
        technical_details: str,
    ) -> None:
        failure_key = (
            device_id,
            measurement_name,
        )

        if failure_key in self._active_read_failures:
            return

        self._active_read_failures.add(failure_key)

        summary = (
            f"Could not read {measurement_name} from "
            f"{device_id!r}. "
            f"{type(error).__name__}: {error}"
        )

        event = Event(
            source=device_id,
            message=summary,
            severity=EventSeverity.ERROR,
        )

        self._events.append(event)

        self._read_failures.append(
            MeasurementReadFailure(
                device_id=device_id,
                measurement_name=measurement_name,
                summary=summary,
                technical_details=technical_details,
                event=event,
            )
        )


    def _record_read_recovery(
        self,
        *,
        device_id: str,
        measurement_name: str,
    ) -> None:
        failure_key = (
            device_id,
            measurement_name,
        )

        if failure_key not in self._active_read_failures:
            return

        self._active_read_failures.remove(failure_key)

        self._events.append(
            Event(
                source=device_id,
                message=(
                    f"{measurement_name.capitalize()} measurement "
                    f"from {device_id!r} recovered."
                ),
                severity=EventSeverity.INFO,
            )
        )

    def _execute(
        self,
        command: ControlCommand,
    ) -> ManualActionResult:
        try:
            result = self._control_service.execute(command)
        except Exception as error:
            return ManualActionResult(
                succeeded=False,
                summary=(
                    f"Manual command failed for "
                    f"{command.device_id!r}. "
                    f"{type(error).__name__}: {error}"
                ),
                technical_details=format_exc(),
            )

        return ManualActionResult(
            succeeded=True,
            summary=result.message,
        )

    def power_supply_mode(self, device_id: str) -> PowerSupplyOperatingMode:
        return self._power_supply_modes.get(
            device_id, PowerSupplyOperatingMode.CONSTANT_CURRENT
        )

    def controller_rows(self) -> tuple[ControllerControlRow, ...]:
        rows: list[ControllerControlRow] = []
        for device_id in self._device_manager.device_ids:
            device = self._device_manager.get(device_id)
            if not isinstance(device, Esp32Controller):
                continue
            available = self._is_available(device.status)
            status = device.controller_status
            outputs = status.get("outputs", {})
            rows.append(ControllerControlRow(
                device_id=device.device_id,
                status=device.status.value,
                is_available=available,
                led_enabled=bool(outputs.get("led", False)) if isinstance(outputs, dict) else False,
                safe_state_active=status.get("safe_state_active") is True,
                watchdog_tripped=status.get("watchdog_tripped") is True,
            ))
        return tuple(rows)

    def set_controller_output(self, device_id: str, output_name: str, enabled: bool) -> ManualActionResult:
        return self._execute(SetControllerOutput(device_id, output_name, enabled, CommandSource.MANUAL))

    def rearm_controller(self, device_id: str) -> ManualActionResult:
        return self._execute(RearmController(device_id, CommandSource.MANUAL))
