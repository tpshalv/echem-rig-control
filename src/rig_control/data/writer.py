from abc import ABC, abstractmethod

from rig_control.data.experiment import ExperimentMetadata
from rig_control.data.records import MeasurementRecord
from rig_control.models import Event


class ExperimentWriter(ABC):
    """Destination for one experiment's metadata, readings and events."""

    @property
    @abstractmethod
    def is_open(self) -> bool:
        """Return whether an experiment is currently being recorded."""

    @abstractmethod
    def open_experiment(
        self,
        metadata: ExperimentMetadata,
    ) -> None:
        """Begin recording one experiment."""

    @abstractmethod
    def write_measurement(
        self,
        record: MeasurementRecord,
    ) -> None:
        """Record one labelled measurement."""

    @abstractmethod
    def write_event(self, event: Event) -> None:
        """Record one warning, error or informational event."""

    @abstractmethod
    def close_experiment(self) -> None:
        """Finish and close the active experiment."""