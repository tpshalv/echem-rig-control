"""Taking a read-only settings dump, and knowing when one is not possible."""

from datetime import datetime

import pytest

from captures import load_capture

from rig_control.devices.alicat.bus import AlicatBus
from rig_control.devices.alicat.configuration import (
    AlicatMfcConfiguration,
    AlicatSerialConfiguration,
)
from rig_control.devices.alicat.driver import AlicatMassFlowController
from rig_control.devices.alicat.protocol import (
    SETUP_QUERIES,
    AlicatAsciiProtocolClient,
    AlicatEngineeringUnits,
    AlicatFrameField,
)
from rig_control.devices.dump_report import DeviceDump, DumpQuery
from rig_control.devices.manager import DeviceManager
from rig_control.devices.mass_flow_controller import MassFlowControllerLimits
from rig_control.diagnostics.device_dump import (
    can_dump,
    dump_device,
    dump_unavailable_reason,
    write_dump,
)
from rig_control.models import DeviceStatus
from rig_control.devices.simulated_mfc import SimulatedMassFlowController
from rig_control.transports.serial_text import SerialTextTransport
from rig_control.ui.diagnostics.model import DiagnosticViewModel


CAPTURE = load_capture("alicat-mc-2slpm-d-10v22-mass-flow.txt")


class CaptureTransport(SerialTextTransport):
    """Answers exactly as the captured instrument did."""

    def __init__(self, missing: tuple[str, ...] = ()) -> None:
        self._open = False
        self.requests: list[str] = []
        self.missing = missing

    @property
    def is_open(self) -> bool:
        return self._open

    def open(self) -> None:
        self._open = True

    def close(self) -> None:
        self._open = False

    def request(self, message: str) -> str:
        self.requests.append(message)
        if message in self.missing:
            raise TimeoutError("no reply")
        try:
            return CAPTURE[message]
        except KeyError:
            raise TimeoutError("no reply") from None


def alicat_device(transport: CaptureTransport) -> AlicatMassFlowController:
    configuration = AlicatMfcConfiguration(
        device_id="nitrogen_mfc",
        friendly_name="Nitrogen",
        unit_address="A",
        connection=AlicatSerialConfiguration("bus", "COM5", 19200, 1.0),
        limits=MassFlowControllerLimits(2.0, "SLPM"),
        frame_fields=(
            AlicatFrameField.ABSOLUTE_PRESSURE,
            AlicatFrameField.GAS_TEMPERATURE,
            AlicatFrameField.VOLUMETRIC_FLOW,
            AlicatFrameField.MASS_FLOW,
            AlicatFrameField.SETPOINT,
            AlicatFrameField.TOTALIZED_FLOW,
            AlicatFrameField.GAS,
        ),
        engineering_units=AlicatEngineeringUnits(
            "SLPM", "LPM", "PSIA", "degC", "SLPM", "SL"
        ),
    )
    return AlicatMassFlowController(
        configuration,
        AlicatAsciiProtocolClient(
            AlicatBus("bus", transport),
            configuration.frame_fields,
            configuration.engineering_units,
        ),
    )


def test_dump_records_every_documented_query_and_changes_nothing() -> None:
    transport = CaptureTransport()
    device = alicat_device(transport)
    device.connect()
    before = list(transport.requests)

    dump = dump_device(device, "mass_flow_controller (alicat)")

    assert [query.command for query in dump.queries] == [
        f"A{suffix}" for suffix, _ in SETUP_QUERIES
    ]
    assert not dump.failures
    assert dump.device_id == "nitrogen_mfc"
    # Only the documented queries were sent, and no command carries an
    # argument that would change a setting.
    sent = transport.requests[len(before):]
    assert sent == [f"A{suffix}" for suffix, _ in SETUP_QUERIES]
    replies = {query.command: query.reply for query in dump.queries}
    assert replies["ALR"] == "A 37 +0.0000 +2.0000 7 SLPM"
    assert replies["AR20"] == "A   020 = 9239"


def test_a_query_that_does_not_answer_is_recorded_not_fatal() -> None:
    transport = CaptureTransport(missing=("AR122", "AFPF 2"))
    device = alicat_device(transport)
    device.connect()

    dump = dump_device(device)

    assert len(dump.queries) == len(SETUP_QUERIES)
    assert [query.command for query in dump.failures] == ["AR122", "AFPF 2"]
    assert "TimeoutError" in dump.failures[0].reply
    text = dump.to_text()
    assert "!!" in text
    assert "2 of 9 queries did not answer" in text


def test_dump_is_unavailable_until_the_device_is_connected() -> None:
    device = alicat_device(CaptureTransport())

    assert device.status is DeviceStatus.DISCONNECTED
    assert can_dump(device) is False
    assert "Connect the device first" in dump_unavailable_reason(device)

    device.connect()

    assert can_dump(device) is True
    assert dump_unavailable_reason(device) == ""


