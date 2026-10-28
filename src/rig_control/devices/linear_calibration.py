"""Linear calibrations: fitting a measured (x, y) line and applying it.

Used where the instrument's own number needs converting with an external,
user-measured calibration that is expected to be replaced from time to time:
pump speed to flow (tubing ages), and EZO-HUM reported RH to an estimate of
the true RH in a CO2 ambient. This module holds the data model and fitting
math; instrument_settings.calibration holds where calibrations are stored
and how they are reviewed and accepted.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from math import isfinite


@dataclass(frozen=True, slots=True)
class CalibrationPoint:
    """One measured (x, y) pair: the instrument's value and the true value."""

    x: float
    y: float

    def __post_init__(self) -> None:
        for name, value in (("x", self.x), ("y", self.y)):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
                raise ValueError(f"{name} must be a finite number")
            if value < 0:
                raise ValueError(f"{name} cannot be negative")


@dataclass(frozen=True, slots=True)
class LinearFit:
    """An ordinary-least-squares fit: y = slope * x + intercept."""

    slope: float
    intercept: float
    r_squared: float

    def __post_init__(self) -> None:
        for name, value in (
            ("slope", self.slope),
            ("intercept", self.intercept),
            ("r_squared", self.r_squared),
        ):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
                raise ValueError(f"{name} must be a finite number")
        # Enforced here too, not only in fit_linear(): a fit loaded from a
        # hand-edited or corrupted history file must fail at load time, not
        # divide by zero the first time x_for_y() is called.
        if self.slope <= 0:
            raise ValueError("Fit slope must be positive; y should increase with x")

    def y_for_x(self, x: float) -> float:
        return self.slope * x + self.intercept

    def x_for_y(self, y: float) -> float:
        return (y - self.intercept) / self.slope


def fit_linear(points: Sequence[CalibrationPoint]) -> LinearFit:
    """Ordinary least-squares fit, not forced through the origin.

    A free intercept is used deliberately: real tubing often has a small
    dead zone at low speed, and an RH probe's error is an offset as much as
    a gain. Forcing the line through zero would hide that rather than
    reveal it in the R^2.
    """

    if len(points) < 2:
        raise ValueError("At least two calibration points are required to fit a line")

    xs = [point.x for point in points]
    ys = [point.y for point in points]
    count = len(points)
    mean_x = sum(xs) / count
    mean_y = sum(ys) / count

    x_variance = sum((x - mean_x) ** 2 for x in xs)
    if x_variance == 0:
        raise ValueError("Calibration points must use more than one distinct x value")

    covariance = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    slope = covariance / x_variance
    intercept = mean_y - slope * mean_x

    y_variance = sum((y - mean_y) ** 2 for y in ys)
    if y_variance == 0:
        r_squared = 1.0
    else:
        residual = sum((y - (slope * x + intercept)) ** 2 for x, y in zip(xs, ys))
        r_squared = 1.0 - residual / y_variance

    if slope <= 0:
        raise ValueError(
            "Fitted calibration has a non-positive slope; y should increase with x "
            "-- check the entered points"
        )

    return LinearFit(slope, intercept, r_squared)


@dataclass(frozen=True, slots=True)
class Calibration:
    """One accepted calibration for one device, kept in a per-device history.

    ``name`` is chosen by the operator (by default the date) and is unique
    within that device's history.
    """

    name: str
    device_id: str
    operator: str
    points: tuple[CalibrationPoint, ...]
    fit: LinearFit
    notes: str = ""
    created_utc: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("Calibration name cannot be empty")
        if not isinstance(self.device_id, str) or not self.device_id.strip():
            raise ValueError("Device ID cannot be empty")
        if not isinstance(self.operator, str) or not self.operator.strip():
            raise ValueError("Operator cannot be empty")
        if not isinstance(self.points, tuple) or len(self.points) < 2:
            raise ValueError("Calibration must keep at least two points")
        if not isinstance(self.fit, LinearFit):
            raise TypeError("Calibration fit must be a LinearFit")
        if self.created_utc.tzinfo is None or self.created_utc.utcoffset() is None:
            raise ValueError("Calibration creation time must include a timezone")
