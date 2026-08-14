from dataclasses import dataclass

from rig_control.models import Measurement


@dataclass(frozen=True, slots=True)
class MeasurementRecord:
    """One measurement labelled with its source and channel."""

    device_id: str
    channel: str
    measurement: Measurement
    sequence_step_id: str | None = None

    def __post_init__(self) -> None:
        self._validate_required_text(
            self.device_id,
            "Measurement device ID",
        )
        self._validate_required_text(
            self.channel,
            "Measurement channel",
        )

        if not isinstance(self.measurement, Measurement):
            raise TypeError(
                "Measurement record must contain a Measurement"
            )

        if self.sequence_step_id is not None:
            self._validate_required_text(
                self.sequence_step_id,
                "Sequence step ID",
            )

    @staticmethod
    def _validate_required_text(
        value: object,
        field_name: str,
    ) -> None:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field_name} cannot be empty")