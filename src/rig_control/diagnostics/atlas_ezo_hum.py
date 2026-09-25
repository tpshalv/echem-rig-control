"""Commissioning check for one Atlas Scientific EZO-HUM UART probe."""

from dataclasses import dataclass

from rig_control.devices.atlas_ezo_hum.configuration import EzoHumConfiguration
from rig_control.devices.atlas_ezo_hum.driver import AtlasEzoHum
from rig_control.devices.atlas_ezo_hum.protocol import EzoHumIdentity, EzoHumProtocol
from rig_control.devices.measurement_source import DeviceMeasurement
from rig_control.transports.pyserial_text import PySerialTextTransport
from rig_control.transports.serial_text import SerialTextTransport


@dataclass(frozen=True, slots=True)
class EzoHumDiagnosticResult:
    identity: EzoHumIdentity
    readings: tuple[DeviceMeasurement, ...]


def check_ezo_hum(
    configuration: EzoHumConfiguration,
    transport: SerialTextTransport | None = None,
) -> EzoHumDiagnosticResult:
    """Configure the temporary UART output, verify identity, read, and close."""
    selected_transport = transport or PySerialTextTransport(
        configuration.port, 9600, configuration.timeout_seconds
    )
    device = AtlasEzoHum(configuration, EzoHumProtocol(selected_transport))
    try:
        device.connect()
        assert device.identity is not None
        return EzoHumDiagnosticResult(device.identity, device.read_measurements())
    finally:
        selected_transport.close()
