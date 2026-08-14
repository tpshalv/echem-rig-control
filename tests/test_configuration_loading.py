from pathlib import Path

import pytest

from rig_control.configuration import load_keithley_configuration


VALID_CONFIGURATION = """
[keithley_2260b]
device_id = "main_power_supply"

[keithley_2260b.connection]
host = "192.168.1.50"
port = 2268
timeout_seconds = 4.0

[keithley_2260b.limits]
maximum_voltage = 30.0
maximum_current = 108.0
maximum_power = 1080.0
"""


def write_configuration(
    temporary_path: Path,
    contents: str,
) -> Path:
    configuration_path = temporary_path / "rig-config.toml"
    configuration_path.write_text(contents, encoding="utf-8")
    return configuration_path


def test_valid_configuration_file_is_loaded(
    tmp_path: Path,
) -> None:
    path = write_configuration(tmp_path, VALID_CONFIGURATION)

    configuration = load_keithley_configuration(path)

    assert configuration.device_id == "main_power_supply"
    assert configuration.connection.host == "192.168.1.50"
    assert configuration.connection.port == 2268
    assert configuration.connection.timeout_seconds == 4.0
    assert configuration.limits.maximum_voltage == 30.0
    assert configuration.limits.maximum_current == 108.0
    assert configuration.limits.maximum_power == 1080.0


def test_timeout_uses_default_when_omitted(
    tmp_path: Path,
) -> None:
    contents = VALID_CONFIGURATION.replace(
        "timeout_seconds = 4.0\n",
        "",
    )
    path = write_configuration(tmp_path, contents)

    configuration = load_keithley_configuration(path)

    assert configuration.connection.timeout_seconds == 5.0


def test_missing_file_has_informative_error(
    tmp_path: Path,
) -> None:
    missing_path = tmp_path / "does-not-exist.toml"

    with pytest.raises(
        FileNotFoundError,
        match="Rig configuration file was not found",
    ):
        load_keithley_configuration(missing_path)


def test_invalid_toml_has_informative_error(
    tmp_path: Path,
) -> None:
    path = write_configuration(
        tmp_path,
        "[keithley_2260b\ninvalid",
    )

    with pytest.raises(
        ValueError,
        match="contains invalid TOML",
    ):
        load_keithley_configuration(path)


def test_missing_keithley_section_is_rejected(
    tmp_path: Path,
) -> None:
    path = write_configuration(
        tmp_path,
        '[unrelated]\nvalue = "example"\n',
    )

    with pytest.raises(
        ValueError,
        match="keithley_2260b",
    ):
        load_keithley_configuration(path)


def test_missing_required_setting_names_exact_setting(
    tmp_path: Path,
) -> None:
    contents = VALID_CONFIGURATION.replace(
        'host = "192.168.1.50"\n',
        "",
    )
    path = write_configuration(tmp_path, contents)

    with pytest.raises(
        ValueError,
        match=r"keithley_2260b\.connection\.host",
    ):
        load_keithley_configuration(path)


def test_incorrect_port_type_names_exact_setting(
    tmp_path: Path,
) -> None:
    contents = VALID_CONFIGURATION.replace(
        "port = 2268",
        'port = "2268"',
    )
    path = write_configuration(tmp_path, contents)

    with pytest.raises(
        TypeError,
        match=r"keithley_2260b\.connection\.port",
    ):
        load_keithley_configuration(path)


def test_incorrect_limit_type_names_exact_setting(
    tmp_path: Path,
) -> None:
    contents = VALID_CONFIGURATION.replace(
        "maximum_voltage = 30.0",
        'maximum_voltage = "30.0"',
    )
    path = write_configuration(tmp_path, contents)

    with pytest.raises(
        TypeError,
        match=r"keithley_2260b\.limits\.maximum_voltage",
    ):
        load_keithley_configuration(path)


@pytest.mark.parametrize(
    ("setting", "invalid_value", "message"),
    [
        ("maximum_voltage = 30.0", "maximum_voltage = 0.0", "voltage"),
        ("maximum_current = 108.0", "maximum_current = -1.0", "current"),
        ("maximum_power = 1080.0", "maximum_power = 0.0", "power"),
    ],
)
def test_non_positive_limits_are_rejected(
    tmp_path: Path,
    setting: str,
    invalid_value: str,
    message: str,
) -> None:
    contents = VALID_CONFIGURATION.replace(
        setting,
        invalid_value,
    )
    path = write_configuration(tmp_path, contents)

    with pytest.raises(ValueError, match=message):
        load_keithley_configuration(path)