from pathlib import Path


FIRMWARE = Path("firmware/nano_esp32_controller/nano_esp32_controller.ino")


def test_physical_watchdog_does_not_trip_before_first_heartbeat() -> None:
    source = FIRMWARE.read_text(encoding="utf-8")

    watchdog_helper = source.split(
        "bool watchdogCurrentlyExpired() {", 1
    )[1].split("// ============================================================", 1)[0]

    assert "if (!heartbeatReceived)" in watchdog_helper
    assert "return false;" in watchdog_helper
