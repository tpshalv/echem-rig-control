"""Take and store a read-only settings dump from one connected device.

A device can be dumped when its driver knows how to report its own settings.
Drivers that do not are reported as unsupported rather than failing, so the
screen can say why the option is unavailable.
"""

from datetime import datetime
from pathlib import Path

from rig_control.app_paths import device_dumps_directory
from rig_control.devices.dump_report import DeviceDump, DumpQuery
from rig_control.models import DeviceStatus


#: Statuses in which the device owns an open connection to query over.
_READABLE = frozenset({DeviceStatus.READY, DeviceStatus.CONNECTED})


def dump_unavailable_reason(device: object) -> str:
    """Return why this device cannot be dumped, or "" when it can.

    The text is shown beside a disabled button, so it says what would make
    the option available.
    """

    if not callable(getattr(device, "setup_report", None)):
        return (
            "No settings dump has been written for this instrument yet. "
            "Its driver would need a read-only query list."
        )
    status = getattr(device, "status", None)
    if status not in _READABLE:
        return "Connect the device first; a dump reads over its live connection."
    return ""


def can_dump(device: object) -> bool:
    return not dump_unavailable_reason(device)


def dump_device(device: object, device_type: str = "") -> DeviceDump:
    """Ask one device for its settings, without changing any of them."""

    reason = dump_unavailable_reason(device)
    if reason:
        raise ValueError(reason)
    queries = device.setup_report()
    if not isinstance(queries, tuple) or any(
        not isinstance(item, DumpQuery) for item in queries
    ):
        raise TypeError("A settings dump must be a tuple of DumpQuery values")
    return DeviceDump(
        device_id=device.device_id,
        device_type=device_type or type(device).__name__,
        queries=queries,
        taken_at=datetime.now(),
    )


def write_dump(dump: DeviceDump, directory: Path | None = None) -> Path:
    """Save one dump beside the others, named so it sorts by device and time."""

    if not isinstance(dump, DeviceDump):
        raise TypeError("write_dump needs a DeviceDump")
    target = Path(directory) if directory is not None else device_dumps_directory()
    target.mkdir(parents=True, exist_ok=True)
    safe_id = "".join(
        character if character.isalnum() or character in "-_" else "-"
        for character in dump.device_id
    )
    path = target / f"{safe_id}-{dump.taken_at:%Y%m%d-%H%M%S}.txt"
    path.write_text(dump.to_text(), encoding="utf-8")
    return path
