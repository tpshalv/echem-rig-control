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


def test_sht85_is_advertised_separately_from_dht11() -> None:
    source = FIRMWARE.read_text(encoding="utf-8")

    assert "#include <Adafruit_SHT31.h>" in source
    assert "#include <Wire.h>" in source
    assert 'dht11Device["id"] = "esp32_dht11";' in source
    assert 'sht85Device["id"] = "esp32_sht85";' in source
    assert 'sht85Device["kind"] = "sht85";' in source
    assert 'sht85Temperature["name"] = "temperature";' in source
    assert 'sht85Humidity["name"] = "humidity";' in source
    assert 'sht85Heater["name"] = "heater";' in source


def test_sht85_readings_have_device_identity_and_heater_quality_reasons() -> None:
    source = FIRMWARE.read_text(encoding="utf-8")

    assert 'sht85Temperature["device_id"] = "esp32_sht85";' in source
    assert 'sht85Humidity["device_id"] = "esp32_sht85";' in source
    assert 'sht85Heater["device_id"] = "esp32_sht85";' in source
    assert 'sht85Temperature["quality_reason"] = sht85QualityReason();' in source
    assert 'sht85Humidity["quality_reason"] = sht85QualityReason();' in source
    assert '"heater_active"' in source
    assert '"heater_cooldown"' in source


def test_sht85_heater_is_momentary_bounded_and_evented() -> None:
    source = FIRMWARE.read_text(encoding="utf-8")

    assert '"sht85_heater"' in source
    assert '"run_heater"' in source
    assert "SHT85_SHORT_PULSE_MS = 100" in source
    assert "SHT85_LONG_PULSE_MS = 1000" in source
    assert "SHT85_SHORT_COOLDOWN_MS = 5000" in source
    assert "SHT85_LONG_COOLDOWN_MS = 10000" in source
    assert "sht85.heater(true);" in source
    assert "delay(durationMs);" in source
    assert "sht85.heater(false);" in source
    assert 'recordSht85HeaterEvent("pulse_started")' in source
    assert 'recordSht85HeaterEvent("cooldown_started")' in source
    assert 'recordSht85HeaterEvent("returned_to_off")' in source
    assert 'target["pulse_count"] = sht85HeaterPulseCount;' in source
