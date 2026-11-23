"""Showing readings in a chosen unit must never change what a device is told.

The hazard these tests exist for: a setpoint typed as 200 SCCM on a screen
being acted on as 200 SLPM by an instrument configured in SLPM.
"""

from dataclasses import replace

import pytest

from rig_control.app_settings import default_app_settings
from rig_control.control.commands import CommandSource, SetMfcFlow
from rig_control.devices.mass_flow_controller import (
    MassFlowControllerLimits,
    convert_mass_flow,
    mass_flow_unit_factor,
)
from rig_control.devices.simulated_mfc import SimulatedMassFlowController
from rig_control.display_units import NATIVE, DisplayUnits


def settings_with(**values):
    settings = default_app_settings()
    return replace(settings, values=dict(settings.values, **values))


def test_defaults_are_sccm_psia_ma_and_ma_per_cm2() -> None:
    units = default_app_settings().display_units

    assert units == DisplayUnits("psia", "SCCM", "mA", "mA/cm²")
    assert units.show_flow(0.2, "SLPM") == (200.0, "SCCM")
    assert units.show_current(0.25) == (250.0, "mA")


def test_native_leaves_every_reading_in_its_own_units() -> None:
    units = DisplayUnits(NATIVE, NATIVE)

    assert units.show_pressure(1.5, "bara") == (1.5, "bara")
    assert units.show_flow(0.2, "SLPM") == (0.2, "SLPM")


def test_a_chosen_unit_converts_both_directions_consistently() -> None:
    units = settings_with(
        display_pressure_unit="psia", display_flow_unit="SCCM"
    ).display_units

    value, unit = units.show_pressure(1.5, "bara")
    assert unit == "psia"
    assert value == pytest.approx(21.7556606)
    assert units.show_flow(0.2, "SLPM") == (200.0, "SCCM")
    # What a typed value means is the unit the row displays.
    assert units.entry_pressure_unit("bara") == "psia"
    assert units.entry_flow_unit("SLPM") == "SCCM"


def test_a_unit_that_cannot_be_converted_is_shown_as_reported() -> None:
    units = settings_with(display_flow_unit="SCCM").display_units

    # Never guessed: the instrument's own number and unit are truthful.
    assert units.show_flow(3.0, "SCFH") == (3.0, "SCFH")
    assert units.entry_flow_unit("SCFH") == "SCFH"


@pytest.mark.parametrize("unit", ["", "bar", "furlongs", "SCF"])
def test_an_unknown_flow_unit_is_refused_rather_than_assumed(unit: str) -> None:
    with pytest.raises(ValueError, match="Unsupported mass-flow unit"):
        mass_flow_unit_factor(unit)


def test_flow_conversion_is_exact_in_both_directions() -> None:
    assert convert_mass_flow(200, "SCCM", "SLPM") == 0.2
    assert convert_mass_flow(0.2, "SLPM", "SCCM") == 200.0
    assert convert_mass_flow(200, "sccm", "SCCM") == 200.0


def test_an_unsupported_display_unit_is_rejected_when_saved() -> None:
    with pytest.raises(ValueError, match="display flow unit must be one of"):
        settings_with(display_flow_unit="furlongs per fortnight")
    with pytest.raises(ValueError, match="display pressure unit must be one of"):
        settings_with(display_pressure_unit="barg")


def test_a_setpoint_entered_in_sccm_reaches_a_slpm_device_as_slpm() -> None:
    device = SimulatedMassFlowController(
        "mfc_a", MassFlowControllerLimits(2.0, "SLPM")
    )
    device.connect()

    device.set_flow_setpoint(200.0, "SCCM")

    # The instrument's own unit is SLPM, and 200 SCCM is 0.2 SLPM. Without
    # the unit travelling with the value this would have been 200 SLPM,
    # a hundredfold over this device's limit.
    assert device.flow_setpoint == pytest.approx(0.2)


