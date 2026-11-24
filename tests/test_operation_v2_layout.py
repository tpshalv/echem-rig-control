"""The v2 Operation layout: quantities down, devices across, settings beside readings."""
from datetime import UTC, datetime
import tkinter as tk

import pytest

from rig_control.ui.operation.compact_layout import (
    OVERVIEW_LINES, control_prefix, details, format_value, needs_attention, severity, system_layout,
)
from rig_control.ui.operation.model import OperationChannelRow

NOW = datetime(2026, 11, 23, 12, 0, tzinfo=UTC)


def row(device, channel, value, unit="", *, name=None, quality="good", read=True, writable=False,
        editor="number", device_name=None, system="Gas flow", **extra) -> OperationChannelRow:
    return OperationChannelRow(
        device, device_name or device.title(), channel, name or channel.replace("_", " ").title(),
        value, unit, quality, NOW if read else None, system, writable, editor, **extra,
    )


def mfc(device, flow, setpoint):
    return [row(device, "mass_flow", flow, "SCCM"),
            row(device, "setpoint", setpoint, "SCCM", name="Flow setpoint", writable=True),
            row(device, "absolute_pressure", 1.01, "bara")]


def test_the_same_quantity_on_different_devices_shares_a_line():
    layout = system_layout(mfc("n2", 49.8, 50.0) + mfc("co2", 20.1, 20.0))

    assert [device for device, _ in layout.devices] == ["n2", "co2"]
    flow = next(line for line in layout.lines if line.key == "mass_flow")
    assert flow.label == "Mass flow" and flow.unit == "SCCM"
    assert set(flow.cells) == {"n2", "co2"}


def test_a_setpoint_sits_with_its_reading_not_on_a_line_of_its_own():
    layout = system_layout(mfc("n2", 49.8, 50.0))

    assert "setpoint" not in [line.key for line in layout.lines]
    cell = next(line for line in layout.lines if line.key == "mass_flow").cells["n2"]
    assert cell.reading.value == 49.8 and cell.control.value == 50.0


def test_a_setting_without_a_reading_is_its_own_editable_cell():
    rows = [row("psu", "output_enabled", False, writable=True, editor="boolean", system="Electrical"),
            row("psu", "voltage_setpoint", 6.0, "V", writable=True, system="Electrical")]
    layout = system_layout(rows)

    output = next(line for line in layout.lines if line.key == "output_enabled")
    assert output.cells["psu"].reading is None and output.cells["psu"].control.editor == "boolean"
    # With no voltage reading, the limit is not hidden behind one.
    assert "voltage_setpoint" in [line.key for line in layout.lines]


def test_units_that_differ_are_left_to_each_cell():
    rows = [row("a", "measurement", 25.0, "degC"), row("b", "measurement", 50.0, "%RH")]
    line = system_layout(rows).lines[0]
    assert line.unit == ""


def test_known_lines_come_in_a_fixed_order_and_unknown_ones_follow():
    rows = [row("x", "zeta_reading", 1.0, name="Zeta"), row("x", "absolute_pressure", 1.0, "bara"),
            row("x", "mass_flow", 1.0, "SCCM"), row("x", "alpha_reading", 1.0, name="Alpha")]
    assert [line.key for line in system_layout(rows).lines] == [
        "mass_flow", "absolute_pressure", "alpha_reading", "zeta_reading"]


def test_long_text_is_a_note_under_the_grid():
    rows = mfc("n2", 1.0, 1.0) + [row("n2", "control_verification", "Setpoint source is front panel; "
                                       "writes are disabled until verified.", quality="bad", read=False)]
    layout = system_layout(rows)
    assert [note.channel for note in layout.notes] == ["control_verification"]
    assert "control_verification" not in [line.key for line in layout.lines]


