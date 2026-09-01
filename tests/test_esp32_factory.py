from rig_control.device_factory import create_device_manager
from rig_control.devices.esp32_controller import Esp32Controller
from rig_control.devices.esp32_dht11 import Esp32Dht11
from rig_control.esp32.protocol import Message
from rig_control.esp32.protocol_handler import ControllerProtocolHandler
from rig_control.esp32.simulated_controller import SimulatedController
from rig_control.polling import PollingService
from rig_control.rig_profile import (
    ConnectionDefinition,
    DeviceBackend,
    DeviceCapability,
    DeviceRole,
    RigProfile,
)
from rig_control.transports.loopback_duplex_text import LoopbackDuplexTextTransport


class CountingLoopbackTransport(LoopbackDuplexTextTransport):
    def __init__(self, responder):  # type: ignore[no-untyped-def]
        super().__init__(responder)
        self.connect_count = 0
        self.disconnect_count = 0

    def connect(self) -> None:
        self.connect_count += 1
        super().connect()

    def disconnect(self) -> None:
        self.disconnect_count += 1
        super().disconnect()


def make_profile() -> RigProfile:
    connection = ConnectionDefinition(
        "main_esp32",
        "serial_json",
        {
            "port": "COM5",
            "baud_rate": 115200,
            "timeout_seconds": 2.0,
            "controller_id": "esp32_main_controller",
        },
    )
    controller = DeviceRole(
        "esp32_main_controller",
        "Main controller",
        DeviceCapability.REMOTE_CONTROLLER,
        "esp32_json",
        DeviceBackend.REAL,
        connection_id="main_esp32",
    )
    sensor = DeviceRole(
        "esp32_dht11",
        "DHT11",
        DeviceCapability.TEMPERATURE_SENSOR,
        "esp32_dht11",
        DeviceBackend.REAL,
        poll_interval_seconds=1.5,
        connection_id="main_esp32",
    )
    return RigProfile("esp32_test", "ESP32 test", (controller, sensor), (connection,))


def test_factory_shares_one_connection_and_polling_reads_both_channels() -> None:
    simulated = SimulatedController(
        {},
        15,
        sensor_channels=[
            {"name": "temperature", "value": 21.5, "unit": "degC", "quality": "good"},
            {"name": "humidity", "value": 48.0, "unit": "%RH", "quality": "good"},
        ],
    )
    handler = ControllerProtocolHandler(
        simulated,
        controller_id="esp32_main_controller",
    )
    transport = CountingLoopbackTransport(
        lambda text: handler.handle(Message.from_json(text)).to_json()
    )
    manager = create_device_manager(
        make_profile(),
        esp32_transport_factory=lambda _: transport,
    )
    controller = manager.get("esp32_main_controller")
    sensor = manager.get("esp32_dht11")

    assert isinstance(controller, Esp32Controller)
    assert isinstance(sensor, Esp32Dht11)
    assert controller._bus is sensor._bus  # type: ignore[attr-defined]

    manager.connect(controller.device_id)
    manager.connect(sensor.device_id)
    batch = PollingService(manager).poll_once()

    assert transport.connect_count == 1
    assert [(record.channel, record.measurement.value) for record in batch.measurements] == [
        ("temperature", 21.5),
        ("humidity", 48.0),
    ]

    manager.disconnect(controller.device_id)
    assert transport.is_connected is True
    manager.disconnect(sensor.device_id)
    assert transport.disconnect_count == 1
    assert transport.is_connected is False
