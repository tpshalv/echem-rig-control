"""Background recipe execution using the shared control service."""
from collections import deque
from collections.abc import Callable, Iterator
from concurrent.futures import Future, TimeoutError as FutureTimeout
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
import json
from pathlib import Path
from secrets import randbelow
from threading import Event as ThreadEvent, Lock, Thread
from time import monotonic, sleep

from rig_control.cell_area import CellArea, CellAreaMissing
from rig_control.control.commands import (
    CommandSource, EnterDeviceSafeState, SetControllerOutput, SetHotplateHeating, SetHotplateSpeed,
    SetHotplateStirring, SetHotplateTemperature, SetMfcFlow, SetPowerSupplyCurrentLimit,
    SetPowerSupplyOutput, SetPowerSupplyVoltage, SetPressureSetpoint, SetPumpDirection,
    SetPumpRunning, SetPumpSpeed, SetTemperatureSetpoint,
)
from rig_control.control.service import RigControlService
from rig_control.data.experiment import ExperimentMetadata
from rig_control.data.experiment_naming import existing_recording, make_experiment_id, remove_recording
from rig_control.devices.manager import DeviceManager
from rig_control.devices.mass_flow_controller import MassFlowController, convert_mass_flow
from rig_control.devices.power_supply import PowerSupply, PowerSupplyOperatingMode
from rig_control.devices.pressure_controller import PressureController
from rig_control.devices.pump import Pump
from rig_control.devices.lumel_re72 import LumelRe72
from rig_control.devices.ohaus_guardian_5000.driver import OhausGuardian5000
from rig_control.devices.esp32_controller import Esp32Controller
from rig_control.devices.pump import PumpDirection
from rig_control.experiment_recording import ExperimentRecorder
from rig_control.instrument_settings.calibration import CalibrationService, PumpCalibrationService
from rig_control.models import Event, EventSeverity
from rig_control.devices.safe_state import SafeStateCapable
from rig_control.recipes.model import (
    CURRENT_DENSITY_SETTING, EndAction, Assignment, LoopStep, Recipe, RepeatStep, WaitStep, iter_steps, power_supply_mode_of, recipe_to_dict,
)
from rig_control.recipes.plan import (
    PlanItem, PlannedLoopOrder, PlannedRamp, PlannedSet, PlannedWait, SupplySequence, plan, recipe_problems,
)
from rig_control.recipes.preview import estimate_recipe
from rig_control.recipes.revision import (
    Revision, RevisionChanges, RevisionRefused, RunSnapshot, prepare_revision, structure_problems,
)


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
    #: How many times the running recipe has been revised.
    revision: int = 0


@dataclass(frozen=True, slots=True)
class RecipeRunResult:
    state: RecipeRunState
    failure: str | None = None
    safe_state_failures: tuple[str, ...] = ()


class RecipeValidationError(ValueError):
    pass


class _MovedOn(Exception):
    """The run started another item while a revision was being checked."""


#: Order seeds are drawn below this, so they stay short enough to read and type.
ORDER_SEED_LIMIT = 1_000_000


def new_order_seed() -> int:
    """A fresh seed for the order of shuffled loops."""
    return randbelow(ORDER_SEED_LIMIT)


