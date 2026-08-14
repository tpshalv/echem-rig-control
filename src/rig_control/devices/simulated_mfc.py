from math import isfinite

from rig_control.devices.mass_flow_controller import (
    MassFlowController,
    MassFlowControllerLimits,
)
from rig_control.models import DeviceStatus, Measurement


class SimulatedMassFlowController(MassFlowController):
    """Safe software-only model of a mass flow controller."""

    def __init__(
        self,
        device_id: str,
        limits: MassFlowControllerLimits,
    ) -> None:
        if not isinstance(device_id, str) or not device_id.strip():
            raise ValueError("MFC device ID cannot be empty")

        self._device_id = device_id
        self._limits = limits
        self._status = DeviceStatus.DISCONNECTED
        self._flow_setpoint = 0.0
        self._measured_flow = 0.0

    @property
    def device_id(self) -> str:
        return self._device_id

    @property
    def status(self) -> DeviceStatus:
        return self._status

    @property
    def limits(self) -> MassFlowControllerLimits:
        return self._limits

    @property
    def flow_setpoint(self) -> float:
        return self._flow_setpoint

    def connect(self) -> None:
        self._status = DeviceStatus.READY

    def disconnect(self) -> None:
        self.enter_safe_state()
        self._status = DeviceStatus.DISCONNECTED

    def set_flow_setpoint(self, flow: float) -> None:
        self._require_ready()
        numeric_flow = self._validate_flow(flow)
        self._flow_setpoint = numeric_flow

    def measure_flow(self) -> Measurement:
        self._require_ready()

        return Measurement(
            self._measured_flow,
            self.limits.flow_unit,
        )

    def set_simulated_measurement(self, flow: float) -> None:
        """Set the flow returned by the simulated measurement."""

        self._require_ready()
        self._measured_flow = self._validate_flow(flow)

    def enter_safe_state(self) -> None:
        """Set requested and measured flow to zero."""

        self._flow_setpoint = 0.0
        self._measured_flow = 0.0

    def _require_ready(self) -> None:
        if self.status is not DeviceStatus.READY:
            raise RuntimeError(
                f"Mass flow controller {self.device_id!r} "
                "is not ready"
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
                f"Flow {numeric_flow} {self.limits.flow_unit} "
                f"exceeds configured maximum "
                f"{self.limits.maximum_flow} "
                f"{self.limits.flow_unit}"
            )

        return numeric_flow