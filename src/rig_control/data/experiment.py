from dataclasses import dataclass, field
from datetime import datetime, timezone
from types import MappingProxyType
from typing import TypeAlias
from collections.abc import Mapping


MetadataValue: TypeAlias = str | int | float | bool | None


@dataclass(frozen=True, slots=True)
class ExperimentMetadata:
    """Information stored once for an entire experiment."""

    experiment_id: str
    operator: str
    started_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    experiment_type: str | None = None
    sequence_file: str | None = None
    notes: str | None = None
    extra: Mapping[str, MetadataValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self._validate_required_text(
            self.experiment_id,
            "Experiment ID",
        )
        self._validate_required_text(
            self.operator,
            "Operator",
        )

        if (
            self.started_at.tzinfo is None
            or self.started_at.utcoffset() is None
        ):
            raise ValueError(
                "Experiment start time must include a timezone"
            )

        self._validate_optional_text(
            self.experiment_type,
            "Experiment type",
        )
        self._validate_optional_text(
            self.sequence_file,
            "Sequence file",
        )
        self._validate_optional_text(
            self.notes,
            "Notes",
        )

        copied_extra = dict(self.extra)

        for key, value in copied_extra.items():
            self._validate_required_text(key, "Metadata field name")

            if not isinstance(
                value,
                (str, int, float, bool, type(None)),
            ):
                raise TypeError(
                    f"Metadata field {key!r} has unsupported value "
                    f"type {type(value).__name__}"
                )

        # Prevent metadata from being silently changed after the
        # experiment has started.
        object.__setattr__(
            self,
            "extra",
            MappingProxyType(copied_extra),
        )

    @staticmethod
    def _validate_required_text(
        value: object,
        field_name: str,
    ) -> None:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field_name} cannot be empty")

    @staticmethod
    def _validate_optional_text(
        value: object,
        field_name: str,
    ) -> None:
        if value is not None and not isinstance(value, str):
            raise TypeError(
                f"{field_name} must be text or None"
            )