"""Detailed RE72 configuration, separate from application preferences and UI."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import re

from rig_control.devices.lumel_re72 import LumelRe72, RE72_SETTINGS, Re72Setting
from rig_control.devices.manager import DeviceManager
from rig_control.models import DeviceStatus, Event, EventSink
from rig_control.rig_profile import RigProfile
from rig_control.instrument_settings.common import InstrumentSettingResult


@dataclass(frozen=True, slots=True)
class Re72SettingRow:
    definition: Re72Setting
    value: str


class Re72SettingsService:
    def __init__(
        self, manager: DeviceManager, profile: RigProfile, *,
        require_write_access: Callable[[], None],
        event_sink: EventSink | None = None,
    ) -> None:
        self._manager = manager
        self._profile = profile
        self._require_write_access = require_write_access
        self._event_sink = event_sink

    def _audit(self, device_id: str, operation: str, values: dict) -> str:
        if self._event_sink is None:
            return ""
        try:
            self._event_sink(Event(source=device_id, message=json.dumps({
                "kind": "instrument_configuration",
                "operation": operation,
                "parameters": values,
                "outcome": "succeeded",
            })), None)
        except Exception as error:
            # The hardware action has already happened; do not suggest retrying it.
            return f" Warning: the configuration change could not be logged: {error}."
        return ""

    def device_ids(self) -> tuple[str, ...]:
        manager = self._manager
        return tuple(
            device_id for device_id in manager.device_ids
            if isinstance(manager.get(device_id), LumelRe72)
        )

    def empty_setting_rows(self) -> tuple[Re72SettingRow, ...]:
        """Rows used to build the editor before hardware has been read."""
        return tuple(
            Re72SettingRow(item, "") for item in RE72_SETTINGS if item.visible
        )

    def read_settings(self, device_id: str) -> tuple[Re72SettingRow, ...]:
        manager = self._manager
        device = manager.get(device_id)
        if not isinstance(device, LumelRe72):
            raise TypeError(f"Device {device_id!r} is not an RE72")
        if device.status is DeviceStatus.DISCONNECTED:
            manager.connect(device_id)
        with manager.operation(device_id):
            values = device.read_settings()
        return tuple(
            Re72SettingRow(item, self._format_value(item, values[item.key]))
            for item in RE72_SETTINGS
            if item.visible
        )

    def read_runtime_state(self, device_id: str) -> dict[str, object]:
        manager = self._manager
        device = manager.get(device_id)
        if not isinstance(device, LumelRe72):
            raise TypeError(f"Device {device_id!r} is not an RE72")
        with manager.operation(device_id):
            return device.read_runtime_state()

    def start_autotune(self, device_id: str) -> InstrumentSettingResult:
        manager = self._manager
        try:
            self._require_write_access()
            device = manager.get(device_id)
            if not isinstance(device, LumelRe72):
                raise TypeError(f"Device {device_id!r} is not an RE72")
            if device.status is DeviceStatus.DISCONNECTED:
                manager.connect(device_id)
            with manager.operation(device_id):
                device.start_autotune()
                warning = self._audit(device_id, "start_autotune", {})
        except Exception as error:
            return InstrumentSettingResult(False, f"Could not start autotune: {error}")
        return InstrumentSettingResult(
            True,
            "Autotune command accepted; monitoring controller status." + warning,
        )

    def save_snapshot(self, device_id: str, path: str | Path) -> InstrumentSettingResult:
        manager = self._manager
        try:
            device = manager.get(device_id)
            if not isinstance(device, LumelRe72):
                raise TypeError(f"Device {device_id!r} is not an RE72")
            if device.status is DeviceStatus.DISCONNECTED:
                manager.connect(device_id)
            with manager.operation(device_id):
                registers = device.read_full_snapshot()
                decoded = device.read_settings()
            definitions = {item.key: item for item in RE72_SETTINGS}
            friendly_name = self._profile.get_role(device_id).friendly_name
            snapshot = {
                "format": "rig-control.re72-snapshot",
                "format_version": 1,
                "created_utc": datetime.now(timezone.utc).isoformat(),
                "source": {
                    "device_id": device_id,
                    "friendly_name": friendly_name,
                    "slave_address": device.slave,
                    "order_code_family": "RE72-122100",
                    "register_range": [min(registers), max(registers)],
                },
                "decoded_settings": {
                    key: {
                        "label": definitions[key].label,
                        "register": definitions[key].register,
                        "value": self._format_value(definitions[key], value),
                        "raw_value": registers[definitions[key].register],
                        "writable": definitions[key].writable,
                    }
                    for key, value in decoded.items()
                },
                "registers": {str(address): value for address, value in registers.items()},
            }
            destination = Path(path)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(
                json.dumps(snapshot, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        except Exception as error:
            return InstrumentSettingResult(False, f"Could not save RE72 snapshot: {error}")
        return InstrumentSettingResult(
            True,
            f"Saved {len(registers)} RE72 registers to {destination}.",
        )

    def snapshot_default_stem(self, device_id: str) -> str:
        """Return a filesystem-safe controller/address and friendly-name prefix."""
        friendly_name = self._profile.get_role(device_id).friendly_name.strip()
        safe_name = re.sub(r"[^A-Za-z0-9_-]+", "_", friendly_name).strip("_-")
        return f"{device_id}_{safe_name}" if safe_name else device_id

    def restore_snapshot(
        self, device_id: str, path: str | Path
    ) -> InstrumentSettingResult:
        manager = self._manager
        try:
            self._require_write_access()
            snapshot = json.loads(Path(path).read_text(encoding="utf-8"))
            if snapshot.get("format") != "rig-control.re72-snapshot":
                raise ValueError("File is not an RE72 settings snapshot")
            if snapshot.get("format_version") != 1:
                raise ValueError("Unsupported RE72 snapshot version")
            raw_registers = snapshot.get("registers")
            if not isinstance(raw_registers, dict):
                raise ValueError("Snapshot has no register data")
            registers = {
                int(address): int(value) for address, value in raw_registers.items()
            }
            if any(not 0 <= value <= 65535 for value in registers.values()):
                raise ValueError("Snapshot contains an invalid register value")
            device = manager.get(device_id)
            if not isinstance(device, LumelRe72):
                raise TypeError(f"Device {device_id!r} is not an RE72")
            if device.status is DeviceStatus.DISCONNECTED:
                manager.connect(device_id)
            with manager.operation(device_id):
                changed = device.restore_snapshot(registers)
                warning = self._audit(device_id, "restore_snapshot", {
                    "path": str(path),
                    "changed_registers": {str(address): registers[address] for address in changed},
                })
        except Exception as error:
            return InstrumentSettingResult(False, f"Could not restore RE72 snapshot: {error}")
        return InstrumentSettingResult(
            True,
            f"Restored and verified {len(changed)} changed RE72 registers." + warning,
        )

    def write_setting(self, device_id: str, key: str, value: str) -> InstrumentSettingResult:
        manager = self._manager
        try:
            self._require_write_access()
            device = manager.get(device_id)
            if not isinstance(device, LumelRe72):
                raise TypeError(f"Device {device_id!r} is not an RE72")
            if device.status is DeviceStatus.DISCONNECTED:
                manager.connect(device_id)
            with manager.operation(device_id):
                device.write_setting(key, value)
                warning = self._audit(device_id, "write_setting", {key: value})
        except Exception as error:
            return InstrumentSettingResult(False, f"Could not update RE72: {error}")
        return InstrumentSettingResult(True, f"Updated {key} on {device_id}." + warning)

    def write_settings(
        self,
        device_id: str,
        values: Mapping[str, str],
    ) -> InstrumentSettingResult:
        """Write a group of edited settings, then verify every value by reading back."""
        manager = self._manager
        warnings: list[str] = []
        try:
            self._require_write_access()
            device = manager.get(device_id)
            if not isinstance(device, LumelRe72):
                raise TypeError(f"Device {device_id!r} is not an RE72")
            if device.status is DeviceStatus.DISCONNECTED:
                manager.connect(device_id)
            with manager.operation(device_id):
                for key, value in values.items():
                    try:
                        device.write_setting(key, value)
                        warning = self._audit(device_id, "write_setting", {key: value})
                        if warning:
                            warnings.append(warning)
                    except Exception as error:
                        raise RuntimeError(f"{key}: {error}") from error
                actual = device.read_settings()
            definitions = {item.key: item for item in RE72_SETTINGS}
            mismatches = []
            for key, requested in values.items():
                displayed = self._format_value(definitions[key], actual[key])
                if not self._values_match(requested, displayed):
                    mismatches.append(
                        f"{definitions[key].label}: requested {requested!r}, "
                        f"controller returned {displayed!r}"
                    )
            if mismatches:
                return InstrumentSettingResult(
                    False,
                    "RE72 did not retain the requested value(s): "
                    + "; ".join(mismatches) + "".join(warnings),
                )
        except Exception as error:
            return InstrumentSettingResult(False, f"Could not update RE72: {error}" + "".join(warnings))
        verified = ", ".join(
            f"{definitions[key].label} = "
            f"{self._format_value(definitions[key], actual[key])}"
            for key in values
        )
        return InstrumentSettingResult(True, f"Applied and read back: {verified}." + "".join(warnings))

    @staticmethod
    def _format_value(setting: Re72Setting, value: float | int) -> str:
        labels = dict(setting.choices)
        return labels.get(value, str(value))

    @staticmethod
    def _values_match(requested: str, displayed: str) -> bool:
        if requested == displayed:
            return True
        try:
            return abs(float(requested) - float(displayed)) < 1e-9
        except ValueError:
            return False
