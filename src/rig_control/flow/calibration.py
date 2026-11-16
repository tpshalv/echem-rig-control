"""Leak-check results on disk: one folder per check, no hardware or UI here."""
from datetime import datetime
from hashlib import sha256
import json
from math import isfinite, sqrt
from pathlib import Path
from statistics import mean
from time import monotonic
from uuid import uuid4

from rig_control.app_paths import calibrations_directory, settings_directory
from rig_control.atomic_replace import replace_atomically

CALIBRATION_FILE = "calibration.json"
TRACE_FILE = "trace.csv"
REFRESH_SECONDS = 10.


def fit_line(points):
    """Least-squares ``outlet = slope * inlet + intercept``, or None if undefined."""
    x = [p["inlet_sccm"] for p in points]
    y = [p["outlet_sccm"] for p in points]
    if len(set(x)) < 2:
        return None
    xm, ym = mean(x), mean(y)
    slope = sum((a-xm)*(b-ym) for a, b in zip(x, y))/sum((a-xm)**2 for a in x)
    intercept = ym-slope*xm
    residuals = [b-slope*a-intercept for a, b in zip(x, y)]
    rmse = sqrt(sum(r*r for r in residuals)/(len(x)-2)) if len(x) > 2 else 0.
    return dict(slope=slope, intercept_sccm=intercept, rmse_sccm=rmse)


def validate(calibration):
    if calibration.get("type") != "leak_check" or calibration.get("version") != 1:
        raise ValueError("Not a leak-check calibration")
    if not calibration["inlet_ids"] or not calibration["outlet_id"]:
        raise ValueError("Calibration does not name its instruments")
    if datetime.fromisoformat(calibration["created_utc"]).tzinfo is None:
        raise ValueError("Calibration timestamp needs a timezone")
    for key in ("slope", "intercept_sccm", "rmse_sccm"):
        value = calibration[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
            raise ValueError(f"Calibration {key} must be a finite number")
    return calibration


def write_json(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name+f".{uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(payload, indent=2, allow_nan=False), encoding="utf-8")
        replace_atomically(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def preferences_path(rig_profile_id):
    return settings_directory()/("leak-check-"+sha256(rig_profile_id.encode()).hexdigest()[:16]+".json")


def load_preferences(path):
    """Remembered form values and current source; missing or damaged means none."""
    try:
        preferences = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return preferences if isinstance(preferences, dict) else {}


class LeakCheckStore:
    """``<directory>/<check>/`` holds trace.csv and, once saved, calibration.json."""

    def __init__(self, directory=None):
        self.directory = Path(directory) if directory else calibrations_directory()/"flow"
        self.errors = []
        self._calibrations = []
        self._refresh_after = 0.

    def new_folder(self, started_at):
        stem = started_at.astimezone().strftime("%Y-%m-%d_%Hh%M")
        self.directory.mkdir(parents=True, exist_ok=True)
        for suffix in ("", *(f"-{n}" for n in range(2, 1000))):
            folder = self.directory/(stem+suffix)
            try:
                folder.mkdir()
            except FileExistsError:
                continue
            return folder
        raise OSError(f"No free leak-check folder name for {stem}")

    def save(self, folder, calibration):
        write_json(Path(folder)/CALIBRATION_FILE, validate(calibration))
        self._refresh_after = 0.

    def latest(self, rig_profile_id):
        """Newest saved calibration for this rig, re-read at most every few seconds."""
        if monotonic() >= self._refresh_after:
            self._reload()
        return next((c for c in self._calibrations if c.get("rig_profile_id") == rig_profile_id), None)

    def _reload(self):
        found, errors = [], []
        for path in self.directory.glob(f"*/{CALIBRATION_FILE}"):
            try:
                found.append(validate(json.loads(path.read_text(encoding="utf-8"))))
            except (ValueError, KeyError, TypeError, OSError, AttributeError) as error:
                errors.append(f"{path.parent.name}: {error}")
        self._calibrations = sorted(found, key=lambda c: datetime.fromisoformat(c["created_utc"]), reverse=True)
        self.errors = errors
        self._refresh_after = monotonic()+REFRESH_SECONDS
