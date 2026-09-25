"""Shared verification for Alicat controllers; never changes their configuration."""

from dataclasses import asdict
import json

from rig_control.devices.alicat.verification import verify_role
from rig_control.models import Event, EventSeverity


class AlicatVerifiedControl:
    def _initialize_verification(self, event_sink=None) -> None:
        self.control_configuration = None
        self.control_ready = False
        self.verification_message = "Unverified: connect and check instrument configuration"
        self._verification_event_sink = event_sink

    def verify_control(self) -> None:
        previous = (self.control_ready, self.verification_message)
        self.control_ready = False
        try:
            observed = self._protocol.read_control_configuration(self.unit_address)
            self.control_configuration = observed
            verify_role(observed, bpr=self._configuration.is_bpr,
                        expected_serial=self._configuration.expected_serial,
                        expected_frame_signature=self._configuration.verified_frame_signature,
                        flow_unit=self._configuration.limits.flow_unit,
                        downstream_confirmed=self._configuration.downstream_valve_confirmed)
            self.control_ready = True
            self.verification_message = "Verified: " + observed.description
        except Exception as error:
            self.verification_message = f"Control blocked: {error}"
            raise RuntimeError(self.verification_message) from error
        finally:
            if previous != (self.control_ready, self.verification_message) and self._verification_event_sink:
                self._verification_event_sink(Event(
                    source=self.device_id,
                    severity=EventSeverity.INFO if self.control_ready else EventSeverity.ERROR,
                    message=json.dumps({"kind": "alicat_verification", "ready": self.control_ready,
                                        "detail": self.verification_message,
                                        "configuration": asdict(self.control_configuration) if self.control_configuration else None},
                                       default=str),
                ), None)

    def _inspect_control(self) -> None:
        try:
            self.verify_control()
        except RuntimeError:
            # Keep a readable diagnostic connection; all operating writes are gated.
            pass
