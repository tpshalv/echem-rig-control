"""Per-pump calibration history: fit a candidate, review it, then accept or decline.

Each pump's calibrations are kept in one append-only history file, so past
and current calibrations can be plotted together to see drift over time.
Accepting a calibration appends it and makes it active; declining writes
nothing, so a bad attempt leaves no trace in that history.
"""

from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from uuid import uuid4
import json
import re

from rig_control.app_paths import calibrations_directory
from rig_control.devices.manager import DeviceManager
from rig_control.devices.pump import Pump
from rig_control.devices.pump_calibration import (
    CalibrationPoint,
    LinearFit,
    PumpCalibration,
    fit_linear,
)
from rig_control.instrument_settings.common import InstrumentSettingResult
from rig_control.models import Event, EventSink
from rig_control.rig_profile import RigProfile


FORMAT = "rig-control.pump-calibration-history"
FORMAT_VERSION = 1


@dataclass(frozen=True, slots=True)
class CalibrationCandidate:
    """A fitted-but-not-yet-accepted calibration, shown for review."""

    device_id: str
    operator: str
    points: tuple[CalibrationPoint, ...]
    fit: LinearFit
    notes: str = ""


class PumpCalibrationService:
    def __init__(self, *, directory: Path | None = None) -> None:
        self._directory = directory or calibrations_directory()
        # Serializes the read-modify-write in accept() against other callers
        # in this process. It does not protect against a second process (or
        # machine, e.g. a shared network path) writing the same device's
        # history file at the same time; calibrations are rare and manual
        # enough that this has not been worth solving with file locking.
        self._lock = Lock()

    def fit_candidate(
        self,
        device_id: str,
        operator: str,
        points: list[CalibrationPoint],
        notes: str = "",
    ) -> CalibrationCandidate:
        """Fit the entered points for review; raises before anything is saved."""

        if not isinstance(device_id, str) or not device_id.strip():
            raise ValueError("Device ID cannot be empty")
        if not isinstance(operator, str) or not operator.strip():
            raise ValueError("Operator cannot be empty")
        fit = fit_linear(points)
        return CalibrationCandidate(device_id, operator, tuple(points), fit, notes)

    def accept(self, candidate: CalibrationCandidate) -> InstrumentSettingResult:
        """Append the candidate to history, making it the active calibration."""

        try:
            with self._lock:
                existing = list(self.history(candidate.device_id))
                calibration_id = self._unique_id(existing)
                calibration = PumpCalibration(
                    calibration_id=calibration_id,
                    device_id=candidate.device_id,
                    operator=candidate.operator,
                    points=candidate.points,
                    fit=candidate.fit,
                    notes=candidate.notes,
                )
                self._write(candidate.device_id, [*existing, calibration])
        except Exception as error:
            return InstrumentSettingResult(False, f"Could not save pump calibration: {error}")
        return InstrumentSettingResult(
            True,
            f"Saved calibration {calibration.calibration_id} for {candidate.device_id!r}; "
            "now the active calibration.",
        )

    def history(self, device_id: str) -> tuple[PumpCalibration, ...]:
        path = self._path(device_id)
        if not path.exists():
            return ()
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("format") != FORMAT or payload.get("format_version") != FORMAT_VERSION:
            raise ValueError(f"{path} is not a recognised pump calibration history file")
        return tuple(_calibration_from_dict(entry) for entry in payload["calibrations"])

    def active_calibration(self, device_id: str) -> PumpCalibration | None:
        """Return the most recently accepted calibration, if any."""

        history = self.history(device_id)
        return history[-1] if history else None

    def _write(self, device_id: str, calibrations: list[PumpCalibration]) -> None:
        path = self._path(device_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "format": FORMAT,
            "format_version": FORMAT_VERSION,
            "device_id": device_id,
            "calibrations": [_calibration_to_dict(calibration) for calibration in calibrations],
        }
        text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
        # Write to a temporary file in the same directory, then rename over
        # the real one. The rename is atomic, so a crash or power loss mid
        # write leaves either the old, complete file or the new, complete
        # one - never a truncated file that would lose this pump's entire
        # calibration history on the next read.
        temporary_path = path.with_name(f"{path.name}.tmp-{uuid4().hex}")
        try:
            temporary_path.write_text(text, encoding="utf-8")
            temporary_path.replace(path)
        finally:
            temporary_path.unlink(missing_ok=True)

    def _path(self, device_id: str) -> Path:
        safe = re.sub(r"[^A-Za-z0-9_-]+", "_", device_id).strip("_-") or "device"
        return self._directory / f"{safe}.json"

    @staticmethod
    def _unique_id(existing: list[PumpCalibration]) -> str:
        used = {calibration.calibration_id for calibration in existing}
        base = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        if base not in used:
            return base
        suffix = 2
        while f"{base}-{suffix}" in used:
            suffix += 1
        return f"{base}-{suffix}"


def _calibration_to_dict(calibration: PumpCalibration) -> dict:
    return {
        "calibration_id": calibration.calibration_id,
        "device_id": calibration.device_id,
        "operator": calibration.operator,
        "created_utc": calibration.created_utc.isoformat(),
        "notes": calibration.notes,
        "points": [asdict(point) for point in calibration.points],
        "fit": asdict(calibration.fit),
    }


def _calibration_from_dict(entry: dict) -> PumpCalibration:
    return PumpCalibration(
        calibration_id=entry["calibration_id"],
        device_id=entry["device_id"],
        operator=entry["operator"],
        created_utc=datetime.fromisoformat(entry["created_utc"]),
        notes=entry.get("notes", ""),
        points=tuple(CalibrationPoint(**point) for point in entry["points"]),
        fit=LinearFit(**entry["fit"]),
    )


class PumpCalibrationSettingsService:
    """Pump-aware front for PumpCalibrationService, for the Settings window.

    Lists configured pump devices and gates accept() behind write access
    and an audit log, the same way Re72SettingsService gates live register
    writes - even though accepting a calibration only writes a local file,
    not the instrument, recalibrating mid-experiment would be confusing
    enough that it deserves the same "not while a run is active" guard.
    """

    def __init__(
        self,
        manager: DeviceManager,
        profile: RigProfile,
        calibrations: PumpCalibrationService | None = None,
        *,
        require_write_access: Callable[[], None],
        event_sink: EventSink | None = None,
    ) -> None:
        self._manager = manager
        self._profile = profile
        self._calibrations = calibrations or PumpCalibrationService()
        self._require_write_access = require_write_access
        self._event_sink = event_sink

    def device_ids(self) -> tuple[str, ...]:
        manager = self._manager
        return tuple(
            device_id for device_id in manager.device_ids
            if isinstance(manager.get(device_id), Pump)
        )

    def history(self, device_id: str) -> tuple[PumpCalibration, ...]:
        return self._calibrations.history(device_id)

    def active_calibration(self, device_id: str) -> PumpCalibration | None:
        return self._calibrations.active_calibration(device_id)

    def fit_candidate(
        self, device_id: str, operator: str, points: list[CalibrationPoint], notes: str = "",
    ) -> CalibrationCandidate:
        return self._calibrations.fit_candidate(device_id, operator, points, notes)

    def accept(self, candidate: CalibrationCandidate) -> InstrumentSettingResult:
        try:
            self._require_write_access()
        except Exception as error:
            return InstrumentSettingResult(False, f"Could not save pump calibration: {error}")
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
                "operation": "accept_pump_calibration",
                "parameters": {
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
