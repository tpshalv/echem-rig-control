"""Shared role checking for Alicat controllers; never changes their configuration."""

from dataclasses import asdict, replace
import json

from rig_control.devices.alicat.protocol_fields import AlicatFrameField
from rig_control.devices.alicat.verification import (
    column_resolution,
    parse_frame_layout,
    verify_role,
)
from rig_control.models import Event, EventSeverity


class AlicatVerifiedControl:
    def _initialize_verification(self, event_sink=None) -> None:
        self.control_configuration = None
        self.control_mode = None
        self.control_ready = False
        self.verification_message = "Not checked: connect to read the instrument's control mode"
        self._verification_event_sink = event_sink

    def verify_control(self, *, full: bool = False) -> None:
        """Confirm the live control mode still matches this device's saved role.

        Setup metadata (identity, firmware, data frame) is read once on
        connection. Ordinary measurements re-read only the loop variable and
        the inverse-control register, which is what control safety depends on.
        """

        previous = (self.control_ready, self.verification_message)
        # Deliberately not cleared before the check: the screen reads this
        # flag from another thread to decide whether a setpoint may be
        # entered, and clearing it here made every control flip to read-only
        # for the duration of each re-check.
        try:
            if full or self.control_configuration is None:
                self.control_configuration = self._protocol.read_control_configuration(
                    self.unit_address
                )
                observed = self.control_configuration.mode
            else:
                observed = self._protocol.read_control_mode(self.unit_address)
                if observed.minimum_setpoint is None or observed.maximum_setpoint is None:
                    cached = self.control_configuration.mode
                    observed = replace(
                        observed,
                        minimum_setpoint=cached.minimum_setpoint,
                        maximum_setpoint=cached.maximum_setpoint,
                    )
            self.control_mode = observed
            verify_role(
                observed,
                bpr=self._configuration.is_bpr,
                flow_unit=self._configuration.limits.flow_unit,
                downstream_confirmed=self._configuration.downstream_valve_confirmed,
            )
            self.control_ready = True
            self.verification_message = "Confirmed: " + observed.description
        except Exception as error:
            self.control_ready = False
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

    def setpoint_tolerance(self) -> float:
        """How close a readback must be before a setpoint counts as accepted.

        An instrument reports its setpoint to a fixed number of decimal
        places, so a value it cannot express exactly comes back rounded. The
        tolerance is therefore at least one least-significant digit of what
        this instrument actually reports, taken from its own data frame. A
        clamped or ignored setpoint differs by far more than that, so this
        still catches what the check exists for.
        """

        configured = self._configuration.setpoint_tolerance
        observed = self.control_configuration
        if observed is None:
            return configured
        try:
            columns = parse_frame_layout(self.unit_address, observed.frame_description)
        except ValueError:
            return configured
        resolution = column_resolution(columns, AlicatFrameField.SETPOINT)
        return max(configured, resolution) if resolution else configured

    def _inspect_control(self) -> None:
        try:
            self.verify_control(full=True)
        except RuntimeError:
            # Keep a readable diagnostic connection; all operating writes are gated.
            pass
