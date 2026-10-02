from rig_control.ui.device_setup.types import SCPI_POWER_SUPPLY_DRIVERS


def test_keithley_2280s_defaults_to_older_raw_socket_port_with_fallback_help() -> None:
    metadata = SCPI_POWER_SUPPLY_DRIVERS["keithley_2280s"]

    assert metadata["default_port"] == 5050
    assert "5025" in metadata["ethernet_port_hint"]
