"""Lets any Alicat device report its settings and its valve drive.

Both are read-only. Valve drive comes from the documented VD query (Serial
Primer p. 25), so it needs no change to the instrument's data frame.
"""

from rig_control.devices.alicat.protocol_fields import AlicatFrameField
from rig_control.devices.dump_report import DumpQuery
from rig_control.devices.measurement_source import DeviceMeasurement
from rig_control.models import Measurement


class AlicatSetupReport:
    """Mixed into each Alicat adapter; sends queries only."""

    #: Set once a device answers VD with something unusable, so a controller
    #: on firmware older than 8v18 is asked once and then left alone.
    _valve_drive_unavailable = False

    def setup_report(self) -> tuple[DumpQuery, ...]:
        return self._protocol.read_setup_report(self.unit_address)

    def valve_drive_measurements(self, state) -> tuple[DeviceMeasurement, ...]:
        """Return the valve drive channels, where this instrument reports them.

        Nothing here can fail a measurement: a controller that does not answer
        VD simply has no valve-drive channel.
        """

        if self._valve_drive_unavailable:
            return ()
        if AlicatFrameField.VALVE_DRIVE in self._configuration.frame_fields:
            return ()  # Already in the data frame, decoded with the rest.
        try:
            drives = self._protocol.read_valve_drive(self.unit_address)
        except Exception:
            self._valve_drive_unavailable = True
            return ()
        if not drives:
            self._valve_drive_unavailable = True
            return ()
        # The primer (p. 25) describes upstream first on a dual-valve
        # controller, but a single-valve MC-2SLPM-D answers with four values,
        # so position cannot be trusted to mean upstream or downstream. The
        # first value is this instrument's valve drive; any others are
        # numbered rather than given a name we cannot stand behind.
        return tuple(
            DeviceMeasurement(
                "valve_drive_percent" if index == 0 else f"valve_drive_{index + 1}_percent",
                Measurement(value, "%", state.timestamp, state.quality),
            )
            for index, value in enumerate(drives)
        )
