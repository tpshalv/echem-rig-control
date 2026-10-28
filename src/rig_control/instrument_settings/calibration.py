"""Per-device calibration history: fit a candidate, review it, then accept or decline.

Each device's calibrations are kept in one append-only history file, so past
and current calibrations can be plotted together to see drift over time.
Accepting a calibration appends it and makes it active; declining writes
nothing, so a bad attempt leaves no trace in that history.

Two kinds exist: pump speed to flow, and EZO-HUM reported RH to a CO2-ambient
RH estimate. They share everything here except their file format name and
what their points are called on disk. Device IDs are unique within a rig, so
both kinds live side by side as ``<device_id>.json`` in one directory.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from uuid import uuid4
import json
import re

from rig_control.atomic_replace import replace_atomically
from rig_control.app_paths import calibrations_directory
from rig_control.devices.atlas_ezo_hum.driver import AtlasEzoHum
from rig_control.devices.linear_calibration import (
    Calibration,
    CalibrationPoint,
    LinearFit,
    fit_linear,
)
from rig_control.devices.manager import DeviceManager
from rig_control.devices.pump import Pump
from rig_control.instrument_settings.common import InstrumentSettingResult
from rig_control.models import Event, EventSink
from rig_control.rig_profile import RigProfile


# Version 1 identified each calibration by an automatic timestamp
# ("calibration_id"); version 2 by an operator-chosen "name". Version 1
# files are still read, and their timestamps become the names.
FORMAT_VERSION = 2
READABLE_FORMAT_VERSIONS = {1, 2}


@dataclass(frozen=True, slots=True)
class CalibrationKind:
    """One family of calibrations: its file format and point field names."""

    label: str
    format: str
    x_key: str
    y_key: str


PUMP_FLOW = CalibrationKind(
    "pump", "rig-control.pump-calibration-history", "speed_rpm", "flow_ml_per_min",
)
HUMIDITY_CO2 = CalibrationKind(
    "humidity", "rig-control.humidity-calibration-history", "reported_rh", "reference_rh",
)


@dataclass(frozen=True, slots=True)
class CalibrationCandidate:
    """A fitted-but-not-yet-accepted calibration, shown for review."""

    device_id: str
    name: str
    operator: str
    points: tuple[CalibrationPoint, ...]
    fit: LinearFit
    notes: str = ""


class CalibrationService:
    def __init__(self, kind: CalibrationKind, *, directory: Path | None = None) -> None:
        self.kind = kind
        self._directory = directory or calibrations_directory()
        # Serializes the read-modify-write in accept() against other callers
        # in this process. It does not protect against a second process (or
        # machine, e.g. a shared network path) writing the same device's
        # history file at the same time; calibrations are rare and manual
        # enough that this has not been worth solving with file locking.
        self._lock = RLock()
        # Polling applies the active calibration to every reading, so the
        # parsed history is kept until the file's size or timestamp changes.
        self._cache: dict[Path, tuple[tuple[int, int], tuple[Calibration, ...]]] = {}

    def fit_candidate(
        self,
        device_id: str,
        name: str,
        operator: str,
        points: list[CalibrationPoint],
        notes: str = "",
    ) -> CalibrationCandidate:
        """Fit the entered points for review; raises before anything is saved."""

        if not isinstance(device_id, str) or not device_id.strip():
            raise ValueError("Device ID cannot be empty")
        if not isinstance(operator, str) or not operator.strip():
            raise ValueError("Operator cannot be empty")
        name = name.strip() if isinstance(name, str) else name
        self._require_unused_name(device_id, name, self.history(device_id))
        fit = fit_linear(points)
        return CalibrationCandidate(device_id, name, operator, tuple(points), fit, notes)

    def accept(self, candidate: CalibrationCandidate) -> InstrumentSettingResult:
        """Append the candidate to history, making it the active calibration."""

        try:
            with self._lock:
                existing = list(self.history(candidate.device_id))
                name = candidate.name.strip()
                self._require_unused_name(candidate.device_id, name, existing)
                calibration = Calibration(
                    name=name,
                    device_id=candidate.device_id,
                    operator=candidate.operator,
                    points=candidate.points,
                    fit=candidate.fit,
                    notes=candidate.notes,
                )
                self._write(candidate.device_id, [*existing, calibration])
        except Exception as error:
            return InstrumentSettingResult(
                False, f"Could not save {self.kind.label} calibration: {error}"
            )
        return InstrumentSettingResult(
            True,
            f"Saved calibration {calibration.name!r} for {candidate.device_id!r}; "
            "now the active calibration.",
        )

    def history(self, device_id: str) -> tuple[Calibration, ...]:
        path = self._path(device_id)
        try:
            stat = path.stat()
        except FileNotFoundError:
            return ()
        signature = (stat.st_mtime_ns, stat.st_size)
        cached = self._cache.get(path)
        if cached is not None and cached[0] == signature:
            return cached[1]
        payload = json.loads(path.read_text(encoding="utf-8"))
        if (
            payload.get("format") != self.kind.format
            or payload.get("format_version") not in READABLE_FORMAT_VERSIONS
        ):
            raise ValueError(
                f"{path} is not a recognised {self.kind.label} calibration history file"
            )
        history = tuple(self._from_dict(entry) for entry in payload["calibrations"])
        self._cache[path] = (signature, history)
        return history

    def active_calibration(self, device_id: str) -> Calibration | None:
        """Return the most recently accepted calibration, if any."""

        history = self.history(device_id)
        return history[-1] if history else None

    @staticmethod
    def _require_unused_name(
        device_id: str, name: object, existing: tuple[Calibration, ...] | list[Calibration],
    ) -> None:
        if not isinstance(name, str) or not name:
            raise ValueError("Calibration name cannot be empty")
        folded = name.casefold()
        if any(calibration.name.casefold() == folded for calibration in existing):
            raise ValueError(
                f"{device_id!r} already has a calibration named {name!r}; choose another name"
            )

    def _write(self, device_id: str, calibrations: list[Calibration]) -> None:
        path = self._path(device_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "format": self.kind.format,
            "format_version": FORMAT_VERSION,
            "device_id": device_id,
            "calibrations": [self._to_dict(calibration) for calibration in calibrations],
        }
        text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
        # Write to a temporary file in the same directory, then rename over
        # the real one. The rename is atomic, so a crash or power loss mid
        # write leaves either the old, complete file or the new, complete
        # one - never a truncated file that would lose this device's entire
        # calibration history on the next read.
        temporary_path = path.with_name(f"{path.name}.tmp-{uuid4().hex}")
        try:
            temporary_path.write_text(text, encoding="utf-8")
            replace_atomically(temporary_path, path)
        finally:
            temporary_path.unlink(missing_ok=True)
            self._cache.pop(path, None)

    def _path(self, device_id: str) -> Path:
        safe = re.sub(r"[^A-Za-z0-9_-]+", "_", device_id).strip("_-") or "device"
        return self._directory / f"{safe}.json"

    def _to_dict(self, calibration: Calibration) -> dict:
        return {
            "name": calibration.name,
            "device_id": calibration.device_id,
            "operator": calibration.operator,
            "created_utc": calibration.created_utc.isoformat(),
            "notes": calibration.notes,
            "points": [
                {self.kind.x_key: point.x, self.kind.y_key: point.y}
                for point in calibration.points
            ],
            "fit": {
                "slope": calibration.fit.slope,
                "intercept": calibration.fit.intercept,
                "r_squared": calibration.fit.r_squared,
            },
        }

    def _from_dict(self, entry: dict) -> Calibration:
        return Calibration(
            name=entry["name"] if "name" in entry else entry["calibration_id"],
            device_id=entry["device_id"],
            operator=entry["operator"],
            created_utc=datetime.fromisoformat(entry["created_utc"]),
            notes=entry.get("notes", ""),
            points=tuple(
                CalibrationPoint(point[self.kind.x_key], point[self.kind.y_key])
                for point in entry["points"]
            ),
            fit=LinearFit(**entry["fit"]),
        )


class PumpCalibrationService(CalibrationService):
    """Pump speed (rpm) to flow (mL/min)."""

    def __init__(self, *, directory: Path | None = None) -> None:
        super().__init__(PUMP_FLOW, directory=directory)


class HumidityCalibrationService(CalibrationService):
    """EZO-HUM reported RH to the reference RH measured in a CO2 ambient."""

    def __init__(self, *, directory: Path | None = None) -> None:
        super().__init__(HUMIDITY_CO2, directory=directory)


def today_calibration_name() -> str:
    """Default name offered for a new calibration: today's local date."""

    return datetime.now().strftime("%Y-%m-%d")


