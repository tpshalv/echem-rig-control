from dataclasses import dataclass
from datetime import datetime, timezone
from traceback import format_exc
from enum import StrEnum

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
    SetTemperatureSetpoint,
)
from rig_control.devices.esp32_controller import Esp32Controller
from rig_control.devices.lumel_re72 import LumelRe72
from rig_control.devices.manager import DeviceManager
from rig_control.devices.mass_flow_controller import (
    MassFlowController,
)
from rig_control.devices.power_supply import PowerSupply
from rig_control.devices.safe_state import SafeStateCapable


class ControlMode(StrEnum):
    """Who may issue ordinary control commands."""

    IDLE = "idle"
    RECIPE_ACTIVE = "recipe_active"
    FAULT_LOCKED = "fault_locked"


class ControlAccessError(RuntimeError):
    """A command is not permitted in the current control mode."""


class ControlExecutionError(RuntimeError):
    """An authorized command failed during execution."""


@dataclass(frozen=True, slots=True)
class ExecutedCommand:
    """Audit record for one successfully executed command."""

    command: ControlCommand
    executed_at: datetime
    message: str

@dataclass(frozen=True, slots=True)
class SafeStateDeviceResult:
    """Result of requesting safe state from one device."""

    device_id: str
    succeeded: bool
    message: str
    technical_details: str | None = None


@dataclass(frozen=True, slots=True)
class GlobalSafeStateResult:
    """Combined result of requesting safe state across the rig."""

    requested_at: datetime
    device_results: tuple[SafeStateDeviceResult, ...]

    @property
    def all_succeeded(self) -> bool:
        return all(
            result.succeeded
            for result in self.device_results
        )

    @property
    def failures(self) -> tuple[SafeStateDeviceResult, ...]:
        return tuple(
            result
            for result in self.device_results
            if not result.succeeded
        )

