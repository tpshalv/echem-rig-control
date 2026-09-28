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


def test_defaults_leave_every_reading_in_its_own_units() -> None:
    units = default_app_settings().display_units

    assert units == DisplayUnits(NATIVE, NATIVE)
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
