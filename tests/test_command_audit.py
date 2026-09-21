import json
from contextlib import nullcontext
from unittest.mock import Mock

import pytest

from rig_control.control.commands import CommandSource, SetPowerSupplyVoltage
from rig_control.control.service import ControlAccessError, ControlExecutionError, RigControlService
from rig_control.devices.power_supply import PowerSupply


def make_service(sink):
    supply = Mock(spec=PowerSupply)
    manager = Mock()
    manager.get.return_value = supply
    manager.operation.return_value = nullcontext(supply)
    return RigControlService(manager, event_sink=sink), supply


@pytest.mark.parametrize("outcome", ["succeeded", "blocked", "failed"])
def test_command_audit_captures_parameters_source_and_outcome(outcome):
    events = []
    service, supply = make_service(lambda event, _details: events.append(event))
    command = SetPowerSupplyVoltage("supply", 2.5, CommandSource.MANUAL)
    if outcome == "blocked":
        service.begin_recipe_control()
        with pytest.raises(ControlAccessError):
            service.execute(command)
        supply.set_voltage.assert_not_called()
    elif outcome == "failed":
        supply.set_voltage.side_effect = OSError("connection lost")
        with pytest.raises(ControlExecutionError):
            service.execute(command)
    else:
        service.execute(command)
        supply.set_voltage.assert_called_once_with(2.5)
    assert len(events) == 1
    event = events[0]
    audit = json.loads(event.message)
    assert event.source == "supply"
    assert audit["command"] == "SetPowerSupplyVoltage"
    assert audit["parameters"] == {"device_id": "supply", "voltage": 2.5, "source": "manual"}
    assert audit["outcome"] == outcome


def test_audit_failure_reports_that_the_hardware_command_already_succeeded():
    service, supply = make_service(Mock(side_effect=OSError("disk full")))
    result = service.execute(SetPowerSupplyVoltage("supply", 2.5, CommandSource.MANUAL))
    supply.set_voltage.assert_called_once_with(2.5)
    assert "command executed, but audit recording failed" in result.message
    assert "disk full" in service.audit_failures[0]
    assert service.history == (result,)
