"""Capture configured hardware and cached runtime state without hardware I/O."""

from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from datetime import UTC, datetime
from enum import Enum
from importlib.metadata import PackageNotFoundError, version
import json
import platform
import tomllib

from rig_control.app_settings import AppSettings
from rig_control.devices.manager import DeviceManager
from rig_control.devices.mass_flow_controller import MassFlowController
from rig_control.devices.power_supply import PowerSupply
from rig_control.rig_profile import RigProfile
from rig_control.rig_profile_writing import serialize_rig_profile


def portable_value(value: object) -> object:
    """Convert cached identities and configuration into JSON-compatible values."""
    if isinstance(value, Enum):
        return value.value
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if is_dataclass(value) and not isinstance(value, type):
        return {item.name: portable_value(getattr(value, item.name)) for item in fields(value)}
    if isinstance(value, Mapping):
        return {str(key): portable_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [portable_value(item) for item in value]
    return str(value)


def capture_run_context(
    profile: RigProfile, settings: AppSettings, manager: DeviceManager,
) -> dict[str, str]:
    """Freeze the active configuration, including explicit real/simulated backends.

    Reported identities and setpoints are cached values, not a fresh read of
    every instrument register. Preserve that distinction in the snapshot.
    """
    try:
        software_version = version("echem-rig-control")
    except PackageNotFoundError:
        software_version = "unknown (package metadata unavailable)"
    devices = []
    for device_id in manager.device_ids:
        with manager.operation(device_id) as device:
            state = {
                "device_id": device_id,
                "status": device.status.value,
                "reported_identity": portable_value(getattr(device, "identity", None)),
            }
            if isinstance(device, PowerSupply):
                state.update(voltage_setpoint=device.voltage_setpoint,
                             current_limit=device.current_limit,
                             output_enabled=device.output_enabled)
            if isinstance(device, MassFlowController):
                state["flow_setpoint"] = device.flow_setpoint
            if hasattr(device, "control_configuration"):
                state["alicat_configuration"] = portable_value(device.control_configuration)
                state["verification"] = device.verification_message
                state["control_ready"] = device.control_ready
            if hasattr(device, "pressure_policy"):
                state["pressure_policy"] = portable_value(device.pressure_policy)
                state["pressure_setpoint_pa"] = device.pressure_setpoint_pa
            devices.append(state)
    snapshot = {
        "format": "rig-control.run-context",
        "format_version": 1,
        "captured_at": datetime.now(UTC).isoformat(),
        "software_version": software_version,
        "python_version": platform.python_version(),
        "rig_profile": tomllib.loads(serialize_rig_profile(profile)),
        "application_settings": {
            "settings_id": settings.settings_id,
            "friendly_name": settings.friendly_name,
            "values": dict(settings.values),
        },
        "cached_device_state": devices,
        "state_source": "Cached at recording start; no instrument configuration queries issued.",
    }
    return {"configuration_snapshot_json": json.dumps(snapshot, ensure_ascii=False)}
