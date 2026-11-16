"""The active area of the cell on the rig, for working in current density.

Density is only a way of stating a current. The supply is always commanded in
amperes and the raw current is always recorded; a value in mA/cm² becomes
amperes here, using the area entered for this cell. There is no default area:
a wrong one would silently scale every current, so without one a density is
refused rather than guessed.
"""

from collections.abc import Callable
from math import isfinite


#: Shown on screens and in recipes. Recordings use the ASCII unit, like degC.
DENSITY_UNIT = "mA/cm²"
RECORDED_DENSITY_UNIT = "mA/cm2"
AREA_METADATA_KEY = "active_area_cm2"
_MA_PER_A = 1000.0


class CellAreaMissing(ValueError):
    pass


def parse_area(text: str) -> float | None:
    """Read a typed area in cm²; blank text means none has been entered."""
    if not text.strip():
        return None
    try:
        area = float(text)
    except ValueError:
        raise ValueError("Active area must be a number") from None
    _check(area)
    return area


def _check(area: float) -> None:
    if not isfinite(area) or area <= 0:
        raise ValueError("Active area must be greater than zero")


class CellArea:
    """The area in use; fixed while recording, so a run has only one."""

    def __init__(self, is_locked: Callable[[], bool] = lambda: False) -> None:
        self._is_locked = is_locked
        self._cm2: float | None = None

    @property
    def cm2(self) -> float | None:
        return self._cm2

    @property
    def is_locked(self) -> bool:
        return self._is_locked()

    def set(self, cm2: float | None) -> None:
        if cm2 is not None:
            _check(cm2)
        if cm2 != self._cm2 and self.is_locked:
            raise RuntimeError("The active area cannot change while recording")
        self._cm2 = cm2

    def amps_for(self, density: float) -> float:
        if self._cm2 is None:
            raise CellAreaMissing(
                "This uses current density, but no active area is entered. "
                "Enter it under Experiment Recording in Operation."
            )
        return density * self._cm2 / _MA_PER_A

    def density_for(self, amps: float) -> float | None:
        return None if self._cm2 is None else amps * _MA_PER_A / self._cm2

    def snapshot(self) -> dict[str, float]:
        """The run metadata entry, so a recording states the area it used."""
        return {} if self._cm2 is None else {AREA_METADATA_KEY: self._cm2}
