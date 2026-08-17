from rig_control.control.service import RigControlService
from rig_control.devices.manager import DeviceManager
from rig_control.devices.mass_flow_controller import (
    MassFlowControllerLimits,
)
from rig_control.devices.power_supply import PowerSupplyLimits
from rig_control.devices.simulated_mfc import (
    SimulatedMassFlowController,
)
from rig_control.devices.simulated_power_supply import (
    SimulatedPowerSupply,
)
from rig_control.devices.power_supply import (
    PowerSupplyOperatingMode,
)
from rig_control.ui.manual_control_model import (
    ManualControlViewModel,
)



def make_model() -> tuple[
    ManualControlViewModel,
    RigControlService,
    SimulatedPowerSupply,
    SimulatedMassFlowController,
]:
    manager = DeviceManager()

    supply = SimulatedPowerSupply(
        "main_supply",
        PowerSupplyLimits(30.0, 108.0, 1080.0),
    )
    mfc = SimulatedMassFlowController(
        "dry_gas_mfc",
        MassFlowControllerLimits(100.0, "sccm"),
    )

    supply.connect()
    mfc.connect()

    manager.register(supply)
    manager.register(mfc)

    service = RigControlService(manager)

    return (
        ManualControlViewModel(manager, service),
        service,
        supply,
        mfc,
    )


def test_rows_are_generated_from_device_capabilities() -> None:
    model, _, _, _ = make_model()

    mfc_rows = model.mfc_rows()
    supply_rows = model.power_supply_rows()

    assert len(mfc_rows) == 1
    assert mfc_rows[0].device_id == "dry_gas_mfc"
    assert mfc_rows[0].maximum_flow == 100.0
    assert mfc_rows[0].flow_unit == "sccm"

    assert len(supply_rows) == 1
    assert supply_rows[0].device_id == "main_supply"
    assert supply_rows[0].maximum_voltage == 30.0
    assert supply_rows[0].maximum_current == 108.0


def test_manual_mfc_flow_command_succeeds() -> None:
    model, _, _, mfc = make_model()

    result = model.set_mfc_flow("dry_gas_mfc", 25.0)

    assert result.succeeded is True
    assert result.technical_details is None
    assert "25.0 sccm" in result.summary
    assert mfc.flow_setpoint == 25.0


def test_manual_power_supply_commands_succeed() -> None:
    model, _, supply, _ = make_model()

    voltage_result = model.set_power_supply_voltage(
        "main_supply",
        10.0,
    )
    current_result = model.set_power_supply_current(
        "main_supply",
        5.0,
    )
    output_result = model.set_power_supply_output(
        "main_supply",
        True,
    )

    assert voltage_result.succeeded is True
    assert current_result.succeeded is True
    assert output_result.succeeded is True
    assert supply.voltage_setpoint == 10.0
    assert supply.current_limit == 5.0
    assert supply.output_enabled is True


def test_manual_command_is_blocked_during_recipe() -> None:
    model, service, _, mfc = make_model()
    service.begin_recipe_control()

    result = model.set_mfc_flow("dry_gas_mfc", 25.0)

    assert result.succeeded is False
    assert "blocked while a recipe controls" in result.summary
    assert result.technical_details is not None
    assert "ControlAccessError" in result.technical_details
    assert mfc.flow_setpoint == 0.0


def test_safe_state_remains_available_during_recipe() -> None:
    model, service, _, mfc = make_model()

    model.set_mfc_flow("dry_gas_mfc", 25.0)
    service.begin_recipe_control()

    result = model.enter_safe_state("dry_gas_mfc")

    assert result.succeeded is True
    assert mfc.flow_setpoint == 0.0
    assert service.mode.value == "recipe_active"


def test_device_limit_error_is_returned_not_raised() -> None:
    model, _, _, mfc = make_model()

    result = model.set_mfc_flow("dry_gas_mfc", 101.0)

    assert result.succeeded is False
    assert "configured maximum" in result.summary
    assert result.technical_details is not None
    assert "ValueError" in result.technical_details
    assert mfc.flow_setpoint == 0.0


def test_disconnected_device_error_is_returned_not_raised() -> None:
    model, _, _, mfc = make_model()
    mfc.disconnect()

    result = model.set_mfc_flow("dry_gas_mfc", 25.0)

    assert result.succeeded is False
    assert "not ready" in result.summary
    assert result.technical_details is not None


