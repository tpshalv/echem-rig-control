from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class InstrumentSettingResult:
    succeeded: bool
    summary: str
