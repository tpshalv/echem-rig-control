"""Background recipe execution using the shared control service."""
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
import json
from pathlib import Path
from threading import Event as ThreadEvent, Lock, Thread
from time import monotonic, sleep

from rig_control.control.commands import (
    CommandSource, EnterDeviceSafeState, SetControllerOutput, SetHotplateHeating, SetHotplateSpeed,
    SetHotplateStirring, SetHotplateTemperature, SetMfcFlow, SetPowerSupplyCurrentLimit,
    SetPowerSupplyOutput, SetPowerSupplyVoltage, SetPressureSetpoint, SetPumpDirection,
    SetPumpRunning, SetPumpSpeed, SetTemperatureSetpoint,
)
from rig_control.control.service import RigControlService
from rig_control.data.experiment import ExperimentMetadata
from rig_control.devices.manager import DeviceManager
from rig_control.devices.mass_flow_controller import MassFlowController
from rig_control.devices.power_supply import PowerSupply, PowerSupplyOperatingMode
from rig_control.devices.pressure_controller import PressureController
from rig_control.devices.pump import Pump
from rig_control.devices.lumel_re72 import LumelRe72
from rig_control.devices.ohaus_guardian_5000.driver import OhausGuardian5000
from rig_control.devices.esp32_controller import Esp32Controller
from rig_control.devices.pump import PumpDirection
from rig_control.experiment_recording import ExperimentRecorder
from rig_control.models import Event, EventSeverity
from rig_control.devices.safe_state import SafeStateCapable
from rig_control.recipes.model import (
    EndAction, Assignment, LoopStep, Recipe, RepeatStep, WaitStep, iter_steps, power_supply_mode_of, recipe_to_dict,
)
from rig_control.recipes.plan import PlannedRamp, PlannedSet, PlannedWait, plan, recipe_problems
from rig_control.recipes.preview import estimate_recipe


class RecipeRunState(StrEnum):
    IDLE = "idle"
    RUNNING = "running"
    COMPLETED = "completed"
    STOPPED = "stopped"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class RecipeRunStatus:
    state: RecipeRunState
    active_step: str | None = None
    #: Loop position, outermost first, e.g. ``("current setpoint 2 A (2/3)",)``.
    position: tuple[str, ...] = ()
    elapsed_seconds: float = 0.0
    estimated_remaining_seconds: float = 0.0
    failure: str | None = None


@dataclass(frozen=True, slots=True)
class RecipeRunResult:
    state: RecipeRunState
    failure: str | None = None
    safe_state_failures: tuple[str, ...] = ()


class RecipeValidationError(ValueError):
    pass