def test_a_driver_without_a_dump_says_so_rather_than_failing() -> None:
    device = SimulatedMassFlowController(
        "simulated_mfc", MassFlowControllerLimits(2.0, "SLPM")
    )
    device.connect()

    assert can_dump(device) is False
    assert "has been written" in dump_unavailable_reason(device)
    with pytest.raises(ValueError, match="has been written"):
        dump_device(device)


def test_dump_is_written_where_it_can_be_found_later(tmp_path) -> None:
    dump = DeviceDump(
        device_id="nitrogen mfc/A",
        device_type="mass_flow_controller (alicat)",
        queries=(DumpQuery("ALR", "loop", "A 37 +0.0000 +2.0000 7 SLPM"),),
        taken_at=datetime(2026, 9, 28, 14, 30, 5),
    )

    path = write_dump(dump, tmp_path)

    # The device ID becomes a safe file name that still identifies the device.
    assert path.name == "nitrogen-mfc-A-20260928-143005.txt"
    text = path.read_text(encoding="utf-8")
    assert "Device settings dump: nitrogen mfc/A" in text
    assert "None of them changes a setting." in text
    assert "A 37 +0.0000 +2.0000 7 SLPM" in text


def test_diagnostics_screen_offers_a_dump_only_where_one_is_possible(
    tmp_path, monkeypatch
) -> None:
    import rig_control.diagnostics.device_dump as dump_module

    monkeypatch.setattr(dump_module, "device_dumps_directory", lambda: tmp_path)
    manager = DeviceManager()
    alicat = alicat_device(CaptureTransport())
    simulated = SimulatedMassFlowController(
        "simulated_mfc", MassFlowControllerLimits(2.0, "SLPM")
    )
    manager.register(alicat)
    manager.register(simulated)
    model = DiagnosticViewModel(manager)

    # Disconnected: offered, but not yet.
    assert "Connect the device first" in model.dump_unavailable_reason("nitrogen_mfc")
    alicat.connect()
    simulated.connect()
    assert model.dump_unavailable_reason("nitrogen_mfc") == ""
    assert "has been written" in model.dump_unavailable_reason("simulated_mfc")
    assert model.dump_unavailable_reason("no_such_device") == "Select a device."

    result = model.dump_device_settings("nitrogen_mfc")

    assert result.succeeded is True
    assert "9 of 9 queries answered" in result.summary
    saved = list(tmp_path.glob("nitrogen_mfc-*.txt"))
    assert len(saved) == 1
    assert "A 37 +0.0000 +2.0000 7 SLPM" in saved[0].read_text(encoding="utf-8")


def test_dump_failure_is_reported_without_crashing_the_screen(tmp_path) -> None:
    manager = DeviceManager()
    device = alicat_device(CaptureTransport())
    manager.register(device)
    model = DiagnosticViewModel(manager)

    result = model.dump_device_settings("nitrogen_mfc")

    assert result.succeeded is False
    assert "Could not dump settings for" in result.summary


class FakeWidget:
    """Records what the screen would do to a button or label."""

    def __init__(self) -> None:
        self.settings: dict[str, str] = {}

    def configure(self, **changes: str) -> None:
        self.settings.update(changes)


def diagnostics_window(reason: str, selected: str | None):
    from rig_control.ui.diagnostics.window import DiagnosticWindow

    window = DiagnosticWindow.__new__(DiagnosticWindow)
    window._dump_button = FakeWidget()
    window._dump_hint = FakeWidget()
    window._selected_device_id = lambda: selected
    window._view_model = type(
        "Stub", (), {"dump_unavailable_reason": staticmethod(lambda _: reason)}
    )()
    return window


def test_dump_button_is_greyed_out_when_a_dump_is_not_possible() -> None:
    window = diagnostics_window("No settings dump has been written", "simulated_mfc")

    window._update_dump_availability()

    assert window._dump_button.settings["state"] == "disabled"
    # The screen says why, rather than offering something that would fail.
    assert "No settings dump has been written" in window._dump_hint.settings["text"]


def test_dump_button_is_enabled_for_a_device_that_can_be_dumped() -> None:
    window = diagnostics_window("", "nitrogen_mfc")

    window._update_dump_availability()

    assert window._dump_button.settings["state"] == "normal"
    assert "read-only" in window._dump_hint.settings["text"]


def test_dump_button_is_greyed_out_when_nothing_is_selected() -> None:
    window = diagnostics_window("", None)

    window._update_dump_availability()

    assert window._dump_button.settings["state"] == "disabled"
    assert window._dump_hint.settings["text"] == ""
