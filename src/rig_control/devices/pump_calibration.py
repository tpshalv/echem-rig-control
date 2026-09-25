"""Pump speed-to-flow calibration: fitting a measured (RPM, ml/min) curve.

Flow rate is not a quantity the pump itself reports or controls (see
pump.py) - it is derived from an external, user-measured calibration that
is expected to be replaced from time to time as tubing ages. This module
holds that calibration's data model and fitting math; instrument_settings
holds where it is stored and how it is reviewed and accepted.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from math import isfinite


@dataclass(frozen=True, slots=True)
class CalibrationPoint:
    """One measured (speed, flow) pair from a calibration run."""

    speed_rpm: float
    flow_ml_per_min: float

    def __post_init__(self) -> None:
        for name, value in (
            ("speed_rpm", self.speed_rpm),
            ("flow_ml_per_min", self.flow_ml_per_min),
        ):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
                raise ValueError(f"{name} must be a finite number")
            if value < 0:
                raise ValueError(f"{name} cannot be negative")


@dataclass(frozen=True, slots=True)
class LinearFit:
    """An ordinary-least-squares fit: flow_ml_per_min = slope * speed_rpm + intercept."""

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
        # divide by zero the first time speed_for_flow() is called.
        if self.slope <= 0:
            raise ValueError("Fit slope must be positive; flow should increase with speed")

    def flow_for_speed(self, speed_rpm: float) -> float:
        return self.slope * speed_rpm + self.intercept

    def speed_for_flow(self, flow_ml_per_min: float) -> float:
        return (flow_ml_per_min - self.intercept) / self.slope


def fit_linear(points: Sequence[CalibrationPoint]) -> LinearFit:
    """Ordinary least-squares fit, not forced through the origin.

    A free intercept is used deliberately: real tubing often has a small
    dead zone at low speed, and forcing the line through zero would hide
    that rather than reveal it in the R^2.
    """

    if len(points) < 2:
        raise ValueError("At least two calibration points are required to fit a line")

    speeds = [point.speed_rpm for point in points]
    flows = [point.flow_ml_per_min for point in points]
    count = len(points)
    mean_speed = sum(speeds) / count
    mean_flow = sum(flows) / count

    speed_variance = sum((speed - mean_speed) ** 2 for speed in speeds)
    if speed_variance == 0:
        raise ValueError("Calibration points must use more than one distinct speed")

    covariance = sum(
        (speed - mean_speed) * (flow - mean_flow) for speed, flow in zip(speeds, flows)
    )
    slope = covariance / speed_variance
    intercept = mean_flow - slope * mean_speed

    flow_variance = sum((flow - mean_flow) ** 2 for flow in flows)
    if flow_variance == 0:
        r_squared = 1.0
    else:
        residual = sum(
            (flow - (slope * speed + intercept)) ** 2 for speed, flow in zip(speeds, flows)
        )
        r_squared = 1.0 - residual / flow_variance

    if slope <= 0:
        raise ValueError(
            "Fitted calibration has a non-positive slope; flow should increase with speed "
            "-- check the entered points"
        )

    return LinearFit(slope, intercept, r_squared)


@dataclass(frozen=True, slots=True)
class PumpCalibration:
    """One accepted calibration for one pump, kept in a per-pump history."""

    calibration_id: str
    device_id: str
    operator: str
    points: tuple[CalibrationPoint, ...]
    fit: LinearFit
    notes: str = ""
    created_utc: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        if not isinstance(self.calibration_id, str) or not self.calibration_id.strip():
            raise ValueError("Calibration ID cannot be empty")
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
