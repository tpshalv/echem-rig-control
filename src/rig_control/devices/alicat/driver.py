from math import isfinite

from rig_control.devices.alicat.configuration import AlicatMfcConfiguration
from rig_control.devices.alicat.protocol import (
    AlicatInstrumentState,
    AlicatProtocolClient,
)
from rig_control.devices.mass_flow_controller import (
    MassFlowController,
    MassFlowControllerLimits,
)
from rig_control.models import DeviceStatus, Measurement


class AlicatMassFlowController(MassFlowController):
    """Rig-facing adapter for one addressed Alicat MFC."""

    def __init__(
        self,
        configuration: AlicatMfcConfiguration,
        protocol: AlicatProtocolClient,
    ) -> None:
        if not isinstance(configuration, AlicatMfcConfiguration):
            raise TypeError("configuration must be AlicatMfcConfiguration")
        if not isinstance(protocol, AlicatProtocolClient):
            raise TypeError("protocol must be an AlicatProtocolClient")

        self._configuration = configuration
        self._protocol = protocol
        self._status = DeviceStatus.DISCONNECTED
        self._state: AlicatInstrumentState | None = None

    @property
    def device_id(self) -> str:
        return self._configuration.device_id

    @property
    def status(self) -> DeviceStatus:
        return self._status

    @property
    def limits(self) -> MassFlowControllerLimits:
        return self._configuration.limits

    @property
    def unit_address(self) -> str:
        return self._configuration.unit_address

    @property
    def current_state(self) -> AlicatInstrumentState | None:
        """Return the most recently confirmed instrument state."""

        return self._state

    @property
    def flow_setpoint(self) -> float:
        return 0.0 if self._state is None else self._state.setpoint

    def connect(self) -> None:
        """Confirm communication by reading actual instrument state."""

        if self.status is not DeviceStatus.DISCONNECTED:
            raise RuntimeError(
                f"Alicat MFC {self.device_id!r} is already connected"
            )

        self._status = DeviceStatus.CONNECTING
        try:
            self._refresh_state("connect")
        except Exception:
            self._status = DeviceStatus.FAULTED
            raise
        self._status = DeviceStatus.READY

    def disconnect(self) -> None:
        """Forget cached state without closing the shared serial bus."""

        self._state = None
        self._status = DeviceStatus.DISCONNECTED

    def set_flow_setpoint(self, flow: float) -> None:
        self._require_ready()
        numeric_flow = self._validate_flow(flow)

        try:
            self._protocol.set_flow_setpoint(
                self.unit_address,
                numeric_flow,
            )
            # A command acknowledgement is not proof of the current state.
            # Query again so reconnects and failed commands are never assumed.
            self._refresh_state("verify set flow")
        except Exception as error:
            self._status = DeviceStatus.FAULTED
            if isinstance(error, RuntimeError) and str(error).startswith(
                "Alicat MFC"
            ):
                raise
            raise self._operation_error("set flow", error) from error

    def measure_flow(self) -> Measurement:
        self._require_ready()
        state = self._refresh_state("read state")
        return Measurement(
            value=state.mass_flow,
            unit=state.mass_flow_unit,
            timestamp=state.timestamp,
            quality=state.quality,
        )

    def enter_safe_state(self) -> None:
        if self.status is DeviceStatus.DISCONNECTED:
            return
        self.set_flow_setpoint(0.0)

    def _refresh_state(self, operation: str) -> AlicatInstrumentState:
        try:
            state = self._protocol.read_state(self.unit_address)
        except Exception as error:
            self._status = DeviceStatus.FAULTED
            raise self._operation_error(operation, error) from error

        if not isinstance(state, AlicatInstrumentState):
            error = TypeError(
                "protocol returned a value other than AlicatInstrumentState"
            )
            self._status = DeviceStatus.FAULTED
            raise self._operation_error(operation, error) from error

        configured_unit = self.limits.flow_unit.strip().casefold()
        reported_units = {
            state.mass_flow_unit.strip().casefold(),
            state.setpoint_unit.strip().casefold(),
        }
        if reported_units != {configured_unit}:
            error = ValueError(
                "reported mass-flow/setpoint units "
                f"{state.mass_flow_unit!r}/{state.setpoint_unit!r} do "
                f"not match configured unit {self.limits.flow_unit!r}"
            )
            self._status = DeviceStatus.FAULTED
            raise self._operation_error(operation, error) from error

        self._state = state
        return state

    def _require_ready(self) -> None:
        if self.status is not DeviceStatus.READY:
            raise RuntimeError(
                f"Alicat MFC {self.device_id!r} at address "
                f"{self.unit_address!r} is not ready"
            )

    def _validate_flow(self, flow: float) -> float:
        if isinstance(flow, bool) or not isinstance(flow, (int, float)):
            raise TypeError("Flow must be an int or float")

        numeric_flow = float(flow)
        if not isfinite(numeric_flow):
            raise ValueError("Flow must be finite")
        if numeric_flow < 0:
            raise ValueError("Flow cannot be negative")
        if numeric_flow > self.limits.maximum_flow:
            raise ValueError(
                f"Flow {numeric_flow} {self.limits.flow_unit} exceeds "
                f"configured maximum {self.limits.maximum_flow} "
                f"{self.limits.flow_unit}"
            )
        return numeric_flow

    def _operation_error(
        self,
        operation: str,
        error: Exception,
    ) -> RuntimeError:
        return RuntimeError(
            f"Alicat MFC {self.device_id!r} on "
            f"{self._configuration.connection.port}, address "
            f"{self.unit_address!r}, failed to {operation}: "
            f"{type(error).__name__}: {error}"
        )
