import json
from contextlib import nullcontext
from unittest.mock import Mock

from rig_control.app_settings import default_app_settings
from rig_control.data.run_context import capture_run_context
from rig_control.devices.power_supply import PowerSupply
from rig_control.models import DeviceStatus
from rig_control.rig_profile_loading import load_rig_profile


def test_context_freezes_configuration_and_cached_state_without_querying_hardware():
    profile = load_rig_profile("rig-profile.example.toml")
    settings = default_app_settings()
    device = Mock(spec=PowerSupply)
    device.status = DeviceStatus.DISCONNECTED
    device.identity = None
    device.voltage_setpoint = 2.5
    device.current_limit = 1.0
    device.output_enabled = False
    manager = Mock()
    manager.device_ids = ("main_power_supply",)
    manager.operation.return_value = nullcontext(device)

    captured = capture_run_context(profile, settings, manager)
    device.voltage_setpoint = 9.0
    snapshot = json.loads(captured["configuration_snapshot_json"])

    assert snapshot["rig_profile"]["profile"]["profile_id"] == profile.profile_id
    devices = snapshot["rig_profile"]["devices"]
    assert {item["device_id"] for item in devices} == {role.device_id for role in profile.device_roles}
    assert all("backend" in item and "driver" in item for item in devices)
    assert snapshot["application_settings"]["values"] == dict(settings.values)
    assert snapshot["cached_device_state"][0]["voltage_setpoint"] == 2.5
    assert snapshot["cached_device_state"][0]["status"] == "disconnected"
    assert snapshot["software_version"]
    assert device.mock_calls == []
