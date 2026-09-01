from pathlib import Path


FIRMWARE = Path("firmware/nano_esp32_controller/nano_esp32_controller.ino")


def test_physical_watchdog_does_not_trip_before_first_heartbeat() -> None:
    source = FIRMWARE.read_text(encoding="utf-8")

    watchdog_helper = source.split(
        "bool watchdogCurrentlyExpired() {", 1
    )[1].split("// ============================================================", 1)[0]

    assert "if (!heartbeatReceived)" in watchdog_helper
    assert "return false;" in watchdog_helper


def test_re72_bus_uses_required_uart_pins_and_framing() -> None:
    source = FIRMWARE.read_text(encoding="utf-8")

    assert "static const uint8_t RS485_RX_PIN = D6;" in source
    assert "static const uint8_t RS485_TX_PIN = D7;" in source
    assert "static const uint32_t RS485_BAUD_RATE = 9600;" in source
    assert "SERIAL_8N2" in source


def test_re72_bridge_supports_two_slave_discovery_and_register_access() -> None:
    source = FIRMWARE.read_text(encoding="utf-8")

    assert "RE72_PROBE_ADDRESSES[] = {1, 2}" in source
    assert '"modbus_read_holding"' in source
    assert '"modbus_write_register"' in source
    assert 'device["kind"] = "lumel_re72";' in source
    assert "MODBUS_MAX_READ_REGISTERS = 64" in source