class RecipeRunner:
    """Own one recipe thread. Stop is cooperative and leaves safe state to control."""
    def __init__(self, control_service: RigControlService, device_manager: DeviceManager, *,
                 recorder: ExperimentRecorder | None = None, event_sink: Callable[[Event], None] | None = None,
                 sleeper: Callable[[float], None] = sleep, clock: Callable[[], float] = monotonic,
                 pump_calibrations: CalibrationService | None = None, cell_area: CellArea | None = None) -> None:
        self._control = control_service; self._devices = device_manager; self._recorder = recorder
        self._pump_calibrations = pump_calibrations or PumpCalibrationService()
        self._cell_area = cell_area or CellArea()
        self._event_sink = event_sink; self._sleeper = sleeper; self._clock = clock
        self._cancel = ThreadEvent(); self._lock = Lock(); self._thread: Thread | None = None
        self._status = RecipeRunStatus(RecipeRunState.IDLE); self._result: RecipeRunResult | None = None
        self._started_at = 0.0; self._estimated_seconds = 0.0; self._order_seed = 0
        self._supply_modes: dict[str, PowerSupplyOperatingMode] = {}
        self._reset_position(None)
        #: Work handed to the run's thread, done between items or during a wait.
        self._requests: deque[tuple[Callable[[], object], Future]] = deque()

    def _reset_position(self, recipe: Recipe | None) -> None:
        """Where the run is, read by ``snapshot`` from other threads (under the lock)."""
        self._recipe = recipe; self._revision = 0
        self._items: Iterator[PlanItem] = iter(())
        #: The running step run's items not yet started; then the first of the next.
        self._rest: deque[PlanItem] = deque(); self._lookahead: PlanItem | None = None
        self._counter = 0; self._where = None; self._trace = ()
        self._written: dict[tuple[str, str], object] = {}; self._supply = SupplySequence()
        self._wait_left = 0.0

    @property
    def cell_area(self) -> CellArea: return self._cell_area
    @property
    def is_running(self) -> bool: return self.status.state is RecipeRunState.RUNNING
    @property
    def status(self) -> RecipeRunStatus:
        with self._lock: return self._status
    @property
    def result(self) -> RecipeRunResult | None:
        with self._lock: return self._result

    def has_safe_state(self, device_id: str) -> bool | None:
        """Whether a device in this session can enter a safe state; None if it is not in the session."""
        try:
            return isinstance(self._devices.get(device_id), SafeStateCapable)
        except Exception:
            return None

    def validate(self, recipe: Recipe, *, order_seed: int = 0) -> None:
        """Construct every command before taking ownership; drivers enforce limits.

        Shuffled loops are checked in the order ``order_seed`` gives, which
        is the order a run started with that seed follows.
        """
        problems = structure_problems(recipe) or recipe_problems(recipe, seed=order_seed)
        if problems:
            raise RecipeValidationError(problems[0])
        # Every value the run will write, ramps included, checked once each.
        self._check_writes([a for item in plan(recipe.steps, seed=order_seed)
                            if isinstance(item, PlannedSet) for a in item.writes], recipe)

    def _check_writes(self, writes, recipe: Recipe) -> None:
        """Each distinct write, and the end state, must be possible for the connected devices."""
        unique = {(a.device_id, a.setting, a.value): a for a in writes}
        # The end state is checked like any step: it must be possible before the run starts.
        unique.update(((a.device_id, a.setting, a.value), a) for d in recipe.end_state.devices for a in d.assignments)
        for entry in recipe.end_state.devices:
            try:
                self._devices.get(entry.device_id)
            except Exception as error:
                raise RecipeValidationError(f"End state: {entry.device_id}: {error}") from error
        for write in unique.values():
            try:
                device = self._devices.get(write.device_id)
                self._command(write)
                self._validate_capability_and_limits(device, write)
            except CellAreaMissing as error:
                raise RecipeValidationError(str(error)) from error
            except Exception as error:
                raise RecipeValidationError(f"Invalid {write.device_id}.{write.setting}: {error}") from error

    def start(self, recipe: Recipe, *, recording_root: str | Path | None = None,
              metadata: ExperimentMetadata | None = None, replace_existing: bool = False,
              order_seed: int | None = None) -> None:
        """Run in the background, recording into ``recording_root``.

        ``metadata`` comes from Operation's ``plan_recording``, so a recipe run
        is named and filed exactly like a manual one; the recipe itself is
        added to it. Without it (scripts, tests) the operator is "recipe".
        ``replace_existing`` deletes a same-named recording, only once every
        check has passed.
        ``order_seed`` sets the order of shuffled loops; without one a fresh
        seed is drawn.  It is recorded, so the same order can be run again.
        """
        if order_seed is None:
            order_seed = new_order_seed()
        self.validate(recipe, order_seed=order_seed)
        with self._lock:
            if self._thread is not None and self._thread.is_alive(): raise RuntimeError("A recipe is already running")
            if self._control.mode.value != "idle":
                raise RuntimeError("Recipe control is unavailable while another control mode is active")
            if self._recorder is not None and self._recorder.is_recording: raise RuntimeError("An experiment is already being recorded; recipe will not replace it")
            if replace_existing and metadata is not None and recording_root is not None:
                existing = existing_recording(recording_root, metadata.experiment_id)
                if existing is not None: remove_recording(existing)
            self._cancel.clear(); self._result = None; self._supply_modes = {}; self._order_seed = order_seed
            self._reset_position(recipe); self._items = plan(recipe.steps, seed=order_seed)
            estimate = estimate_recipe(recipe, seed=order_seed)
            self._started_at = self._clock(); self._estimated_seconds = estimate.duration_seconds
            self._status = RecipeRunStatus(RecipeRunState.RUNNING, estimated_remaining_seconds=estimate.duration_seconds)
            self._thread = Thread(target=self._run, args=(recipe, recording_root, metadata),
                                  name="recipe-runner", daemon=True)
            self._thread.start()

    def stop(self) -> None: self._cancel.set()

    # --- revising the running recipe ---

    @property
    def active_recipe(self) -> Recipe | None:
        """The recipe the run is following now, revisions included; None when idle."""
        with self._lock:
            return self._recipe if self._status.state is RecipeRunState.RUNNING else None

    def snapshot(self) -> RunSnapshot:
        """The run as it stands, for working out a revision or what is left."""
        with self._lock:
            if self._recipe is None or self._status.state is not RecipeRunState.RUNNING:
                raise RuntimeError("No recipe is running")
            return RunSnapshot(self._recipe, self._order_seed, self._counter, self._where, self._trace,
                               tuple(self._rest), dict(self._written), self._supply.copy(), self._wait_left,
                               self._revision)

    def revise(self, recipe: Recipe, *, timeout: float = 10.0) -> RevisionChanges:
        """Replace what the run has still to do with ``recipe``'s; see ``rig_control.recipes.revision``.

        Checked here, then swapped in by the run's own thread before its next
        item (or during a wait), so the run never sees half a change.  If the
        run moves on meanwhile, the check is done again.  Raises
        ``RevisionRefused`` with the reasons, or ``RuntimeError`` if no run.
        """
        for _attempt in range(5):
            revision = self._prepare(recipe)
            future = self._submit(lambda revision=revision: self._apply_revision(revision))
            try:
                return future.result(timeout)  # type: ignore[return-value]
            except _MovedOn:
                continue
            except FutureTimeout:
                raise RevisionRefused(("The run did not take the change in time (a device may be slow "
                                       "to answer); nothing was changed. Try again.",)) from None
        raise RevisionRefused(("The run kept moving on while the change was checked; try again.",))

    def check_revision(self, recipe: Recipe) -> RevisionChanges:
        """What ``revise`` would change now, without changing anything; raises as it does."""
        return self._prepare(recipe).changes

    def _prepare(self, recipe: Recipe) -> Revision:
        revision = prepare_revision(self.snapshot(), recipe)
        try:
            self._check_writes(revision.writes, recipe)
        except RecipeValidationError as error:
            raise RevisionRefused((str(error),)) from error
        return revision

    def _submit(self, work: Callable[[], object]) -> Future:
        future: Future = Future()
        with self._lock:
            if self._thread is None or not self._thread.is_alive() or self._status.state is not RecipeRunState.RUNNING:
                raise RuntimeError("No recipe is running")
            self._requests.append((work, future))
        return future

    def _serve_requests(self) -> None:
        """Run work handed over by other threads; only ever called on the run's thread."""
        while True:
            with self._lock:
                if not self._requests:
                    return
                work, future = self._requests.popleft()
            if not future.set_running_or_notify_cancel():
                continue
            try:
                future.set_result(work())
            except BaseException as error:  # Handed back to the caller, never lost.
                future.set_exception(error)

    def _drop_requests(self) -> None:
        with self._lock:
            pending, self._requests = list(self._requests), deque()
        for _work, future in pending:
            if future.set_running_or_notify_cancel():
                future.set_exception(RuntimeError("The run has ended"))

    def _apply_revision(self, revision: Revision) -> RevisionChanges:
        if revision.based_on != self._counter:
            raise _MovedOn()
        # Read only once the running step has finished; see ``resume_plan``.
        items = revision.items(lambda: dict(self._written))
        elapsed = self._clock() - self._started_at
        with self._lock:
            self._items, self._lookahead = items, None
            self._recipe = revision.recipe; self._revision += 1
            self._estimated_seconds = elapsed + revision.changes.remaining_seconds
            self._status = replace(self._status, revision=self._revision,
                                   estimated_remaining_seconds=revision.changes.remaining_seconds)
        changes = revision.changes
        self._emit("recipe_revised", f"revision {self._revision}", revision=self._revision,
                   position=list(self._status.position), changed=list(changes.changed), added=list(changes.added),
                   removed=list(changes.removed), running_changed=changes.running_changed,
                   end_state_changed=changes.end_state_changed,
                   recipe_snapshot_json=json.dumps(recipe_to_dict(revision.recipe), sort_keys=True))
        return changes
    def join(self, timeout: float | None = None) -> RecipeRunResult | None:
        thread = self._thread
        if thread is not None: thread.join(timeout)
        return self.result

    def _run(self, recipe: Recipe, root: str | Path | None, metadata: ExperimentMetadata | None) -> None:
        started_recording = False; safe_failures: tuple[str, ...] = (); failure: str | None = None; state = RecipeRunState.COMPLETED; started = self._clock()
        try:
            if self._recorder is not None:
                if root is None: raise RuntimeError("Recipe recording needs an output folder")
                if metadata is None:
                    metadata = ExperimentMetadata(make_experiment_id("recipe", datetime.now(UTC)), "recipe")
                extra = {"recipe_snapshot_json": json.dumps(recipe_to_dict(recipe), sort_keys=True), "recipe_name": recipe.name,
                         "recipe_order_seed": self._order_seed}
                self._recorder.start(metadata=replace(metadata, experiment_type="recipe",
                                                      extra={**metadata.extra, **extra}), root_directory=root)
                started_recording = True
            self._control.begin_recipe_control()
            self._emit("recipe_started", recipe.name, order_seed=self._order_seed)
            self._execute()
            # Finishing, Stop and failure all leave the rig in the recipe's
            # end state (as last revised).  Stop skips every remaining step,
            # ramps and holds included; the rig's global safe state stays for
            # emergencies.
            self._drop_requests()
            safe_failures = self._apply_end_state(self._active(recipe), "completed")
        except _Cancelled:
            self._drop_requests()
            state = RecipeRunState.STOPPED; self._emit("recipe_stopped", "Operator requested Stop")
            safe_failures = self._apply_end_state(self._active(recipe), "stopped")
        except Exception as error:
            self._drop_requests()
            state = RecipeRunState.FAILED; failure = f"{type(error).__name__}: {error}"; self._emit("recipe_failed", failure, EventSeverity.ERROR)
            safe_failures = self._apply_end_state(self._active(recipe), "failed")
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
                self._result = result
                self._status = RecipeRunStatus(state, elapsed_seconds=elapsed, failure=failure, revision=self._revision)
            self._drop_requests()  # Anything handed over while the end state applied.

    def _active(self, started: Recipe) -> Recipe:
        with self._lock:
            return self._recipe or started

    def _next_item(self) -> PlanItem | None:
        """The next item, keeping ``_rest`` as the running step run's unstarted items.

        A step run's items are taken from the plan together when it starts,
        so a revision can leave the rest of it as it started.
        """
        if not self._rest:
            first = self._lookahead if self._lookahead is not None else next(self._items, None)
            if first is None:
                return None
            run, lookahead = [first], None
            for item in self._items:
                if item.where != first.where:
                    lookahead = item
                    break
                run.append(item)
            with self._lock:
                self._rest, self._lookahead = deque(run), lookahead
        with self._lock:
            item = self._rest.popleft()
            self._counter += 1; self._where = item.where; self._trace = item.trace
        return item

    def _execute(self) -> None:
        """Run the plan item by item: exactly what the timeline and estimate show."""
        while True:
            self._check_cancel()
            self._serve_requests()
            item = self._next_item()
            if item is None:
                return
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
            elif isinstance(item, PlannedLoopOrder):
                loop = item.loop
                self._emit("recipe_loop_order", loop.name or f"{loop.device_id}.{loop.setting}",
                           order=loop.order.value, values=list(item.values),
                           pass_names=[loop.pass_name(i) for i in item.order] if loop.pass_names else [],
                           position=list(item.position))

    def _wait(self, step: WaitStep, position: tuple[str, ...]) -> None:
        self._set_status(step.title, position)
        # ``purpose`` lets recorded data separate settling from measuring.
        self._emit("recipe_wait", step.title, purpose=step.purpose.value,
                   duration_seconds=step.duration_seconds, position=list(position))
        remaining = step.duration_seconds
        while remaining > 0:
            self._check_cancel()
            with self._lock:
                self._wait_left = remaining
            self._serve_requests()
            period = min(remaining, 0.1); self._sleeper(period); remaining -= period
        with self._lock:
            self._wait_left = 0.0

    def _execute_set(self, step: Assignment, position: tuple[str, ...], name: str = "") -> None:
        self._check_cancel()
        mode = power_supply_mode_of(step.setting)
        if mode is not None and self._supply_modes.get(step.device_id) is not mode:
            # Intended mode only; actual CC/CV is the polled regulation_mode channel.
            self._supply_modes[step.device_id] = mode
            self._emit("power_supply_mode", f"{step.device_id}={mode.value}")
        self._set_status(name or f"Set {step.setting}", position)
        command = self._command(step)
        self._control.execute(command)
        with self._lock:
            self._written[step.key] = step.value
            self._supply.write(step)
        # A flow is sent as rpm; record which rpm and calibration it became.
        extra = ({"rpm": command.rpm, "calibration": self._pump_calibration(step.device_id).name}
                 if step.setting == "pump_flow" else
                 # Likewise a density is sent as amperes.
                 {"amps": command.current, "area_cm2": self._cell_area.cm2}
                 if step.setting == CURRENT_DENSITY_SETTING else {})
        self._emit("recipe_command", f"{step.device_id}.{step.setting}={step.value}", **extra)

    def _pump_calibration(self, device_id: str):
        calibration = self._pump_calibrations.active_calibration(device_id)
        if calibration is None:
            raise ValueError(f"pump {device_id!r} has no accepted calibration; calibrate it in "
                             "Settings or set its speed in rpm")
        return calibration

    def _rpm_for_flow(self, device_id: str, flow_ml_per_min: float) -> float:
        """Convert with the pump's calibration as it is when the step runs."""
        if flow_ml_per_min == 0:
            return 0.0  # Off, even when the calibration line does not pass through zero.
        calibration = self._pump_calibration(device_id)
        rpm = round(calibration.fit.x_for_y(flow_ml_per_min), 2)
        if rpm < 0:
            raise ValueError(f"{flow_ml_per_min:g} mL/min is below what calibration {calibration.name} "
                             f"reaches: its line gives {calibration.fit.intercept:g} mL/min at 0 rpm")
        return rpm

    def _command(self, p: Assignment):
        source = CommandSource.RECIPE; setting = p.setting; value = p.value
        if setting == "flow": return SetMfcFlow(p.device_id, float(value), source, p.unit)
        if setting == "flow_sccm": return SetMfcFlow(p.device_id, float(value), source, "SCCM")
        if setting == "pressure": return SetPressureSetpoint(p.device_id, float(value), source, p.unit or "bara")
        if setting == "voltage": return SetPowerSupplyVoltage(p.device_id, float(value), source)
        if setting == "current_limit": return SetPowerSupplyCurrentLimit(p.device_id, float(value), source)
        # CC names drive the same two registers as CV, with the roles swapped.
        if setting == "current_setpoint": return SetPowerSupplyCurrentLimit(p.device_id, float(value), source)
        if setting == CURRENT_DENSITY_SETTING:
            return SetPowerSupplyCurrentLimit(p.device_id, self._cell_area.amps_for(float(value)), source)
        if setting == "voltage_limit": return SetPowerSupplyVoltage(p.device_id, float(value), source)
        if setting == "output_enabled": return SetPowerSupplyOutput(p.device_id, bool(value), source)
        if setting == "temperature_setpoint": return SetTemperatureSetpoint(p.device_id, float(value), source)
        if setting == "pump_speed": return SetPumpSpeed(p.device_id, float(value), source)
        if setting == "pump_flow": return SetPumpSpeed(p.device_id, self._rpm_for_flow(p.device_id, float(value)), source)
        if setting == "pump_direction": return SetPumpDirection(p.device_id, PumpDirection(str(value)), source)
        if setting == "pump_running": return SetPumpRunning(p.device_id, bool(value), source)
        if setting == "hotplate_temperature": return SetHotplateTemperature(p.device_id, float(value), source)
        if setting == "hotplate_speed": return SetHotplateSpeed(p.device_id, float(value), source)
        if setting == "hotplate_heating": return SetHotplateHeating(p.device_id, bool(value), source)
        if setting == "hotplate_stirring": return SetHotplateStirring(p.device_id, bool(value), source)
        if setting.startswith("controller_output:"): return SetControllerOutput(p.device_id, setting.split(":", 1)[1], bool(value), source)
        raise ValueError("Unsupported recipe setting; use an explicit supported setting")
    def _validate_capability_and_limits(self, device: object, step: Assignment) -> None:
        setting = step.setting
        required = {
            "flow": MassFlowController, "flow_sccm": MassFlowController, "pressure": PressureController,
            "voltage": PowerSupply, "current_limit": PowerSupply, "output_enabled": PowerSupply,
            "current_setpoint": PowerSupply, CURRENT_DENSITY_SETTING: PowerSupply, "voltage_limit": PowerSupply,
            "temperature_setpoint": LumelRe72, "pump_speed": Pump, "pump_flow": Pump, "pump_direction": Pump,
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
        maximums = {"flow": "maximum_flow", "flow_sccm": "maximum_flow", "voltage": "maximum_voltage",
                    "current_limit": "maximum_current", "voltage_limit": "maximum_voltage",
                    "current_setpoint": "maximum_current", CURRENT_DENSITY_SETTING: "maximum_current"}
        maximum = getattr(limits, maximums.get(setting, ""), None)
        if maximum is None or not isinstance(value, (int, float)):
            return
        # The maximum is in the device's own unit, not the setting's.
        actual = (self._cell_area.amps_for(float(value)) if setting == CURRENT_DENSITY_SETTING
                  else convert_mass_flow(float(value), "SCCM", limits.flow_unit) if setting == "flow_sccm"
                  else float(value))
        if actual > maximum:
            raise ValueError(f"{setting} {value:g} ({actual:g} in the device's unit) exceeds configured maximum {maximum:g}")
    def _check_cancel(self) -> None:
        if self._cancel.is_set(): raise _Cancelled()
    def _set_status(self, label: str, position: tuple[str, ...]) -> None:
        elapsed = self._clock() - self._started_at
        with self._lock:
            self._status = RecipeRunStatus(
                RecipeRunState.RUNNING, label, position, elapsed, max(0.0, self._estimated_seconds - elapsed),
                revision=self._revision,
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
