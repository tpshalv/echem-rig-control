from dataclasses import dataclass

from rig_control.control.commands.common import CommandSource, validate_device_id, validate_number


@dataclass(frozen=True, slots=True)
class SetPressureSetpoint:
    device_id: str
    value: float
    source: CommandSource
    unit: str = "bara"

    def __post_init__(self):
        validate_device_id(self.device_id)
        validate_number(self.value, "Pressure")
        if self.unit not in {"bara", "barg", "psia", "psig", "paa", "pag", "kpaa", "kpag"}:
            raise ValueError("Pressure unit must explicitly specify absolute or gauge reference")


@dataclass(frozen=True, slots=True)
class ResumePressureControl:
    device_id: str
    source: CommandSource

    def __post_init__(self):
        validate_device_id(self.device_id)
