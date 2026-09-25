from dataclasses import replace

from rig_control.devices.atlas_ezo_hum.protocol import EzoHumIdentity
from rig_control.devices.measurement_source import DeviceMeasurement
from rig_control.models import Measurement
from rig_control.rig_profile import RigProfile
from rig_control.rig_profile_loading import load_rig_profile
from rig_control.ui.device_setup.model import DeviceSetupViewModel
from rig_control.ui.device_setup.types import AddEzoHumRequest
from rig_control.diagnostics.atlas_ezo_hum import EzoHumDiagnosticResult


def empty_profile():
    return replace(load_rig_profile("rig-profile.example.toml"), connections=(), device_roles=())


def diagnostic(configuration):
    assert configuration.port == "COM12"
    return EzoHumDiagnosticResult(
        EzoHumIdentity("1.01"),
        (
            DeviceMeasurement("humidity", Measurement(48.2, "%RH")),
            DeviceMeasurement("temperature", Measurement(22.4, "degC")),
        ),
    )


def test_checked_ezo_hum_is_saved_with_its_own_serial_connection(tmp_path):
    profile_path = tmp_path / "rig-profile.toml"
    model = DeviceSetupViewModel(
        empty_profile(), profile_path=profile_path, ezo_hum_checker=diagnostic
    )
    result = model.add_ezo_hum_and_check(
        AddEzoHumRequest(
            "inlet_humidity", "Inlet humidity", "Inlet gas", "COM12",
            include_dew_point=True,
        )
    )

    assert result.succeeded is True
    saved = load_rig_profile(profile_path)
    role = saved.get_role("inlet_humidity")
    connection = saved.get_connection(role.connection_id)
    assert role.driver == "atlas_ezo_hum"
    assert role.settings["include_dew_point"] is True
    assert connection.parameters["port"] == "COM12"
    assert connection.parameters["baud_rate"] == 9600


def test_duplicate_ezo_hum_port_is_rejected_before_hardware_check(tmp_path):
    calls = 0

    def checker(configuration):
        nonlocal calls
        calls += 1
        return diagnostic(configuration)

    model = DeviceSetupViewModel(
        empty_profile(), profile_path=tmp_path / "rig-profile.toml", ezo_hum_checker=checker
    )
    first = model.add_ezo_hum_and_check(
        AddEzoHumRequest("inlet_humidity", "Inlet humidity", "", "COM12")
    )
    second = model.add_ezo_hum_and_check(
        AddEzoHumRequest("outlet_humidity", "Outlet humidity", "", "COM12")
    )

    assert first.succeeded is True
    assert second.succeeded is False
    assert "already used" in second.technical_details
    assert calls == 1
