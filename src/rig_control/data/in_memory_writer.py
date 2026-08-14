from rig_control.data.experiment import ExperimentMetadata
from rig_control.data.records import MeasurementRecord
from rig_control.data.writer import ExperimentWriter
from rig_control.models import Event


class InMemoryExperimentWriter(ExperimentWriter):
    """Experiment writer used for tests and software simulation."""

    def __init__(self) -> None:
        self._is_open = False
        self._metadata: ExperimentMetadata | None = None
        self._measurements: list[MeasurementRecord] = []
        self._events: list[Event] = []

    @property
    def is_open(self) -> bool:
        return self._is_open

    @property
    def metadata(self) -> ExperimentMetadata | None:
        return self._metadata

    @property
    def measurements(self) -> tuple[MeasurementRecord, ...]:
        return tuple(self._measurements)

    @property
    def events(self) -> tuple[Event, ...]:
        return tuple(self._events)

    def open_experiment(
        self,
        metadata: ExperimentMetadata,
    ) -> None:
        if self.is_open:
            raise RuntimeError(
                "An experiment is already open for recording"
            )

        if not isinstance(metadata, ExperimentMetadata):
            raise TypeError(
                "Experiment writer requires ExperimentMetadata"
            )

        self._metadata = metadata
        self._measurements.clear()
        self._events.clear()
        self._is_open = True

    def write_measurement(
        self,
        record: MeasurementRecord,
    ) -> None:
        self._require_open()

        if not isinstance(record, MeasurementRecord):
            raise TypeError(
                "Experiment writer requires MeasurementRecord"
            )

        self._measurements.append(record)

    def write_event(self, event: Event) -> None:
        self._require_open()

        if not isinstance(event, Event):
            raise TypeError(
                "Experiment writer requires Event"
            )

        self._events.append(event)

    def close_experiment(self) -> None:
        self._require_open()
        self._is_open = False

    def _require_open(self) -> None:
        if not self.is_open:
            raise RuntimeError(
                "No experiment is open for recording"
            )