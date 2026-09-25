"""Read-only readiness check for the Kamoer M1-STP peristaltic pump."""

from dataclasses import dataclass

from rig_control.devices.kamoer_m1_stp.configuration import KamoerM1StpConfiguration
from rig_control.devices.kamoer_m1_stp.driver import KamoerM1Stp
from rig_control.devices.kamoer_m1_stp.protocol import KamoerM1StpProtocol
from rig_control.devices.pump import PumpDirection
from rig_control.transports.pyserial_binary import PySerialBinaryTransport
from rig_control.transports.serial_binary import SerialBinaryTransport


@dataclass(frozen=True, slots=True)
class KamoerM1StpDiagnosticResult:
    """State read from one connect, without starting, stopping or reconfiguring the pump."""

    fault_status: int
    running: bool
    direction: PumpDirection
    speed_setpoint_rpm: float


def read_pump_state(
    configuration: KamoerM1StpConfiguration,
    transport: SerialBinaryTransport | None = None,
) -> KamoerM1StpDiagnosticResult:
    """Connect, read current state, and close the port without writing.

    Uses the driver's own connect(), which only issues Modbus reads for
    this pump. Closes the transport directly afterwards instead of calling
    disconnect(), which would also write a stop command - this check must
    not change the pump's running state.
    """

    selected_transport = transport or PySerialBinaryTransport(
        configuration.port, 9600, configuration.timeout_seconds
    )
    protocol = KamoerM1StpProtocol(
        selected_transport, configuration.slave, configuration.timeout_seconds
    )
    device = KamoerM1Stp(configuration, protocol)
    try:
        device.connect()
        assert device.running is not None
        assert device.direction is not None
        assert device.speed_setpoint_rpm is not None
        return KamoerM1StpDiagnosticResult(
            fault_status=device.read_fault_status(),
            running=device.running,
            direction=device.direction,
            speed_setpoint_rpm=device.speed_setpoint_rpm,
        )
    finally:
        selected_transport.close()