def test_rows_include_actual_measurements() -> None:
    model, _, supply, mfc = make_model()

    supply.set_simulated_measurement(
        voltage=9.8,
        current=2.5,
    )
    mfc.set_simulated_measurement(24.7)

    mfc_row = model.mfc_rows()[0]
    supply_row = model.power_supply_rows()[0]

    assert mfc_row.is_available is True
    assert mfc_row.measured_flow == 24.7
    assert mfc_row.measurement_time is not None
    assert mfc_row.measurement_quality == "good"

    assert supply_row.is_available is True
    assert supply_row.measured_voltage == 9.8
    assert supply_row.measured_current == 2.5
    assert supply_row.voltage_measurement_time is not None
    assert supply_row.current_measurement_time is not None
    assert supply_row.voltage_quality == "good"
    assert supply_row.current_quality == "good"


def test_disconnected_devices_remain_visible() -> None:
    model, _, supply, mfc = make_model()

    supply.disconnect()
    mfc.disconnect()

    mfc_rows = model.mfc_rows()
    supply_rows = model.power_supply_rows()

    assert len(mfc_rows) == 1
    assert len(supply_rows) == 1

    assert mfc_rows[0].status == "disconnected"
    assert mfc_rows[0].is_available is False
    assert mfc_rows[0].measured_flow is None
    assert mfc_rows[0].measurement_time is None

    assert supply_rows[0].status == "disconnected"
    assert supply_rows[0].is_available is False
    assert supply_rows[0].measured_voltage is None
    assert supply_rows[0].measured_current is None
    assert supply_rows[0].voltage_measurement_time is None
    assert supply_rows[0].current_measurement_time is None


def test_measurement_failure_is_retained_as_error_event() -> None:
    model, _, supply, _ = make_model()

    def failed_voltage_read():
        raise OSError("Simulated communication failure")

    supply.measure_voltage = failed_voltage_read  # type: ignore[method-assign]

    row = model.power_supply_rows()[0]

    assert row.measured_voltage is None

    failures = model.read_failures

    assert len(failures) == 1
    assert failures[0].device_id == "main_supply"
    assert failures[0].measurement_name == "voltage"
    assert "OSError" in failures[0].summary
    assert "Simulated communication failure" in failures[0].summary
    assert "Traceback" in failures[0].technical_details
    assert failures[0].event.severity.value == "error"
    assert failures[0].event.source == "main_supply"


def test_repeated_measurement_failure_is_not_duplicated() -> None:
    model, _, supply, _ = make_model()

    def failed_voltage_read():
        raise OSError("Simulated communication failure")

    supply.measure_voltage = failed_voltage_read  # type: ignore[method-assign]

    model.power_supply_rows()
    model.power_supply_rows()
    model.power_supply_rows()

    assert len(model.read_failures) == 1
    assert len(model.events) == 1
    assert model.events[0].severity.value == "error"


def test_measurement_recovery_is_recorded_once() -> None:
    model, _, supply, _ = make_model()
    original_measure_voltage = supply.measure_voltage

    def failed_voltage_read():
        raise OSError("Simulated communication failure")

    supply.measure_voltage = failed_voltage_read  # type: ignore[method-assign]

    model.power_supply_rows()
    model.power_supply_rows()

    supply.measure_voltage = original_measure_voltage  # type: ignore[method-assign]

    model.power_supply_rows()
    model.power_supply_rows()

    assert len(model.read_failures) == 1
    assert len(model.events) == 2

    error_event = model.events[0]
    recovery_event = model.events[1]

    assert error_event.severity.value == "error"
    assert recovery_event.severity.value == "info"
    assert "recovered" in recovery_event.message

def test_global_safe_state_succeeds_for_all_devices() -> None:
    model, service, supply, mfc = make_model()

    supply.set_voltage(10.0)
    supply.set_current_limit(5.0)
    supply.set_output_enabled(True)
    mfc.set_flow_setpoint(25.0)

    result = model.enter_global_safe_state()

    assert result.succeeded is True
    assert result.technical_details is None
    assert "GLOBAL SAFE STATE REQUESTED" in result.summary

    assert supply.output_enabled is False
    assert supply.voltage_setpoint == 0.0
    assert supply.current_limit == 0.0
    assert mfc.flow_setpoint == 0.0
    assert service.mode.value == "idle"


def test_global_safe_state_reports_individual_failure() -> None:
    model, service, supply, mfc = make_model()

    mfc.set_flow_setpoint(25.0)

    def failed_safe_state():
        raise OSError("Simulated supply communication failure")

    supply.enter_safe_state = failed_safe_state  # type: ignore[method-assign]

    result = model.enter_global_safe_state()

    assert result.succeeded is False
    assert "FAILED" in result.summary
    assert "main_supply" in result.summary
    assert "Simulated supply communication failure" in result.summary
    assert result.technical_details is not None
    assert "Traceback" in result.technical_details

    # The MFC was still handled despite the supply failure.
    assert mfc.flow_setpoint == 0.0
    assert service.mode.value == "idle"

