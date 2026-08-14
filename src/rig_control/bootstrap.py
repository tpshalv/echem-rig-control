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


def create_simulated_device_manager() -> DeviceManager:
    """Create a safe demonstration rig using no physical hardware."""

    manager = DeviceManager()

    manager.register(
        SimulatedPowerSupply(
            device_id="main_power_supply",
            limits=PowerSupplyLimits(
                maximum_voltage=30.0,
                maximum_current=108.0,
                maximum_power=1080.0,
            ),
        )
    )

    manager.register(
        SimulatedMassFlowController(
            device_id="dry_gas_mfc",
            limits=MassFlowControllerLimits(
                maximum_flow=100.0,
                flow_unit="sccm",
            ),
        )
    )

    manager.register(
        SimulatedMassFlowController(
            device_id="wet_gas_mfc",
            limits=MassFlowControllerLimits(
                maximum_flow=100.0,
                flow_unit="sccm",
            ),
        )
    )

    return manager