class CalibrationSettingsService:
    """Device-aware front for a CalibrationService, for the Settings window.

    Lists configured devices of one type and gates accept() behind write
    access and an audit log, the same way Re72SettingsService gates live
    register writes - even though accepting a calibration only writes a local
    file, not the instrument, recalibrating mid-experiment would be confusing
    enough that it deserves the same "not while a run is active" guard.
    """

    def __init__(
        self,
        manager: DeviceManager,
        profile: RigProfile,
        calibrations: CalibrationService,
        *,
        device_type: type,
        require_write_access: Callable[[], None],
        event_sink: EventSink | None = None,
    ) -> None:
        self._manager = manager
        self._profile = profile
        self._calibrations = calibrations
        self._device_type = device_type
        self._require_write_access = require_write_access
        self._event_sink = event_sink

    @property
    def kind(self) -> CalibrationKind:
        return self._calibrations.kind

    def device_ids(self) -> tuple[str, ...]:
        manager = self._manager
        return tuple(
            device_id for device_id in manager.device_ids
            if isinstance(manager.get(device_id), self._device_type)
        )

    def history(self, device_id: str) -> tuple[Calibration, ...]:
        return self._calibrations.history(device_id)

    def active_calibration(self, device_id: str) -> Calibration | None:
        return self._calibrations.active_calibration(device_id)

    def fit_candidate(
        self, device_id: str, name: str, operator: str, points: list[CalibrationPoint],
        notes: str = "",
    ) -> CalibrationCandidate:
        return self._calibrations.fit_candidate(device_id, name, operator, points, notes)

    def accept(self, candidate: CalibrationCandidate) -> InstrumentSettingResult:
        try:
            self._require_write_access()
        except Exception as error:
            return InstrumentSettingResult(
                False, f"Could not save {self.kind.label} calibration: {error}"
            )
        result = self._calibrations.accept(candidate)
        if result.succeeded:
            warning = self._audit(candidate)
            if warning:
                result = InstrumentSettingResult(True, result.summary + warning)
        return result

    def _audit(self, candidate: CalibrationCandidate) -> str:
        if self._event_sink is None:
            return ""
        try:
            self._event_sink(Event(source=candidate.device_id, message=json.dumps({
                "kind": "instrument_configuration",
                "operation": f"accept_{self.kind.label}_calibration",
                "parameters": {
                    "name": candidate.name,
                    "operator": candidate.operator,
                    "slope": candidate.fit.slope,
                    "intercept": candidate.fit.intercept,
                    "r_squared": candidate.fit.r_squared,
                    "point_count": len(candidate.points),
                },
                "outcome": "succeeded",
            })), None)
        except Exception as error:
            return f" Warning: the calibration was saved, but could not be logged: {error}."
        return ""


class PumpCalibrationSettingsService(CalibrationSettingsService):
    def __init__(
        self,
        manager: DeviceManager,
        profile: RigProfile,
        calibrations: PumpCalibrationService | None = None,
        *,
        require_write_access: Callable[[], None],
        event_sink: EventSink | None = None,
    ) -> None:
        super().__init__(
            manager, profile, calibrations or PumpCalibrationService(),
            device_type=Pump, require_write_access=require_write_access,
            event_sink=event_sink,
        )


class HumidityCalibrationSettingsService(CalibrationSettingsService):
    def __init__(
        self,
        manager: DeviceManager,
        profile: RigProfile,
        calibrations: HumidityCalibrationService | None = None,
        *,
        require_write_access: Callable[[], None],
        event_sink: EventSink | None = None,
    ) -> None:
        super().__init__(
            manager, profile, calibrations or HumidityCalibrationService(),
            device_type=AtlasEzoHum, require_write_access=require_write_access,
            event_sink=event_sink,
        )
