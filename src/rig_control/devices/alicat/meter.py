from rig_control.devices.alicat.configuration import AlicatMfcConfiguration
from rig_control.devices.alicat.protocol import (
    AlicatInstrumentState,
    AlicatProtocolClient,
)
from rig_control.devices.base import Device
from rig_control.devices.measurement_source import DeviceMeasurement, MeasurementSource
from rig_control.models import DeviceStatus, Measurement


class AlicatMassFlowMeter(Device, MeasurementSource):
    """Read-only rig adapter for one addressed Alicat mass-flow meter."""

    def __init__(
        self,
        configuration: AlicatMfcConfiguration,
        protocol: AlicatProtocolClient,
    ) -> None:
        if configuration.is_controller:
            raise ValueError("Alicat meter configuration cannot be a controller")
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
    def unit_address(self) -> str:
        return self._configuration.unit_address

    @property
    def current_state(self) -> AlicatInstrumentState | None:
        return self._state

    def connect(self) -> None:
        if self.status is not DeviceStatus.DISCONNECTED:
            raise RuntimeError(f"Alicat meter {self.device_id!r} is already connected")
        self._status = DeviceStatus.CONNECTING
        try:
            self._protocol.connect()
            self._state = self._read_state("connect")
        except Exception:
            self._status = DeviceStatus.FAULTED
            self._protocol.disconnect()
            raise
        self._status = DeviceStatus.READY

    def disconnect(self) -> None:
        self._protocol.disconnect()
        self._state = None
        self._status = DeviceStatus.DISCONNECTED

    def read_measurements(self) -> tuple[DeviceMeasurement, ...]:
        self._require_ready()
        state = self._read_state("read state")
        self._state = state
        readings = [
            _reading("mass_flow", state.mass_flow, state.mass_flow_unit, state),
            _reading(
                "volumetric_flow",
                state.volumetric_flow,
                state.volumetric_flow_unit,
                state,
            ),
            _reading(
                "absolute_pressure",
                state.absolute_pressure,
                state.pressure_unit,
                state,
            ),
            _reading(
                "gas_temperature",
                state.gas_temperature,
                state.temperature_unit,
                state,
            ),
        ]
        if state.totalized_flow is not None:
            readings.append(
                _reading(
                    "totalized_flow",
                    state.totalized_flow,
                    state.totalized_flow_unit or "",
                    state,
                )
            )
        return tuple(readings)

    def _read_state(self, operation: str) -> AlicatInstrumentState:
        try:
            return self._protocol.read_state(self.unit_address)
        except Exception as error:
            self._status = DeviceStatus.FAULTED
            connection = self._configuration.connection
            raise RuntimeError(
                f"Alicat meter {self.device_id!r} on {connection.port}, "
                f"address {self.unit_address!r}, failed to {operation}: "
                f"{type(error).__name__}: {error}"
            ) from error

    def _require_ready(self) -> None:
        if self.status is not DeviceStatus.READY:
            raise RuntimeError(f"Alicat meter {self.device_id!r} is not ready")


def _reading(
    channel: str,
    value: float,
    unit: str,
    state: AlicatInstrumentState,
) -> DeviceMeasurement:
    return DeviceMeasurement(
        channel,
        Measurement(value, unit, state.timestamp, state.quality),
    )