def test_empty_expected_lines_go_once_the_device_reports_under_other_names():
    rows = [row("probe", "measurement", 25.0, "degC"), row("probe", "temperature", None, read=False)]
    assert [line.key for line in system_layout(rows).lines] == ["measurement"]
    # A device that has sent nothing keeps its empty lines, so it shows as missing.
    silent = [row("probe", "temperature", None, read=False)]
    assert [line.key for line in system_layout(silent).lines] == ["temperature"]


def test_overview_keeps_only_the_headline_lines():
    rows = mfc("n2", 49.8, 50.0) + [row("n2", "gas", "N2")]
    layout = system_layout(rows, only=OVERVIEW_LINES)
    assert [line.key for line in layout.lines] == ["mass_flow", "absolute_pressure"]


def test_only_readings_raise_an_alarm():
    stale = row("n2", "mass_flow", 49.8, "SCCM", quality="stale")
    unusable = row("psu", "current_density_setpoint", None, quality="bad", read=False, writable=False)
    override = row("bpr", "pressure_ceiling", 6.0, "bara", quality="warning", read=False)

    assert severity(stale) == "error"
    assert severity(unusable) == ""  # Not set up yet (no area), not a fault.
    assert severity(override) == "warning"
    assert needs_attention([unusable, override]) == "warning"
    assert needs_attention([override, stale]) == "error"


def test_values_read_cleanly():
    assert format_value(row("a", "mass_flow", 49.81234)) == "49.81"
    assert format_value(row("a", "mass_flow", None)) == "—"
    assert format_value(row("a", "output_enabled", True, editor="boolean")) == "On"
    assert format_value(row("a", "watchdog_tripped", False)) == "OK"
    assert format_value(row("a", "operating_mode", "constant_current", editor="mode")) == "Constant current"
    assert format_value(row("a", "resume", "Resume (may discharge gas downstream)", editor="action")) == "Resume"


def test_a_supply_limit_says_limit_and_a_setpoint_says_set():
    assert control_prefix(row("psu", "voltage_setpoint", 6.0, name="Voltage limit")) == "limit"
    assert control_prefix(row("n2", "setpoint", 6.0, name="Flow setpoint")) == "set"


def test_hover_details_carry_quality_and_time():
    text = details(row("n2", "mass_flow", 49.8, "SCCM", quality="stale", device_name="Nitrogen"))
    assert "Nitrogen" in text and "Quality: stale" in text and "Updated" in text


# --- The grid widget ---------------------------------------------------------------

@pytest.fixture
def root():
    window = tk.Tk()
    window.withdraw()
    try:
        yield window
    finally:
        window.destroy()


def test_grid_builds_once_and_then_only_updates(root):
    from rig_control.ui.operation.compact_panels import QuantityGrid

    grid = QuantityGrid(root, on_edit=lambda *_: None, on_trend=lambda *_: None)
    grid.pack()
    grid.show(system_layout(mfc("n2", 49.8, 50.0)))
    built = grid.winfo_children()
    grid.show(system_layout(mfc("n2", 51.2, 50.0)))

    assert grid.winfo_children() == built
    widgets = grid._cells[("mass_flow", "n2")]
    assert widgets.value.cget("text") == "51.2"
    assert widgets.control.cget("text") == "set 50"


def test_clicking_a_setting_asks_to_edit_it_and_a_stale_reading_turns_red(root):
    from rig_control.ui.common.theme_v2 import ERROR
    from rig_control.ui.operation.compact_panels import QuantityGrid

    edits = []
    grid = QuantityGrid(root, on_edit=lambda r, anchor: edits.append(r.key), on_trend=lambda *_: None)
    grid.pack()
    rows = mfc("n2", 49.8, 50.0)
    rows[0] = row("n2", "mass_flow", 49.8, "SCCM", quality="stale")
    grid.show(system_layout(rows))
    widgets = grid._cells[("mass_flow", "n2")]

    root.deiconify(); root.update()  # Tk only delivers clicks to a shown window.
    widgets.control.event_generate("<Button-1>", x=2, y=2)
    root.update()
    assert edits == [("n2", "setpoint")]
    assert str(widgets.value.cget("foreground")) == ERROR