class RecipeRunner:
    """Own one recipe thread. Stop is cooperative and leaves safe state to control."""
    def __init__(self, control_service: RigControlService, device_manager: DeviceManager, *,
                 recorder: ExperimentRecorder | None = None, event_sink: Callable[[Event], None] | None = None,
                 sleeper: Callable[[float], None] = sleep, clock: Callable[[], float] = monotonic) -> None:
        self._control = control_service; self._devices = device_manager; self._recorder = recorder
        self._event_sink = event_sink; self._sleeper = sleeper; self._clock = clock
        self._cancel = ThreadEvent(); self._lock = Lock(); self._thread: Thread | None = None
        self._status = RecipeRunStatus(RecipeRunState.IDLE); self._result: RecipeRunResult | None = None
        self._started_at = 0.0; self._estimated_seconds = 0.0
        self._supply_modes: dict[str, PowerSupplyOperatingMode] = {}

    @property
    def is_running(self) -> bool: return self.status.state is RecipeRunState.RUNNING
    @property
    def status(self) -> RecipeRunStatus:
        with self._lock: return self._status
    @property
    def result(self) -> RecipeRunResult | None:
        with self._lock: return self._result

    def validate(self, recipe: Recipe) -> None:
        """Construct every command before taking ownership; drivers enforce limits."""
        for step in iter_steps(recipe.steps):
            if isinstance(step, (LoopStep, RepeatStep)) and not step.steps:
                name = "Repeat" if isinstance(step, RepeatStep) else f"Loop over {step.device_id}.{step.setting}"
                raise RecipeValidationError(f"{name} has no steps inside it")
        problems = recipe_problems(recipe)
        if problems:
            raise RecipeValidationError(problems[0])
        # Every value the run will write, ramps included, checked once each.
        writes = {(a.device_id, a.setting, a.value): a for item in plan(recipe.steps)
                  if isinstance(item, PlannedSet) for a in item.writes}
        # The end state is checked like any step: it must be possible before the run starts.
        writes.update(((a.device_id, a.setting, a.value), a) for d in recipe.end_state.devices for a in d.assignments)
        for entry in recipe.end_state.devices:
            try:
                self._devices.get(entry.device_id)
            except Exception as error:
                raise RecipeValidationError(f"End state: {entry.device_id}: {error}") from error
        for write in writes.values():
            try:
                device = self._devices.get(write.device_id)
                self._command(write)
                self._validate_capability_and_limits(device, write)
            except Exception as error:
                raise RecipeValidationError(f"Invalid {write.device_id}.{write.setting}: {error}") from error

    def start(self, recipe: Recipe, *, recording_root: str | Path | None = None,
              experiment_id: str | None = None, operator: str = "recipe", notes: str | None = None) -> None:
        self.validate(recipe)
        with self._lock:
            if self._thread is not None and self._thread.is_alive(): raise RuntimeError("A recipe is already running")
            if self._control.mode.value != "idle":
                raise RuntimeError("Recipe control is unavailable while another control mode is active")
            if self._recorder is not None and self._recorder.is_recording: raise RuntimeError("An experiment is already being recorded; recipe will not replace it")
            self._cancel.clear(); self._result = None; self._supply_modes = {}
            estimate = estimate_recipe(recipe)
            self._started_at = self._clock(); self._estimated_seconds = estimate.duration_seconds
            self._status = RecipeRunStatus(RecipeRunState.RUNNING, estimated_remaining_seconds=estimate.duration_seconds)
            self._thread = Thread(target=self._run, args=(recipe, recording_root, experiment_id, operator, notes), name="recipe-runner", daemon=True)
            self._thread.start()

    def stop(self) -> None: self._cancel.set()
    def join(self, timeout: float | None = None) -> RecipeRunResult | None:
        thread = self._thread
        if thread is not None: thread.join(timeout)
        return self.result

    def _run(self, recipe: Recipe, root: str | Path | None, experiment_id: str | None, operator: str, notes: str | None) -> None:
        started_recording = False; safe_failures: tuple[str, ...] = (); failure: str | None = None; state = RecipeRunState.COMPLETED; started = self._clock()
        try:
            if self._recorder is not None:
                if root is None: raise RuntimeError("Recipe recording needs an output folder")
                extra = {"recipe_snapshot_json": json.dumps(recipe_to_dict(recipe), sort_keys=True), "recipe_name": recipe.name}
                self._recorder.start(metadata=ExperimentMetadata(experiment_id=experiment_id or recipe.name, operator=operator, notes=notes,
                    experiment_type="recipe", extra=extra), root_directory=root)
                started_recording = True
            self._control.begin_recipe_control()
            self._emit("recipe_started", recipe.name)
            self._execute(recipe)
            # Finishing, Stop and failure all leave the rig in the recipe's
            # end state.  Stop skips every remaining step, ramps and holds
            # included; the rig's global safe state stays for emergencies.
            safe_failures = self._apply_end_state(recipe, "completed")
        except _Cancelled:
            state = RecipeRunState.STOPPED; self._emit("recipe_stopped", "Operator requested Stop")
            safe_failures = self._apply_end_state(recipe, "stopped")
        except Exception as error:
            state = RecipeRunState.FAILED; failure = f"{type(error).__name__}: {error}"; self._emit("recipe_failed", failure, EventSeverity.ERROR)
            safe_failures = self._apply_end_state(recipe, "failed")
        finally:
            if self._control.mode.value == "recipe_active":
                try: self._control.end_recipe_control()
                except Exception as error: failure = failure or f"Could not release recipe control: {error}"; state = RecipeRunState.FAILED
            if started_recording:
                try: self._recorder.stop()  # type: ignore[union-attr]
                except Exception as error: failure = failure or f"Could not stop recording: {error}"; state = RecipeRunState.FAILED
            elapsed = self._clock() - started
            result = RecipeRunResult(state, failure, safe_failures)
            with self._lock:
                self._result = result; self._status = RecipeRunStatus(state, elapsed_seconds=elapsed, failure=failure)

    def _execute(self, recipe: Recipe) -> None:
        """Run the plan item by item: exactly what the timeline and estimate show."""
        for item in plan(recipe.steps):
            self._check_cancel()
            if isinstance(item, PlannedSet):
                if item.name and not item.ramping:
                    self._emit("recipe_set", item.name, position=list(item.position))
                for assignment in item.writes:
                    self._execute_set(assignment, item.position, "Ramp" if item.ramping else item.name)
            elif isinstance(item, PlannedWait):
                # Waits, Set holds and ramp holds share one path: same events, status and Stop.
                self._wait(item.wait, item.position)
            elif isinstance(item, PlannedRamp):
                self._emit("recipe_ramp", f"{item.target.device_id}.{item.target.setting}", start=item.start,
                           target=item.target.value, ramp_kind=item.ramp.kind.value, steps=item.steps,
                           position=list(item.position))

    def _wait(self, step: WaitStep, position: tuple[str, ...]) -> None:
        self._set_status(step.title, position)
        # ``purpose`` lets recorded data separate settling from measuring.
        self._emit("recipe_wait", step.title, purpose=step.purpose.value,
                   duration_seconds=step.duration_seconds, position=list(position))
        remaining = step.duration_seconds
        while remaining > 0:
            self._check_cancel(); period = min(remaining, 0.1); self._sleeper(period); remaining -= period

    def _execute_set(self, step: Assignment, position: tuple[str, ...], name: str = "") -> None:
        self._check_cancel()
        mode = power_supply_mode_of(step.setting)
        if mode is not None and self._supply_modes.get(step.device_id) is not mode:
            # Intended mode only; actual CC/CV is the polled regulation_mode channel.
            self._supply_modes[step.device_id] = mode
            self._emit("power_supply_mode", f"{step.device_id}={mode.value}")
        self._set_status(name or f"Set {step.setting}", position)
        self._control.execute(self._command(step))
        self._emit("recipe_command", f"{step.device_id}.{step.setting}={step.value}")

    def _command(self, p: Assignment):
        source = CommandSource.RECIPE; setting = p.setting; value = p.value
        if setting == "flow": return SetMfcFlow(p.device_id, float(value), source, p.unit)
        if setting == "pressure": return SetPressureSetpoint(p.device_id, float(value), source, p.unit or "bara")
        if setting == "voltage": return SetPowerSupplyVoltage(p.device_id, float(value), source)
        if setting == "current_limit": return SetPowerSupplyCurrentLimit(p.device_id, float(value), source)
        # CC names drive the same two registers as CV, with the roles swapped.
        if setting == "current_setpoint": return SetPowerSupplyCurrentLimit(p.device_id, float(value), source)
        if setting == "voltage_limit": return SetPowerSupplyVoltage(p.device_id, float(value), source)
        if setting == "output_enabled": return SetPowerSupplyOutput(p.device_id, bool(value), source)
        if setting == "temperature_setpoint": return SetTemperatureSetpoint(p.device_id, float(value), source)
        if setting == "pump_speed": return SetPumpSpeed(p.device_id, float(value), source)
        if setting == "pump_direction": return SetPumpDirection(p.device_id, PumpDirection(str(value)), source)
        if setting == "pump_running": return SetPumpRunning(p.device_id, bool(value), source)
        if setting == "hotplate_temperature": return SetHotplateTemperature(p.device_id, float(value), source)
        if setting == "hotplate_speed": return SetHotplateSpeed(p.device_id, float(value), source)
        if setting == "hotplate_heating": return SetHotplateHeating(p.device_id, bool(value), source)
        if setting == "hotplate_stirring": return SetHotplateStirring(p.device_id, bool(value), source)
        if setting.startswith("controller_output:"): return SetControllerOutput(p.device_id, setting.split(":", 1)[1], bool(value), source)
        raise ValueError("Unsupported recipe setting; use an explicit supported setting")
    @staticmethod
    def _validate_capability_and_limits(device: object, step: Assignment) -> None:
        setting = step.setting
        required = {
            "flow": MassFlowController, "pressure": PressureController,
            "voltage": PowerSupply, "current_limit": PowerSupply, "output_enabled": PowerSupply,
            "current_setpoint": PowerSupply, "voltage_limit": PowerSupply,
            "temperature_setpoint": LumelRe72, "pump_speed": Pump, "pump_direction": Pump,
            "pump_running": Pump, "hotplate_temperature": OhausGuardian5000,
            "hotplate_speed": OhausGuardian5000, "hotplate_heating": OhausGuardian5000,
            "hotplate_stirring": OhausGuardian5000,
        }
        expected = Esp32Controller if setting.startswith("controller_output:") else required.get(setting)
        if expected is None or not isinstance(device, expected):
            name = expected.__name__ if expected is not None else "a supported device"
            raise ValueError(f"Setting {setting!r} requires {name}")
        value = step.value
        limits = getattr(device, "limits", None)
        maximums = {"flow": "maximum_flow", "voltage": "maximum_voltage", "current_limit": "maximum_current",
                    "voltage_limit": "maximum_voltage", "current_setpoint": "maximum_current"}
        maximum = getattr(limits, maximums.get(setting, ""), None)
        if maximum is not None and isinstance(value, (int, float)) and float(value) > maximum:
            raise ValueError(f"{setting} {value:g} exceeds configured maximum {maximum:g}")
    def _check_cancel(self) -> None:
        if self._cancel.is_set(): raise _Cancelled()
    def _set_status(self, label: str, position: tuple[str, ...]) -> None:
        elapsed = self._clock() - self._started_at
        with self._lock:
            self._status = RecipeRunStatus(
                RecipeRunState.RUNNING, label, position, elapsed, max(0.0, self._estimated_seconds - elapsed),
            )
    def _apply_end_state(self, recipe: Recipe, reason: str) -> tuple[str, ...]:
        """Leave each device as the end state says; any device not listed goes to safe state.

        Every device is attempted even if another fails, sources first and
        pressure controllers (the outlet) last, as in the global safe state.
        A device whose end-state settings cannot be written falls back to its
        own safe state.  If control was taken away mid-run (global safe state
        or fault lock), recipe writes are refused, so those devices fall back
        too: an emergency always wins.
        """
        end = recipe.end_state
        self._emit("recipe_end_state", reason, devices={
            d.device_id: d.action.value for d in end.devices})
        failures: list[str] = []
        order = sorted(self._devices.device_ids,
                       key=lambda device_id: isinstance(self._devices.get(device_id), PressureController))
        for device_id in order:
            entry = end.for_device(device_id)
            if entry.action is EndAction.LEAVE:
                continue
            if entry.action is EndAction.SET:
                try:
                    for assignment in entry.assignments:
                        self._control.execute(self._command(assignment))
                        self._emit("recipe_command", f"{device_id}.{assignment.setting}={assignment.value}")
                    continue
                except Exception as error:
                    failures.append(f"{device_id}: end-state settings failed ({type(error).__name__}: {error}); "
                                    "safe state requested instead")
            if not isinstance(self._devices.get(device_id), SafeStateCapable):
                continue
            try:
                self._control.execute(EnterDeviceSafeState(device_id, CommandSource.SAFETY_SYSTEM))
            except Exception as error:
                failures.append(f"Safe-state request failed for {device_id!r}: {type(error).__name__}: {error}")
        if failures:
            self._emit("recipe_end_state_failed", "; ".join(failures), EventSeverity.ERROR)
        return tuple(failures)
    def _emit(self, kind: str, detail: str, severity: EventSeverity = EventSeverity.INFO, **extra: object) -> None:
        event = Event(source="recipe", severity=severity, message=json.dumps({"kind": kind, "detail": detail, **extra}))
        if self._event_sink is not None: self._event_sink(event)
        elif self._recorder is not None: self._recorder.record_event(event)


class _Cancelled(Exception): pass
