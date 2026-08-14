from dataclasses import dataclass
from traceback import format_exc

from rig_control.control.commands import (
    CommandSource,
    EnterDeviceSafeState,
    SetMfcFlow,
    SetPowerSupplyCurrentLimit,
    SetPowerSupplyOutput,
    SetPowerSupplyVoltage,
    ControlCommand,
)

from rig_control.control.service import RigControlService

from rig_control.devices.manager import DeviceManager
from rig_control.devices.mass_flow_controller import (
    MassFlowController,
)
from rig_control.devices.power_supply import PowerSupply


@dataclass(frozen=True, slots=True)
class ManualActionResult:
    """Result displayed after a manual control action."""

    succeeded: bool
    summary: str
    technical_details: str | None = None


@dataclass(frozen=True, slots=True)
class MfcControlRow:
    """Current manual-control information for one MFC."""

    device_id: str
    status: str
    flow_setpoint: float
    maximum_flow: float
    flow_unit: str


@dataclass(frozen=True, slots=True)
class PowerSupplyControlRow:
    """Current manual-control information for one supply."""

    device_id: str
    status: str
    voltage_setpoint: float
    current_setting: float
    output_enabled: bool
    maximum_voltage: float
    maximum_current: float
    maximum_power: float


class ManualControlViewModel:
    """GUI-independent manual control logic."""

    def __init__(
        self,
        device_manager: DeviceManager,
        control_service: RigControlService,
    ) -> None:
        self._device_manager = device_manager
        self._control_service = control_service

    @property
    def control_mode(self) -> str:
        return self._control_service.mode.value

    def mfc_rows(self) -> tuple[MfcControlRow, ...]:
        rows: list[MfcControlRow] = []

        for device_id in self._device_manager.device_ids:
            device = self._device_manager.get(device_id)

            if isinstance(device, MassFlowController):
                rows.append(
                    MfcControlRow(
                        device_id=device.device_id,
                        status=device.status.value,
                        flow_setpoint=device.flow_setpoint,
                        maximum_flow=device.limits.maximum_flow,
                        flow_unit=device.limits.flow_unit,
                    )
                )

        return tuple(rows)

    def power_supply_rows(
        self,
    ) -> tuple[PowerSupplyControlRow, ...]:
        rows: list[PowerSupplyControlRow] = []

        for device_id in self._device_manager.device_ids:
            device = self._device_manager.get(device_id)

            if isinstance(device, PowerSupply):
                rows.append(
                    PowerSupplyControlRow(
                        device_id=device.device_id,
                        status=device.status.value,
                        voltage_setpoint=device.voltage_setpoint,
                        current_setting=device.current_limit,
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

    def set_power_supply_voltage(
        self,
        device_id: str,
        voltage: float,
    ) -> ManualActionResult:
        return self._execute(
            SetPowerSupplyVoltage(
                device_id=device_id,
                voltage=voltage,
                source=CommandSource.MANUAL,
            )
        )

    def set_power_supply_current(
        self,
        device_id: str,
        current: float,
    ) -> ManualActionResult:
        return self._execute(
            SetPowerSupplyCurrentLimit(
                device_id=device_id,
                current=current,
                source=CommandSource.MANUAL,
            )
        )

    def set_power_supply_output(
        self,
        device_id: str,
        enabled: bool,
    ) -> ManualActionResult:
        return self._execute(
            SetPowerSupplyOutput(
                device_id=device_id,
                enabled=enabled,
                source=CommandSource.MANUAL,
            )
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