def test_a_value_over_the_limit_is_refused_in_the_unit_it_was_entered() -> None:
    device = SimulatedMassFlowController(
        "mfc_a", MassFlowControllerLimits(2.0, "SLPM")
    )
    device.connect()

    with pytest.raises(ValueError, match="exceeds"):
        device.set_flow_setpoint(2500.0, "SCCM")  # 2.5 SLPM
    assert device.flow_setpoint == 0.0


def test_a_flow_command_carrying_an_unknown_unit_is_refused() -> None:
    with pytest.raises(ValueError, match="must be SCCM or SLPM"):
        SetMfcFlow("mfc_a", 200.0, CommandSource.MANUAL, unit="SCFH")


def test_a_command_without_a_unit_still_means_the_device_s_own_unit() -> None:
    device = SimulatedMassFlowController(
        "mfc_a", MassFlowControllerLimits(2.0, "SLPM")
    )
    device.connect()

    device.set_flow_setpoint(1.5)

    assert device.flow_setpoint == 1.5


# --- Typed entry: one display unit per quantity; a typed unit always wins ---------

from rig_control.display_units import Quantity, split_typed  # noqa: E402

ALL_DISPLAYS = [DisplayUnits(p, f, c, d) for p in ("psia", "bara", "kpaa", "mbara") for f in ("SCCM", "SLPM")
                for c in ("mA", "A") for d in ("mA/cm²", "A/cm²")]


@pytest.mark.parametrize("units", ALL_DISPLAYS)
def test_a_typed_unit_means_the_same_whatever_the_display_unit(units) -> None:
    assert units.read("250 mA", Quantity.CURRENT, "A") == 0.25
    assert units.read("0.25A", Quantity.CURRENT, "A") == 0.25
    assert units.read("10 mA/cm2", Quantity.DENSITY, "mA/cm²") == 10.0
    assert units.read("0.01 A/cm²", Quantity.DENSITY, "mA/cm²") == 10.0
    assert units.read("0.5 slpm", Quantity.FLOW, "SCCM") == 500.0
    assert units.read("14.5038 psia", Quantity.PRESSURE, "bara") == pytest.approx(1.0, rel=1e-5)


def test_a_bare_number_is_in_the_display_unit() -> None:
    ma = DisplayUnits(current="mA", density="A/cm²", flow="SLPM", pressure="psia")
    assert ma.read("250", Quantity.CURRENT, "A") == 0.25
    assert DisplayUnits(current="A").read("250", Quantity.CURRENT, "A") == 250.0  # The limits catch this.
    assert ma.read("0.3", Quantity.DENSITY, "mA/cm²") == 300.0
    assert ma.read("0.2", Quantity.FLOW, "SCCM") == 200.0
    # "native" means the instrument's own unit.
    assert DisplayUnits(flow=NATIVE).read("0.2", Quantity.FLOW, "SCCM", native_unit="SLPM") == 200.0


@pytest.mark.parametrize("text,quantity,message", [
    ("250 sccm", Quantity.CURRENT, "not a current unit"),
    ("10 mA", Quantity.DENSITY, "not a current density unit"),
    ("15 psi", Quantity.PRESSURE, "absolute"),
    ("1 bar", Quantity.PRESSURE, "absolute"),
    ("5 psig", Quantity.PRESSURE, "absolute"),
    ("2 V", Quantity.CURRENT, "not a current unit"),
    ("abc", Quantity.FLOW, "not a number"),
    ("", Quantity.FLOW, "not a number"),
])
def test_wrong_or_ambiguous_units_are_refused(text, quantity, message) -> None:
    with pytest.raises(ValueError, match=message):
        DisplayUnits().read(text, quantity, "A" if quantity is Quantity.CURRENT else "SCCM")


def test_split_typed_and_show_round_trip() -> None:
    assert split_typed(" 1.5e-3  mA ") == (0.0015, "mA")
    assert split_typed("-2") == (-2.0, "")
    units = DisplayUnits(current="mA", density="A/cm²")
    assert units.show_current(0.0123) == (12.3, "mA")
    assert units.show_density(300.0) == (0.3, "A/cm²")
    assert DisplayUnits(density="mA/cm2").density == "mA/cm²"
