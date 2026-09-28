"""Lets any Alicat device hand over a read-only dump of its settings."""

from rig_control.devices.dump_report import DumpQuery


class AlicatSetupReport:
    """Mixed into each Alicat adapter; sends queries only."""

    def setup_report(self) -> tuple[DumpQuery, ...]:
        return self._protocol.read_setup_report(self.unit_address)
