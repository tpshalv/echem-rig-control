"""Read-only readiness check for the OHAUS Guardian 5000 hotplate/stirrer."""

from dataclasses import dataclass

from rig_control.devices.ohaus_guardian_5000.configuration import GuardianConfiguration
from rig_control.devices.ohaus_guardian_5000.driver import OhausGuardian5000
from rig_control.devices.ohaus_guardian_5000.protocol import GuardianIdentity, OperatingMode
from rig_control.transports.pyserial_text import PySerialTextTransport
from rig_control.transports.serial_text import SerialTextTransport


@dataclass(frozen=True, slots=True)
class GuardianDiagnosticResult:
    """Identity and measurements from one connect, without starting/stopping."""

    identity: GuardianIdentity
    mode: OperatingMode
    temperature: float | None
    probe_temperature: float | None
    stir_speed: float | None


def read_guardian_state(
    configuration: GuardianConfiguration,
    transport: SerialTextTransport | None = None,
) -> GuardianDiagnosticResult:
    """Connect, read identity and measurements, and close the port.

    Uses the driver's own connect()/read_measurements() so identity and
    limit checks match normal operation. Closes the transport directly
    afterwards instead of calling disconnect(), which would also send
    stop commands - this check must not touch heating or stirring state.
    """

    selected_transport = transport or PySerialTextTransport(
        configuration.port,
        9600,
        configuration.timeout_seconds,
        line_ending="\r\n",
        xonxoff=True,
    )
    device = OhausGuardian5000(
        configuration.device_id, selected_transport, configuration.limits
    )
    try:
        device.connect()
        readings = {
            measurement.channel: measurement.measurement.value
            for measurement in device.read_measurements()
        }
        assert device.identity is not None
        assert device.operating_mode is not None
        return GuardianDiagnosticResult(
            identity=device.identity,
            mode=device.operating_mode,
            temperature=readings.get("temperature"),
            probe_temperature=readings.get("probe_temperature"),
            stir_speed=readings.get("stir_speed"),
        )
    finally:
        selected_transport.close()