def test_power_supply_defaults_to_constant_current_mode() -> None:
    model, _, _, _ = make_model()

    row = model.power_supply_rows()[0]

    assert (
        row.operating_mode
        is PowerSupplyOperatingMode.CONSTANT_CURRENT
    )


def test_switching_to_constant_voltage_uses_safe_initial_target() -> None:
    model, _, supply, _ = make_model()

    model.set_power_supply_current(
        "main_supply",
        5.0,
    )

    result = model.set_power_supply_operating_mode(
        "main_supply",
        PowerSupplyOperatingMode.CONSTANT_VOLTAGE,
    )

    assert result.succeeded is True
    assert supply.voltage_setpoint == 0.0
    assert supply.current_limit == 108.0
    assert supply.output_enabled is False
    assert (
        model.power_supply_rows()[0].operating_mode
        is PowerSupplyOperatingMode.CONSTANT_VOLTAGE
    )


def test_switching_modes_restores_separate_targets() -> None:
    model, _, supply, _ = make_model()

    model.set_power_supply_current(
        "main_supply",
        15.0,
    )

    model.set_power_supply_operating_mode(
        "main_supply",
        PowerSupplyOperatingMode.CONSTANT_VOLTAGE,
    )
    model.set_power_supply_voltage(
        "main_supply",
        2.0,
    )

    model.set_power_supply_operating_mode(
        "main_supply",
        PowerSupplyOperatingMode.CONSTANT_CURRENT,
    )

    assert supply.current_limit == 15.0
    assert supply.voltage_setpoint == 30.0

    model.set_power_supply_operating_mode(
        "main_supply",
        PowerSupplyOperatingMode.CONSTANT_VOLTAGE,
    )

    assert supply.voltage_setpoint == 2.0
    assert supply.current_limit == 108.0

def test_manual_mode_cannot_change_while_output_is_enabled() -> None:
    model, _, supply, _ = make_model()

    model.set_power_supply_current(
        "main_supply",
        15.0,
    )
    supply.set_output_enabled(True)

    result = model.set_power_supply_operating_mode(
        "main_supply",
        PowerSupplyOperatingMode.CONSTANT_VOLTAGE,
    )

    assert result.succeeded is False
    assert "Disable the output first" in result.summary
    assert supply.output_enabled is True
    assert supply.current_limit == 15.0
    assert supply.voltage_setpoint == 0.0
    assert (
        model.power_supply_rows()[0].operating_mode
        is PowerSupplyOperatingMode.CONSTANT_CURRENT
    )

def test_supply_row_exposes_separate_remembered_targets() -> None:
    model, _, _, _ = make_model()

    model.set_power_supply_current(
        "main_supply",
        15.0,
    )
    model.set_power_supply_operating_mode(
        "main_supply",
        PowerSupplyOperatingMode.CONSTANT_VOLTAGE,
    )
    model.set_power_supply_voltage(
        "main_supply",
        2.0,
    )

    row = model.power_supply_rows()[0]

    assert row.constant_current_target == 15.0
    assert row.constant_voltage_target == 2.0

def test_manual_defaults_use_maximum_voltage_compliance() -> None:
    model, _, supply, _ = make_model()

    results = model.initialize_manual_power_supply_defaults()

    assert all(result.succeeded for result in results)
    assert supply.current_limit == 0.0
    assert supply.voltage_setpoint == 30.0
    assert supply.output_enabled is False

def test_constant_current_output_rejects_zero_voltage_limit() -> None:
    model, _, supply, _ = make_model()

    model.set_power_supply_current(
        "main_supply",
        15.0,
    )

    result = model.set_power_supply_output(
        "main_supply",
        True,
    )

    assert result.succeeded is False
    assert "voltage compliance limit is zero" in result.summary
    assert supply.output_enabled is False


def test_constant_voltage_output_rejects_zero_current_limit() -> None:
    model, _, supply, _ = make_model()

    model.set_power_supply_operating_mode(
        "main_supply",
        PowerSupplyOperatingMode.CONSTANT_VOLTAGE,
    )

    # Deliberately override the normal 108 A default compliance.
    model.set_power_supply_current(
        "main_supply",
        0.0,
    )
    model.set_power_supply_voltage(
        "main_supply",
        2.0,
    )

    result = model.set_power_supply_output(
        "main_supply",
        True,
    )

    assert result.succeeded is False
    assert "current compliance limit is zero" in result.summary
    assert supply.output_enabled is False