class RigControlService:
    """Authorize and route commands to registered device capabilities."""

    def __init__(self, device_manager: DeviceManager) -> None:
        self._device_manager = device_manager
        self._mode = ControlMode.IDLE
        self._history: list[ExecutedCommand] = []

    @property
    def mode(self) -> ControlMode:
        return self._mode

    @property
    def history(self) -> tuple[ExecutedCommand, ...]:
        return tuple(self._history)

    def begin_recipe_control(self) -> None:
        if self.mode is not ControlMode.IDLE:
            raise ControlAccessError(
                "Recipe control can begin only while control mode "
                f"is idle; current mode is {self.mode.value!r}"
            )

        self._mode = ControlMode.RECIPE_ACTIVE

    def end_recipe_control(self) -> None:
        if self.mode is not ControlMode.RECIPE_ACTIVE:
            raise ControlAccessError(
                "Recipe control is not currently active"
            )

        self._mode = ControlMode.IDLE

    def enter_fault_lock(self) -> None:
        """Block ordinary control until explicitly cleared."""

        self._mode = ControlMode.FAULT_LOCKED

    def clear_fault_lock(self) -> None:
        if self.mode is not ControlMode.FAULT_LOCKED:
            raise ControlAccessError(
                "Control service is not fault-locked"
            )

        self._mode = ControlMode.IDLE

    def enter_global_safe_state(
        self,
        source: CommandSource = CommandSource.SAFETY_SYSTEM,
    ) -> GlobalSafeStateResult:
        """Request safe state from every capable device.

        Every device is attempted even if another device fails.
        """

        requested_at = datetime.now(timezone.utc)
        results: list[SafeStateDeviceResult] = []

        # A safe-state request stops recipe control, but it does not create
        # or clear a genuine fault lock.
        if self.mode is ControlMode.RECIPE_ACTIVE:
            self._mode = ControlMode.IDLE

        for device_id in self._device_manager.device_ids:
            device = self._device_manager.get(device_id)

            # Sensors and other read-only devices may have no physical
            # safe-state action. They are not treated as failures.
            if not isinstance(device, SafeStateCapable):
                continue

            try:
                executed = self.execute(
                    EnterDeviceSafeState(
                        device_id=device_id,
                        source=source,
                    )
                )
            except Exception as error:
                results.append(
                    SafeStateDeviceResult(
                        device_id=device_id,
                        succeeded=False,
                        message=(
                            f"Safe-state request failed for "
                            f"{device_id!r}. "
                            f"{type(error).__name__}: {error}"
                        ),
                        technical_details=format_exc(),
                    )
                )
                continue

            results.append(
                SafeStateDeviceResult(
                    device_id=device_id,
                    succeeded=True,
                    message=executed.message,
                )
            )

        return GlobalSafeStateResult(
            requested_at=requested_at,
            device_results=tuple(results),
        )

    def execute(
        self,
        command: ControlCommand,
    ) -> ExecutedCommand:
        """Authorize, execute and record one command."""

        self._validate_command_type(command)
        self._authorize(command)

        try:
            with self._device_manager.operation(command.device_id):
                message = self._dispatch(command)
        except ControlAccessError:
            raise
        except Exception as error:
            raise ControlExecutionError(
                f"Command {type(command).__name__} failed for "
                f"device {command.device_id!r}: "
                f"{type(error).__name__}: {error}"
            ) from error

        record = ExecutedCommand(
            command=command,
            executed_at=datetime.now(timezone.utc),
            message=message,
        )
        self._history.append(record)

        return record

    def _authorize(self, command: ControlCommand) -> None:
        if isinstance(command, EnterDeviceSafeState):
            return

        source = command.source

        if source is CommandSource.SAFETY_SYSTEM:
            return

        if self.mode is ControlMode.FAULT_LOCKED:
            raise ControlAccessError(
                f"{source.value} command "
                f"{type(command).__name__} is blocked because "
                "the control service is fault-locked"
            )

        if self.mode is ControlMode.RECIPE_ACTIVE:
            if source is not CommandSource.RECIPE:
                raise ControlAccessError(
                    f"{source.value} command "
                    f"{type(command).__name__} is blocked while "
                    "a recipe controls the rig"
                )

            return

        if source is CommandSource.RECIPE:
            raise ControlAccessError(
                f"Recipe command {type(command).__name__} is "
                "blocked because recipe control has not begun"
            )

    def _dispatch(self, command: ControlCommand) -> str:
        device = self._device_manager.get(command.device_id)

        if isinstance(command, SetMfcFlow):
            if not isinstance(device, MassFlowController):
                raise TypeError(
                    f"Device {command.device_id!r} is not a "
                    "mass flow controller"
                )

            device.set_flow_setpoint(command.flow)

            return (
                f"Set MFC {command.device_id!r} flow to "
                f"{command.flow} {device.limits.flow_unit}."
            )

        if isinstance(command, SetPowerSupplyVoltage):
            supply = self._require_power_supply(
                device,
                command.device_id,
            )
            supply.set_voltage(command.voltage)

            return (
                f"Set power supply {command.device_id!r} "
                f"voltage to {command.voltage} V."
            )

        if isinstance(command, SetPowerSupplyCurrentLimit):
            supply = self._require_power_supply(
                device,
                command.device_id,
            )
            supply.set_current_limit(command.current)

            return (
                f"Set power supply {command.device_id!r} "
                f"current setting to {command.current} A."
            )

        if isinstance(command, SetPowerSupplyOutput):
            supply = self._require_power_supply(
                device,
                command.device_id,
            )
            supply.set_output_enabled(command.enabled)

            state = "enabled" if command.enabled else "disabled"

            return (
                f"Power supply {command.device_id!r} "
                f"output {state}."
            )

        if isinstance(command, SetControllerOutput):
            controller = self._require_esp32_controller(device, command.device_id)
            controller.set_output(command.output_name, command.enabled)
            state = "enabled" if command.enabled else "disabled"
            return f"Controller {command.device_id!r} output {command.output_name!r} {state}."

        if isinstance(command, RearmController):
            controller = self._require_esp32_controller(device, command.device_id)
            controller.rearm()
            return f"Controller {command.device_id!r} watchdog rearmed."

        if isinstance(command, SetTemperatureSetpoint):
            if not isinstance(device, LumelRe72):
                raise TypeError(f"Device {command.device_id!r} is not an RE72")
            device.set_target_setpoint(command.value)
            return (
                f"Set RE72 {command.device_id!r} target setpoint to "
                f"{command.value:g} degC."
            )

        if isinstance(command, EnterDeviceSafeState):
            if not isinstance(device, SafeStateCapable):
                raise TypeError(
                    f"Device {command.device_id!r} does not "
                    "provide a safe-state operation"
                )

            device.enter_safe_state()

            return (
                f"Device {command.device_id!r} entered its "
                "configured safe state."
            )

        raise TypeError(
            f"Unsupported control command: "
            f"{type(command).__name__}"
        )

    @staticmethod
    def _require_power_supply(
        device: object,
        device_id: str,
    ) -> PowerSupply:
        if not isinstance(device, PowerSupply):
            raise TypeError(
                f"Device {device_id!r} is not a power supply"
            )

        return device

    @staticmethod
    def _require_esp32_controller(device: object, device_id: str) -> Esp32Controller:
        if not isinstance(device, Esp32Controller):
            raise TypeError(f"Device {device_id!r} is not an ESP32 controller")
        return device

    @staticmethod
    def _validate_command_type(command: object) -> None:
        supported_types = (
            SetMfcFlow,
            SetPowerSupplyVoltage,
            SetPowerSupplyCurrentLimit,
            SetPowerSupplyOutput,
            EnterDeviceSafeState,
            SetControllerOutput,
            RearmController,
            SetTemperatureSetpoint,
        )

        if not isinstance(command, supported_types):
            raise TypeError(
                "Control service received an unsupported "
                f"command object: {type(command).__name__}"
            )
