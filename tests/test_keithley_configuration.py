import pytest

from rig_control.devices.keithley_2260b.configuration import (
    Keithley2260BConfiguration,
    SocketScpiConfiguration,
)

from rig_control.devices.power_supply import PowerSupplyLimits


def make_connection() -> SocketScpiConfiguration:
    return SocketScpiConfiguration(
        host="192.168.1.50",
        port=2268,
        timeout_seconds=5.0,
    )


def make_limits() -> PowerSupplyLimits:
    return PowerSupplyLimits(
        maximum_voltage=30.0,
        maximum_current=108.0,
        maximum_power=1080.0,
    )


def test_socket_configuration_stores_values() -> None:
    configuration = make_connection()

    assert configuration.host == "192.168.1.50"
    assert configuration.port == 2268
    assert configuration.timeout_seconds == 5.0


def test_socket_configuration_has_default_timeout() -> None:
    configuration = SocketScpiConfiguration(
        host="192.168.1.50",
        port=2268,
    )

    assert configuration.timeout_seconds == 5.0


@pytest.mark.parametrize("host", ["", "   "])
def test_empty_host_is_rejected(host: str) -> None:
    with pytest.raises(ValueError, match="cannot be empty"):
        SocketScpiConfiguration(
            host=host,
            port=2268,
        )


@pytest.mark.parametrize("port", [0, -1, 65536])
def test_invalid_port_is_rejected(port: int) -> None:
    with pytest.raises(ValueError, match="between 1 and 65535"):
        SocketScpiConfiguration(
            host="192.168.1.50",
            port=port,
        )


@pytest.mark.parametrize("port", [2268.0, "2268", True])
def test_non_integer_port_is_rejected(
    port: object,
) -> None:
    with pytest.raises(TypeError, match="must be an integer"):
        SocketScpiConfiguration(
            host="192.168.1.50",
            port=port,  # type: ignore[arg-type]
        )


@pytest.mark.parametrize("timeout", [0.0, -1.0])
def test_invalid_timeout_is_rejected(timeout: float) -> None:
    with pytest.raises(ValueError, match="greater than zero"):
        SocketScpiConfiguration(
            host="192.168.1.50",
            port=2268,
            timeout_seconds=timeout,
        )


@pytest.mark.parametrize("timeout", ["5", True])
def test_non_numeric_timeout_is_rejected(
    timeout: object,
) -> None:
    with pytest.raises(TypeError, match="int or float"):
        SocketScpiConfiguration(
            host="192.168.1.50",
            port=2268,
            timeout_seconds=timeout,  # type: ignore[arg-type]
        )


def test_keithley_configuration_combines_required_settings() -> None:
    configuration = Keithley2260BConfiguration(
        device_id="main_power_supply",
        connection=make_connection(),
        limits=make_limits(),
    )

    assert configuration.device_id == "main_power_supply"
    assert configuration.connection.port == 2268
    assert configuration.limits.maximum_voltage == 30.0


@pytest.mark.parametrize("device_id", ["", "   "])
def test_empty_device_id_is_rejected(device_id: str) -> None:
    with pytest.raises(ValueError, match="cannot be empty"):
        Keithley2260BConfiguration(
            device_id=device_id,
            connection=make_connection(),
            limits=make_limits(),
        )