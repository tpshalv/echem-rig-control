"""Pressure quantities and operating policy shared by UI, API and drivers."""

from abc import abstractmethod
from dataclasses import dataclass
from math import isfinite

from rig_control.devices.base import Device
from rig_control.devices.measurement_source import MeasurementSource

NORMAL_MAXIMUM_PRESSURE_PA = 250_000.0


def finite_number(value: float, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be numeric")
    if not isfinite(value):
        raise ValueError(f"{name} must be finite")
    return float(value)


def absolute_unit_factor(unit: str) -> float:
    # Bare engineering units are accepted only for a verified absolute variable.
    factors = {
        "pa": 1.0, "paa": 1.0, "kpa": 1000.0, "kpaa": 1000.0,
        "bar": 100_000.0, "bara": 100_000.0, "mbar": 100.0,
        "mbara": 100.0, "psia": 6894.757293168, "psi": 6894.757293168,
    }
    try:
        return factors[unit.strip().casefold()]
    except KeyError as error:
        raise ValueError(f"Unsupported absolute pressure unit {unit!r}") from error


@dataclass(frozen=True, slots=True)
class PressurePolicy:
    high_pressure_mode: bool = False
    maximum_pressure_bara: float = 2.5
    atmospheric_reference_bara: float = 1.01325

    def __post_init__(self) -> None:
        if not isinstance(self.high_pressure_mode, bool):
            raise TypeError("High Pressure Mode must be Boolean")
        for name in ("maximum_pressure_bara", "atmospheric_reference_bara"):
            if finite_number(getattr(self, name), name) <= 0:
                raise ValueError(f"{name} must be positive")

    @property
    def maximum_pa(self) -> float:
        return self.maximum_pressure_bara * 100_000 if self.high_pressure_mode else NORMAL_MAXIMUM_PRESSURE_PA

    @property
    def overridden(self) -> bool:
        return self.maximum_pa > NORMAL_MAXIMUM_PRESSURE_PA

    def to_absolute_pa(self, value: float, unit: str) -> float:
        value = finite_number(value, "Pressure")
        # Public input requires explicit pressure reference, never ambiguous 'bar'.
        factors = {"bara": 100_000.0, "barg": 100_000.0, "psia": 6894.757293168,
                   "psig": 6894.757293168, "paa": 1.0, "pag": 1.0,
                   "kpaa": 1000.0, "kpag": 1000.0}
        key = unit.strip().casefold()
        if key not in factors:
            raise ValueError("Specify an absolute or gauge pressure unit (e.g. bara or barg)")
        result = value * factors[key]
        if key.endswith("g"):
            result += self.atmospheric_reference_bara * 100_000
        return result

    def validate(self, absolute_pa: float, instrument_maximum_pa: float) -> float:
        absolute_pa = finite_number(absolute_pa, "Absolute pressure")
        maximum = min(self.maximum_pa, finite_number(instrument_maximum_pa, "Instrument maximum"))
        if absolute_pa <= 0 or absolute_pa > maximum:
            raise ValueError(f"Requested pressure {absolute_pa / 100_000:g} bara must be greater than zero "
                             f"and at most {maximum / 100_000:g} bara")
        return absolute_pa


class PressureController(Device, MeasurementSource):
    @property
    @abstractmethod
    def pressure_policy(self) -> PressurePolicy: ...

    @property
    @abstractmethod
    def maximum_pressure_pa(self) -> float: ...

    @abstractmethod
    def set_pressure_setpoint(self, value: float, unit: str = "bara") -> None: ...

    @abstractmethod
    def enter_safe_state(self) -> None: ...

    @abstractmethod
    def resume_regulation(self) -> None: ...
