"""Pop-out editor for one recipe step, with every option for that step.

The builder's side panel handles quick value changes; this window handles
the rest (which device and setting, adding settings to a Set step, list or
range for a loop).  It edits a copy: OK hands back the new step, Cancel
leaves the recipe untouched.
"""
from collections.abc import Callable
import tkinter as tk
from tkinter import ttk

from rig_control.devices.power_supply import PowerSupplyOperatingMode
from rig_control.rig_profile import DeviceCapability
from rig_control.recipes import Ramp, RampKind
from rig_control.recipes import Assignment, LoopStep, RepeatStep, SetStep, WaitPurpose, WaitStep
from rig_control.recipes.outline import OutlineRow
from rig_control.ui.common.theme import ERROR_TEXT, INFO_TEXT, MUTED_TEXT, SECTION_FONT, WARNING_TEXT
from rig_control.ui.common.widgets import VerticalScrolledFrame
from rig_control.ui.operation.recipe_settings import (
    ON_OFF, SETTINGS, TIME_UNITS, SettingSpec, as_range, default_value, expand_range, format_value,
    CURRENT_UNITS, RAMP_METHODS, ValueDisplay, device_settings, group_by_device, group_mode, next_device, parse_pass_names,
    spec_for, unit_for,
)

_WRAP = 380


def _configure_value_box(box: ttk.Combobox, spec: SettingSpec) -> None:
    if spec.kind == "number":
        box.configure(values=(), state="normal")
    else:
        box.configure(values=ON_OFF if spec.kind == "bool" else spec.choices, state="readonly")


class _TargetPicker:
    """Device and setting dropdowns; the setting list follows the device.

    ``on_change(previous_spec, new_spec)`` runs when the user picks either.
    """

    def __init__(self, parent: tk.Widget, roles, on_change) -> None:
        self._roles, self._on_change = roles, on_change
        self._spec: SettingSpec | None = None
        self.frame = ttk.Frame(parent); self.frame.columnconfigure(1, weight=1)
        ttk.Label(self.frame, text="Device").grid(row=0, column=0, sticky="w", padx=(0, 6))
        self._device = ttk.Combobox(self.frame, state="readonly",
                                    values=[f"{r.friendly_name} ({r.device_id})" for r in roles])
        self._device.grid(row=0, column=1, sticky="ew")
        ttk.Label(self.frame, text="Setting").grid(row=1, column=0, sticky="w", padx=(0, 6), pady=(3, 0))
        self._setting = ttk.Combobox(self.frame, state="readonly")
        self._setting.grid(row=1, column=1, sticky="ew", pady=(3, 0))
        self._device.bind("<<ComboboxSelected>>", lambda _e: self._changed(refill=True))
        self._setting.bind("<<ComboboxSelected>>", lambda _e: self._changed(refill=False))

    def select(self, device_id: str, setting: str) -> None:
        ids = [r.device_id for r in self._roles]
        if device_id in ids:
            self._device.current(ids.index(device_id))
        else:
            self._device.set(f"{device_id} (not in this rig profile)")
        self._fill(setting)

    def device_id(self) -> str:
        index = self._device.current()
        if index < 0:
            raise ValueError("Choose a device")
        return self._roles[index].device_id

    def spec(self) -> SettingSpec:
        specs = self._specs()
        index = self._setting.current()
        if not 0 <= index < len(specs):
            raise ValueError("Choose a setting")
        return specs[index]

    def _specs(self) -> tuple[SettingSpec, ...]:
        index = self._device.current()
        return SETTINGS.get(self._roles[index].capability, ()) if index >= 0 else ()

    def _fill(self, setting: str | None = None) -> None:
        specs = self._specs()
        self._setting.configure(values=[s.label for s in specs])
        names = [s.name for s in specs]
        if setting in names:
            self._setting.current(names.index(setting))
        elif specs:
            self._setting.current(0)
        else:
            self._setting.set("")
        self._spec = specs[self._setting.current()] if specs else None

    def _changed(self, refill: bool) -> None:
        before = self._spec
        if refill:
            self._fill()
        else:
            try:
                self._spec = self.spec()
            except ValueError:
                self._spec = None
        if self._spec is not None:
            self._on_change(before, self._spec)


def _replace_text(entry: ttk.Entry, text: str) -> None:
    """Set an entry's text even while it is disabled."""
    state = str(entry.cget("state"))
    entry.configure(state="normal"); entry.delete(0, "end"); entry.insert(0, text); entry.configure(state=state)


class _UnitPicker:
    """The unit beside a value: an mA/A dropdown for currents, a plain label otherwise.

    Switching mA <-> A converts the numbers shown (250 mA -> 0.25 A) through
    ``on_switch(old, new)``, so a quantity never changes just because its
    unit did.  (Time units reinterpret instead, which is harmless there.)
    """

    def __init__(self, parent: tk.Widget, units: ValueDisplay, on_switch) -> None:
        self.units, self._on_switch = units, on_switch
        self.box = ttk.Combobox(parent, values=CURRENT_UNITS, state="readonly", width=4)
        self.box.set(units.current_unit)
        self.box.bind("<<ComboboxSelected>>", lambda _e: self._switched())
        self.label = ttk.Label(parent, foreground=MUTED_TEXT)

    def show(self, spec: SettingSpec | None, **grid) -> None:
        """Grid the dropdown for a current setting, else the label."""
        current = spec is not None and spec.unit == "A"
        (self.box if current else self.label).grid(**grid)
        (self.label if current else self.box).grid_remove()
        self.label.configure(text=self.units.unit(spec) if spec else "")

    def _switched(self) -> None:
        old, self.units = self.units, ValueDisplay(self.box.get())
        if old.current_unit != self.units.current_unit:
            self._on_switch(old, self.units)


class _RampEditor:
    """Ramp options for one numeric setting: method, step size or count, hold.

    By default the ramp starts from the last value the recipe set before
    this step, shown read-only.  "Fixed start" makes it jump to a typed value
    first; it is required when nothing earlier sets the value.  Values are
    typed in display units (e.g. mA) and a rate per minute; the ``Ramp`` it
    builds holds the recipe's own units (A, per second).
    """

    _AMOUNT = {"step_size": "Step size", "step_count": "Number of steps",
               "setpoints": "Setpoints", "rate": "Rate"}

    def __init__(self, parent: tk.Widget, dialog: "StepEditorDialog", *, allow_setpoints: bool,
                 in_loop: bool = False, units=None) -> None:
        self._dialog, self._spec, self._in_loop = dialog, None, in_loop
        #: The unit its setting is typed in (follows that setting's mA/A choice).
        self._units = units or (lambda: dialog._units)
        self._auto: object = None
        self.enabled = tk.BooleanVar(value=False)
        self._fixed = tk.BooleanVar(value=False)
        self._methods = [k for k in RAMP_METHODS if allow_setpoints or k != "setpoints"]
        self.frame = ttk.Frame(parent, padding=(24, 2, 0, 6))
        ttk.Label(self.frame, text="Method").grid(row=0, column=0, sticky="w", padx=(0, 8))
        self._method = ttk.Combobox(self.frame, state="readonly", width=26,
                                    values=[RAMP_METHODS[k] for k in self._methods])
        self._method.grid(row=0, column=1, columnspan=2, sticky="w", pady=1)
        self._method.bind("<<ComboboxSelected>>", lambda _e: (self._method_changed(), dialog._changed()))
        self._start_info = ttk.Label(self.frame, wraplength=_WRAP - 40, justify="left")
        self._start_info.grid(row=1, column=0, columnspan=3, sticky="w", pady=(2, 0))
        self._fixed_tick = ttk.Checkbutton(self.frame, text="Fixed first value" if in_loop else "Fixed start",
                                           variable=self._fixed, command=self._fixed_changed)
        self._fixed_tick.grid(row=2, column=0, sticky="w", padx=(0, 8))
        self._from = ttk.Entry(self.frame, width=10); self._from.grid(row=2, column=1, sticky="w", pady=1)
        self._from_unit = ttk.Label(self.frame, foreground=MUTED_TEXT); self._from_unit.grid(row=2, column=2, sticky="w", padx=4)
        self._amount_label = ttk.Label(self.frame); self._amount_label.grid(row=3, column=0, sticky="w", padx=(0, 8))
        self._amount = ttk.Entry(self.frame, width=10); self._amount.grid(row=3, column=1, sticky="w", pady=1)
        self._amount_unit = ttk.Label(self.frame, foreground=MUTED_TEXT); self._amount_unit.grid(row=3, column=2, sticky="w", padx=4)
        self._hold_label = ttk.Label(self.frame); self._hold_label.grid(row=4, column=0, sticky="w", padx=(0, 8))
        hold = ttk.Frame(self.frame); hold.grid(row=4, column=1, columnspan=2, sticky="w", pady=1)
        self._hold = ttk.Entry(hold, width=10); self._hold.pack(side="left")
        self._hold_unit = ttk.Combobox(hold, values=tuple(TIME_UNITS), state="readonly", width=5)
        self._hold_unit.pack(side="left", padx=4)
        for widget in (self._from, self._amount, self._hold, self._hold_unit):
            dialog._watch(widget)

    def load(self, ramp: Ramp | None, spec: SettingSpec, auto_start: object = None) -> None:
        units = self._units()
        self._spec, self._auto = spec, auto_start
        self.enabled.set(ramp is not None)
        kind = ramp.kind.value if ramp else "step_size"
        self._method.current(self._methods.index(kind) if kind in self._methods else 0)
        self._fixed.set(ramp is not None and ramp.start is not None)
        start = ramp.start if ramp is not None and ramp.start is not None else (
            auto_start if isinstance(auto_start, float) else 0.0)
        amount = ("" if ramp is None else units.show(ramp.step, spec) if kind == "step_size" else str(ramp.count)
                  if kind == "step_count" else ", ".join(units.show(v, spec) for v in ramp.setpoints)
                  if kind == "setpoints" else units.show(ramp.rate * 60.0, spec))
        seconds = ramp.dwell_seconds if ramp else 30.0
        unit = next((u for u in ("h", "min") if seconds >= TIME_UNITS[u]
                     and (seconds / TIME_UNITS[u]).is_integer()), "s")
        for entry, text in ((self._from, units.show(start, spec)), (self._amount, amount),
                            (self._hold, f"{seconds / TIME_UNITS[unit]:g}")):
            entry.delete(0, "end"); entry.insert(0, text)
        self._hold_unit.set(unit)
        self._method_changed()

    def set_spec(self, spec: SettingSpec, auto_start: object = None) -> None:
        self._spec, self._auto = spec, auto_start; self._method_changed()

    def convert(self, old: ValueDisplay, new: ValueDisplay) -> None:
        """Re-show typed values after the setting's unit changed; quantities stay the same."""
        kind, spec = self._kind(), self._spec
        if spec is not None:
            _replace_text(self._from, old.convert_text(self._from.get(), spec, new))
            if kind != "step_count":
                _replace_text(self._amount, old.convert_text(self._amount.get(), spec, new,
                                                             many=kind == "setpoints"))
        self._method_changed()

    def _kind(self) -> str:
        return self._methods[max(self._method.current(), 0)]

    def _fixed_changed(self) -> None:
        self._method_changed(); self._dialog._changed()

    def _method_changed(self) -> None:
        kind = self._kind()
        units = self._units()
        unit = units.unit(self._spec) if self._spec else ""
        uses_start = kind != "setpoints"
        known = isinstance(self._auto, float)
        if uses_start and not known:
            self._fixed.set(True)  # Nothing earlier sets it: a fixed start is the only option.
        for widget in (self._fixed_tick, self._from, self._from_unit):
            widget.grid() if uses_start else widget.grid_remove()
        self._fixed_tick.configure(state="normal" if known else "disabled")
        self._from.configure(state="normal" if self._fixed.get() else "disabled")
        self._from_unit.configure(text=unit)
        where = "the loop" if self._in_loop else "this step"
        if not uses_start:
            info, colour = "Moves through the listed setpoints in order, then to the value.", MUTED_TEXT
        elif self._fixed.get():
            info, colour = ((f"Jumps to the fixed {'first value' if self._in_loop else 'start'} first, then ramps."
                             if known else f"Nothing earlier in the recipe sets this, so the ramp needs a fixed start."),
                            MUTED_TEXT if known else WARNING_TEXT)
        else:
            shown = f"{units.show(self._auto, self._spec)} {units.suffix(self._spec)}".strip() if self._spec else ""
            info = (f"Starts from {shown}, the last value set before {where}"
                    + ("; each later value ramps from the one before." if self._in_loop else "."))
            colour = INFO_TEXT
        self._start_info.configure(text=info, foreground=colour)
        self._amount_label.configure(text=self._AMOUNT[kind])
        self._amount_unit.configure(text={"step_size": unit, "step_count": "", "setpoints": f"{unit}  (comma-separated)",
                                          "rate": f"{unit}/min"}[kind])
        self._hold_label.configure(text="Update every" if kind == "rate" else "Hold each step")

    def read(self) -> Ramp | None:
        if not self.enabled.get():
            return None
        units, spec, kind = self._units(), self._spec, self._kind()
        def number(entry: ttk.Entry, what: str) -> float:
            try:
                return float(entry.get())
            except ValueError:
                raise ValueError(f"{what} must be a number") from None
        def amount_of(entry: ttk.Entry, what: str) -> float:
            try:
                return float(units.read(entry.get(), spec))  # type: ignore[arg-type]  # A typed unit (e.g. 0.2 A) wins.
            except ValueError:
                raise ValueError(f"{what} must be a number") from None
        hold = number(self._hold, "Hold") * TIME_UNITS.get(self._hold_unit.get(), 1.0)
        if kind == "setpoints":
            values = units.read_values(self._amount.get(), spec)  # type: ignore[arg-type]
            return Ramp(RampKind.SETPOINTS, hold, setpoints=values)  # type: ignore[arg-type]
        start = amount_of(self._from, "Fixed start") if self._fixed.get() else None
        if kind == "step_count":
            try:
                count = int(self._amount.get())
            except ValueError:
                raise ValueError("Number of steps must be a whole number") from None
            return Ramp(RampKind.STEP_COUNT, hold, start=start, count=count)  # type: ignore[arg-type]
        amount = amount_of(self._amount, self._AMOUNT[kind])
        if kind == "step_size":
            return Ramp(RampKind.STEP_SIZE, hold, start=start, step=amount)  # type: ignore[arg-type]
        return Ramp(RampKind.RATE, hold, start=start, rate=amount / 60.0)  # type: ignore[arg-type]


_CC, _CV = PowerSupplyOperatingMode.CONSTANT_CURRENT, PowerSupplyOperatingMode.CONSTANT_VOLTAGE
_MODE_TEXT = {_CC: "Constant current (CC)", _CV: "Constant voltage (CV)"}


class _DeviceCard:
    """One device in a Set step: all its settings as a compact tick list.

    Ticked settings are written in the order listed; unticked ones are left
    as they are.  A supply card shows only the chosen mode's settings.
    """

    def __init__(self, parent: tk.Widget, dialog: "StepEditorDialog", device_id: str,
                 assignments: list[Assignment], *, fixed: bool = False, allow_ramps: bool = True) -> None:
        """``fixed`` hides the device chooser and buttons (the end state lists each
        device itself); ``allow_ramps`` False drops the ramp ticks."""
        self._dialog, self._roles, self._allow_ramps = dialog, dialog._roles, allow_ramps
        self.frame = ttk.Frame(parent, padding=(0, 4, 4, 8)); self.frame.columnconfigure(0, weight=1)
        head = ttk.Frame(self.frame); head.grid(row=0, column=0, sticky="ew"); head.columnconfigure(0, weight=1)
        self._device = ttk.Combobox(head, state="readonly", font=SECTION_FONT,
                                    values=[f"{r.friendly_name} ({r.device_id})" for r in self._roles])
        self._device.grid(row=0, column=0, sticky="ew")
        self._device.bind("<<ComboboxSelected>>", lambda _e: self._device_changed())
        self.up = ttk.Button(head, text="▲", width=3, command=lambda: dialog._raise_card(self))
        self.up.grid(row=0, column=1, padx=(6, 2))
        ttk.Button(head, text="✕", width=3, command=lambda: dialog._remove_card(self)).grid(row=0, column=2)
        if fixed:
            head.grid_remove()
        self._mode = tk.StringVar(value=(group_mode(assignments) or _CC).value)
        self._mode_row = ttk.Frame(self.frame)
        for mode in (_CC, _CV):
            ttk.Radiobutton(self._mode_row, text=_MODE_TEXT[mode], variable=self._mode, value=mode.value,
                            command=self._mode_changed).pack(side="left", padx=(0, 12))
        self._rows = ttk.Frame(self.frame); self._rows.grid(row=2, column=0, sticky="ew", pady=(4, 0))
        ttk.Separator(self.frame).grid(row=3, column=0, sticky="ew", pady=(8, 0))
        # Remembered per setting, so switching CC/CV and back loses nothing.
        self._ticked = {a.setting: True for a in assignments}
        self._texts = {a.setting: dialog._units.show(a.value, spec_for(a.setting)) for a in assignments}
        self._ramps: dict[str, Ramp | None] = {a.setting: a.ramp for a in assignments}
        self._widgets: dict[str, tuple[SettingSpec, tk.BooleanVar, ttk.Entry]] = {}
        self._ramp_editors: dict[str, _RampEditor] = {}
        self._ramp_ticks: dict[str, ttk.Checkbutton] = {}
        #: Each setting's typed unit (mA or A for currents); texts above are in it.
        self._row_units: dict[str, ValueDisplay] = {}
        self.device_id = device_id
        ids = [r.device_id for r in self._roles]
        if device_id in ids:
            self._device.current(ids.index(device_id))
        else:
            self._device.set(f"{device_id} (not in this rig profile)")
        self._build_rows()

    def _capability(self) -> DeviceCapability | None:
        return next((r.capability for r in self._roles if r.device_id == self.device_id), None)

    def _specs(self) -> tuple[SettingSpec, ...]:
        capability = self._capability()
        if capability is None:
            return ()
        return device_settings(capability, PowerSupplyOperatingMode(self._mode.get()))

    def _friendly(self) -> str:
        return next((r.friendly_name for r in self._roles if r.device_id == self.device_id), self.device_id)

    def _remember(self) -> None:
        for name, (_spec, ticked, box) in self._widgets.items():
            self._ticked[name] = ticked.get(); self._texts[name] = box.get()
        for name, editor in self._ramp_editors.items():
            try:
                self._ramps[name] = editor.read()
            except ValueError:
                pass  # Keep the last good ramp rather than lose it on a mode switch.

    def _build_rows(self) -> None:
        self._remember()
        for child in self._rows.winfo_children():
            child.destroy()
        self._widgets, self._ramp_editors, self._ramp_ticks = {}, {}, {}
        if self._capability() is DeviceCapability.DC_POWER_SUPPLY:
            self._mode_row.grid(row=1, column=0, sticky="w", pady=(4, 0))
        else:
            self._mode_row.grid_remove()
        specs = self._specs()
        for index, spec in enumerate(specs):
            r = 2 * index  # Each setting has a row, then a row for its ramp options.
            ticked = tk.BooleanVar(value=self._ticked.get(spec.name, False))
            ttk.Checkbutton(self._rows, text=spec.label, variable=ticked,
                            command=lambda n=spec.name: self._tick_changed(n)).grid(row=r, column=0, sticky="w", pady=1)
            # Numbers are typed; On/Off and choices are picked from a list.
            box = ttk.Entry(self._rows, width=12) if spec.kind == "number" else ttk.Combobox(self._rows, width=10)
            text = self._texts.get(spec.name, format_value(default_value(spec)))
            if isinstance(box, ttk.Combobox):
                _configure_value_box(box, spec); box.set(text)  # readonly: set(), not insert()
            else:
                box.insert(0, text)
            box.grid(row=r, column=1, sticky="w", padx=(10, 4), pady=1)
            picker = _UnitPicker(self._rows, self._units_for(spec.name),
                                 lambda old, new, n=spec.name: self._unit_switched(n, old, new))
            picker.show(spec, row=r, column=2, sticky="w")
            self._dialog._watch(box)
            self._widgets[spec.name] = (spec, ticked, box)
            if spec.kind == "number" and self._allow_ramps:
                editor = _RampEditor(self._rows, self._dialog, allow_setpoints=True,
                                     units=lambda n=spec.name: self._units_for(n))
                editor.frame.grid(row=r + 1, column=0, columnspan=4, sticky="w")
                editor.load(self._ramps.get(spec.name), spec, self._dialog._start_for(self.device_id, spec.name))
                tick = ttk.Checkbutton(self._rows, text="ramp", variable=editor.enabled,
                                       command=lambda n=spec.name: self._ramp_toggled(n))
                tick.grid(row=r, column=3, sticky="w", padx=(8, 0))
                self._ramp_editors[spec.name], self._ramp_ticks[spec.name] = editor, tick
            self._enable(spec.name)
        if not specs:
            ttk.Label(self._rows, text="Choose a device from this rig profile.", foreground=MUTED_TEXT).grid(row=0, column=0)

    def _enable(self, name: str) -> None:
        spec, ticked, box = self._widgets[name]
        box.configure(state=("normal" if spec.kind == "number" else "readonly") if ticked.get() else "disabled")
        if name in self._ramp_editors:
            self._ramp_ticks[name].configure(state="normal" if ticked.get() else "disabled")
            editor = self._ramp_editors[name]
            editor.frame.grid() if ticked.get() and editor.enabled.get() else editor.frame.grid_remove()

    def _units_for(self, name: str) -> ValueDisplay:
        return self._row_units.get(name, self._dialog._units)

    def _unit_switched(self, name: str, old: ValueDisplay, new: ValueDisplay) -> None:
        spec, _ticked, box = self._widgets[name]
        _replace_text(box, old.convert_text(box.get(), spec, new))
        self._row_units[name] = new
        if name in self._ramp_editors:
            self._ramp_editors[name].convert(old, new)
        self._dialog._changed()

    def _ramp_toggled(self, name: str) -> None:
        self._enable(name); self._dialog._changed()

    def _tick_changed(self, name: str) -> None:
        self._enable(name)
        if self._widgets[name][1].get():
            self._widgets[name][2].focus_set()
        self._dialog._changed()

    def _mode_changed(self) -> None:
        self._build_rows(); self._dialog._changed()

    def _device_changed(self) -> None:
        index = self._device.current()
        new = self._roles[index].device_id
        if new == self.device_id:
            return
        if new in self._dialog._card_devices(exclude=self):
            self._dialog._error.configure(text=f"{self._roles[index].friendly_name} is already in this step; "
                                               "change its settings in that card.")
            ids = [r.device_id for r in self._roles]
            if self.device_id in ids:
                self._device.current(ids.index(self.device_id))
            return
        # A different device has different settings: start with its first one ticked.
        self.device_id, self._widgets, self._texts = new, {}, {}
        first = device_settings(self._roles[index].capability)[:1]
        self._ticked = {spec.name: True for spec in first}
        self._build_rows(); self._dialog._changed()

    def read(self) -> list[Assignment]:
        result = []
        for spec in self._specs():
            _spec, ticked, box = self._widgets[spec.name]
            if ticked.get():
                try:
                    value = self._units_for(spec.name).read(box.get(), spec)
                except ValueError as error:
                    raise ValueError(f"{self._friendly()} · {spec.label}: {error}") from None
                try:
                    ramp = self._ramp_editors[spec.name].read() if spec.name in self._ramp_editors else None
                    result.append(Assignment(self.device_id, spec.name, value, unit_for(spec), ramp))
                except ValueError as error:
                    raise ValueError(f"{self._friendly()} · {spec.label} ramp: {error}") from None
        if not result:
            raise ValueError(f"Nothing is ticked for {self._friendly()}; tick a setting or remove the device")
        return result

    def focus(self) -> None:
        for _spec, ticked, box in self._widgets.values():
            if ticked.get():
                box.focus_set()
                if str(box.cget("state")) == "normal":
                    box.select_range(0, "end")
                return


class StepEditorDialog:
    """Modal editor for one Set, Wait, Loop or Repeat step.

    ``check(step)`` returns warnings for the step in its place in the
    recipe; they are shown but do not block OK.  ``on_done`` receives the
    edited step, or ``None`` on Cancel.
    """

    def __init__(self, parent: tk.Misc, row: OutlineRow, *, roles,
                 check: Callable[[OutlineRow], tuple[str, ...]],
                 on_done: Callable[[OutlineRow | None], None], is_new: bool = False,
                 focus_device: str | None = None, units: ValueDisplay | None = None,
                 values_before: dict[tuple[str, str], object] | None = None) -> None:
        self._row, self._roles, self._check, self._on_done = row, roles, check, on_done
        self._focus_device = focus_device
        #: Shows and reads currents in mA or A; the step itself keeps amperes.
        self._units = units or ValueDisplay()
        #: Where automatic ramps start: each setting's value just before this step.
        self._values_before = values_before or {}
        self._closed = False
        top = self._top = tk.Toplevel(parent)
        top.transient(parent.winfo_toplevel())
        kind = {SetStep: "Set values", WaitStep: "Wait", LoopStep: "Loop over values", RepeatStep: "Repeat"}[type(row)]
        top.title(("New step — " if is_new else "Edit step — ") + kind)
        top.columnconfigure(0, weight=1); top.rowconfigure(0, weight=1)
        body = ttk.Frame(top, padding=12); body.grid(row=0, column=0, sticky="nsew")
        body.columnconfigure(0, weight=1); body.rowconfigure(1, weight=1)
        self._body = body
        # Every step can have a human-readable name.
        name_row = ttk.Frame(body); name_row.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        name_row.columnconfigure(1, weight=1)
        ttk.Label(name_row, text="Step name", font=SECTION_FONT).grid(row=0, column=0, sticky="w", padx=(0, 8))
        self._name = ttk.Entry(name_row); self._name.grid(row=0, column=1, sticky="ew")
        self._name.insert(0, row.name)
        ttk.Label(name_row, text="Optional, e.g. “Break in” or “Stabilise”. Shown in the list and in recorded data.",
                  foreground=MUTED_TEXT, wraplength=_WRAP, justify="left").grid(row=1, column=1, sticky="w")
        self._watch(self._name)
        content = self._content = ttk.Frame(body); content.grid(row=1, column=0, sticky="nsew")
        content.columnconfigure(0, weight=1); content.rowconfigure(0, weight=1)

        if isinstance(row, SetStep):
            self._build_set(row)
        elif isinstance(row, LoopStep):
            self._build_loop(row)
        elif isinstance(row, WaitStep):
            self._build_wait(row)
        else:
            self._build_repeat(row)

        self._warning = ttk.Label(body, foreground=WARNING_TEXT, wraplength=_WRAP, justify="left")
        self._warning.grid(row=8, column=0, sticky="ew", pady=(8, 0))
        self._error = ttk.Label(body, foreground=ERROR_TEXT, wraplength=_WRAP, justify="left")
        self._error.grid(row=9, column=0, sticky="ew")
        buttons = ttk.Frame(body); buttons.grid(row=10, column=0, sticky="e", pady=(10, 0))
        ttk.Button(buttons, text="OK", command=self._ok, default="active").pack(side="left", padx=3)
        ttk.Button(buttons, text="Cancel", command=self._cancel).pack(side="left")
        top.bind("<Return>", lambda _e: self._ok())
        top.bind("<Escape>", lambda _e: self._cancel())
        top.protocol("WM_DELETE_WINDOW", self._cancel)
        self._changed()
        self._place(parent)
        top.grab_set()
        self._focus_first()

    # --- layout per step kind ---

    def _build_set(self, row: SetStep) -> None:
        groups = group_by_device(row.assignments)
        ramps = sum(1 for a in row.assignments if a.ramp)
        self._top.geometry(f"560x{min(800, 420 + 190 * len(groups) + 110 * ramps)}")
        scroller = VerticalScrolledFrame(self._content)
        scroller.grid(row=0, column=0, sticky="nsew")
        self._cards_frame = scroller.content
        self._cards = [_DeviceCard(self._cards_frame, self, device_id, items) for device_id, items in groups]
        self._regrid_cards()
        ttk.Button(self._content, text="+ Add another device to this step",
                   command=self._add_card).grid(row=1, column=0, sticky="w", pady=(6, 0))
        hold = ttk.Frame(self._content); hold.grid(row=2, column=0, sticky="w", pady=(10, 0))
        ttk.Label(hold, text="Then hold for", font=SECTION_FONT).grid(row=0, column=0, sticky="w", padx=(0, 8))
        seconds = row.hold.duration_seconds if row.hold else 0.0
        unit = next((u for u in ("h", "min") if seconds >= TIME_UNITS[u]
                     and (seconds / TIME_UNITS[u]).is_integer()), "s") if seconds else "min"
        self._hold = ttk.Entry(hold, width=8); self._hold.grid(row=0, column=1)
        if seconds:
            self._hold.insert(0, f"{seconds / TIME_UNITS[unit]:g}")
        self._hold_unit = ttk.Combobox(hold, values=tuple(TIME_UNITS), state="readonly", width=5)
        self._hold_unit.grid(row=0, column=2, padx=4); self._hold_unit.set(unit)
        self._hold_purpose = tk.StringVar(value=(row.hold.purpose if row.hold else WaitPurpose.SETTLE).value)
        ttk.Radiobutton(hold, text="Settle", variable=self._hold_purpose, value=WaitPurpose.SETTLE.value,
                        command=self._changed).grid(row=0, column=3, padx=(8, 4))
        ttk.Radiobutton(hold, text="Measure", variable=self._hold_purpose, value=WaitPurpose.MEASURE.value,
                        command=self._changed).grid(row=0, column=4)
        self._watch(self._hold); self._watch(self._hold_unit)
        ttk.Label(hold, foreground=MUTED_TEXT, wraplength=_WRAP, justify="left", text=(
            "Leave empty or 0 to go straight on. Either way the settings stay until a later step "
            "changes them; a hold only delays the next step.")).grid(row=1, column=0, columnspan=5, sticky="w")
        ttk.Label(self._content, foreground=MUTED_TEXT, wraplength=_WRAP, justify="left", text=(
            "Tick what to change; unticked settings are left as they are. Each device's ticked "
            "settings are written in the order listed (a supply's output last), then the next "
            "device, with no wait between. Ramped settings then ramp from their last value (or jump to "
            "a fixed start with the others first), holding at each step on the way but not at the end.")
                  ).grid(row=3, column=0, sticky="w", pady=(8, 0))

    def _build_loop(self, row: LoopStep) -> None:
        frame = ttk.Frame(self._content); frame.grid(row=0, column=0, sticky="nsew"); frame.columnconfigure(2, weight=1)
        ttk.Label(frame, text="Each pass sets", font=SECTION_FONT).grid(row=0, column=0, columnspan=3, sticky="w")
        self._loop_target = _TargetPicker(frame, self._roles, self._loop_target_changed)
        self._loop_target.frame.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(2, 10))
        self._loop_target.select(row.device_id, row.setting)
        ttk.Label(frame, text="To each of these values, in order", font=SECTION_FONT).grid(row=2, column=0, columnspan=3, sticky="w")
        spec = spec_for(row.setting)
        #: The unit the loop's values are typed in (mA or A for currents).
        self._loop_units = self._units
        shown_values = tuple(self._units.to_display(v, spec) for v in row.values)
        span = as_range(shown_values) if spec.kind == "number" else None
        self._loop_kind = tk.StringVar(value="range" if span else "list")
        ttk.Radiobutton(frame, text="List (comma-separated; any order; repeats allowed)", variable=self._loop_kind,
                        value="list", command=self._loop_kind_changed).grid(row=3, column=0, columnspan=3, sticky="w")
        self._loop_list = ttk.Entry(frame, width=40)
        self._loop_list.grid(row=4, column=0, columnspan=3, sticky="ew", padx=(20, 0))
        self._loop_list.insert(0, ", ".join(format_value(v) for v in shown_values))
        self._watch(self._loop_list)
        self._range_radio = ttk.Radiobutton(frame, text="Range", variable=self._loop_kind, value="range",
                                            command=self._loop_kind_changed)
        self._range_radio.grid(row=5, column=0, columnspan=3, sticky="w", pady=(6, 0))
        self._range: dict[str, ttk.Entry] = {}
        for r, (key, label) in enumerate((("start", "From"), ("stop", "To (inclusive)"), ("step", "Step")), start=6):
            ttk.Label(frame, text=label).grid(row=r, column=0, sticky="w", padx=(20, 6))
            entry = ttk.Entry(frame, width=12); entry.grid(row=r, column=1, sticky="w", pady=1)
            entry.insert(0, f"{span[r - 6]:g}" if span else "")
            self._watch(entry); self._range[key] = entry
        self._loop_unit = _UnitPicker(frame, self._units, self._loop_unit_switched)
        self._loop_values = ttk.Label(frame, foreground=INFO_TEXT, wraplength=_WRAP, justify="left")
        self._loop_values.grid(row=9, column=0, columnspan=3, sticky="w", pady=(8, 0))
        ttk.Label(frame, text="Pass names (optional)", font=SECTION_FONT).grid(row=10, column=0, columnspan=3, sticky="w", pady=(10, 0))
        self._pass_names = ttk.Entry(frame, width=40)
        self._pass_names.grid(row=11, column=0, columnspan=3, sticky="ew")
        self._pass_names.insert(0, ", ".join(row.pass_names)); self._watch(self._pass_names)
        ttk.Label(frame, foreground=MUTED_TEXT, wraplength=_WRAP, justify="left", text=(
            "One per value, comma-separated, e.g. break in, 100 mA, 200 mA. "
            "Leave empty to name each pass by its value.")).grid(row=12, column=0, columnspan=3, sticky="w")
        self._loop_ramp = _RampEditor(frame, self, allow_setpoints=False, in_loop=True,
                                      units=lambda: self._loop_units)
        self._loop_ramp_tick = ttk.Checkbutton(frame, text="Ramp to each value instead of jumping",
                                               variable=self._loop_ramp.enabled, command=self._loop_ramp_toggled)
        self._loop_ramp_tick.grid(row=13, column=0, columnspan=3, sticky="w", pady=(10, 0))
        self._loop_ramp.frame.grid(row=14, column=0, columnspan=3, sticky="w")
        self._loop_ramp.load(row.ramp, spec, self._start_for(row.device_id, row.setting))
        self._loop_ramp_hint = ttk.Label(frame, foreground=MUTED_TEXT, wraplength=_WRAP, justify="left", text=(
            "Each step on the way is held; the value reached is not, so the steps inside the loop follow on."))
        self._loop_ramp_hint.grid(row=15, column=0, columnspan=3, sticky="w")
        self._show_loop_spec(spec)
        self._loop_ramp_toggled(check=False)
        self._show_loop_spec(spec)
        self._loop_kind_changed(check=False)

    def _build_wait(self, row: WaitStep) -> None:
        frame = ttk.Frame(self._content); frame.grid(row=0, column=0, sticky="nsew"); frame.columnconfigure(1, weight=1)
        ttk.Label(frame, text="Purpose").grid(row=0, column=0, sticky="nw", padx=(0, 10))
        self._purpose = tk.StringVar(value=row.purpose.value)
        purposes = ttk.Frame(frame); purposes.grid(row=0, column=1, sticky="w")
        ttk.Radiobutton(purposes, text="Measure (data of interest)", variable=self._purpose,
                        value=WaitPurpose.MEASURE.value, command=self._changed).pack(anchor="w")
        ttk.Radiobutton(purposes, text="Settle (let conditions stabilise)", variable=self._purpose,
                        value=WaitPurpose.SETTLE.value, command=self._changed).pack(anchor="w")
        ttk.Label(frame, text="Duration").grid(row=1, column=0, sticky="w", padx=(0, 10), pady=(8, 0))
        duration = ttk.Frame(frame); duration.grid(row=1, column=1, sticky="w", pady=(8, 0))
        unit = next((u for u in ("h", "min") if row.duration_seconds >= TIME_UNITS[u]
                     and (row.duration_seconds / TIME_UNITS[u]).is_integer()), "s")
        self._duration = ttk.Entry(duration, width=10); self._duration.pack(side="left")
        self._duration.insert(0, f"{row.duration_seconds / TIME_UNITS[unit]:g}")
        self._duration_unit = ttk.Combobox(duration, values=tuple(TIME_UNITS), state="readonly", width=5)
        self._duration_unit.pack(side="left", padx=4); self._duration_unit.set(unit)
        self._watch(self._duration); self._watch(self._duration_unit)

    def _build_repeat(self, row: RepeatStep) -> None:
        frame = ttk.Frame(self._content); frame.grid(row=0, column=0, sticky="nsew")
        ttk.Label(frame, text="Run the steps down to this loop's Next row").grid(row=0, column=0, columnspan=2, sticky="w")
        self._repeat_count = ttk.Spinbox(frame, from_=1, to=100_000, width=8, command=self._changed)
        self._repeat_count.grid(row=1, column=0, sticky="w", pady=(4, 0)); self._repeat_count.set(row.count)
        ttk.Label(frame, text="times").grid(row=1, column=1, sticky="w", padx=4, pady=(4, 0))
        self._watch(self._repeat_count)

    # --- Set: one card per device ---

    def _regrid_cards(self) -> None:
        for index, card in enumerate(self._cards):
            card.frame.grid(row=index, column=0, sticky="ew")
            card.up.configure(state="disabled" if index == 0 else "normal")
        self._changed()

    def _card_devices(self, exclude: _DeviceCard | None = None) -> set[str]:
        return {card.device_id for card in self._cards if card is not exclude}

    def _read_cards(self) -> list[Assignment]:
        return [assignment for card in self._cards for assignment in card.read()]

    def _add_card(self) -> None:
        role = next_device(self._card_devices(), self._roles)
        if role is None:
            self._error.configure(text="Every device is already in this step"); return
        spec = device_settings(role.capability)[0]
        card = _DeviceCard(self._cards_frame, self, role.device_id,
                           [Assignment(role.device_id, spec.name, default_value(spec), unit_for(spec))])
        self._cards.append(card); self._regrid_cards(); card.focus()

    def _remove_card(self, card: _DeviceCard) -> None:
        if len(self._cards) == 1:
            self._error.configure(text="A Set step needs at least one device; delete the step from the list instead.")
            return
        card.frame.destroy(); self._cards.remove(card); self._regrid_cards()

    def _raise_card(self, card: _DeviceCard) -> None:
        index = self._cards.index(card)
        if index > 0:
            self._cards[index - 1], self._cards[index] = card, self._cards[index - 1]
            self._regrid_cards()

    # --- loop helpers ---

    def _loop_ramp_toggled(self, check: bool = True) -> None:
        on = self._loop_ramp.enabled.get()
        for widget in (self._loop_ramp.frame, self._loop_ramp_hint):
            widget.grid() if on else widget.grid_remove()
        if check:
            self._changed()

    def _show_loop_spec(self, spec: SettingSpec) -> None:
        if hasattr(self, "_loop_ramp"):
            try:
                device_id = self._loop_target.device_id()
            except ValueError:
                device_id = ""
            self._loop_ramp.set_spec(spec, self._start_for(device_id, spec.name))
            if spec.kind != "number":
                self._loop_ramp.enabled.set(False); self._loop_ramp_toggled(check=False)
            self._loop_ramp_tick.configure(state="normal" if spec.kind == "number" else "disabled")
        self._loop_unit.show(spec, row=6, column=2, sticky="w", padx=4)
        self._range_radio.configure(state="normal" if spec.kind == "number" else "disabled")
        if spec.kind != "number" and self._loop_kind.get() == "range":
            self._loop_kind.set("list"); self._loop_kind_changed(check=False)

    def _loop_unit_switched(self, old: ValueDisplay, new: ValueDisplay) -> None:
        spec = self._loop_target.spec()
        _replace_text(self._loop_list, old.convert_text(self._loop_list.get(), spec, new, many=True))
        for entry in self._range.values():
            _replace_text(entry, old.convert_text(entry.get(), spec, new))
        self._loop_units = new
        self._loop_ramp.convert(old, new)
        self._changed()

    def _loop_target_changed(self, before: SettingSpec | None, spec: SettingSpec) -> None:
        self._show_loop_spec(spec)
        if before is None or before.kind != spec.kind:
            self._loop_list.delete(0, "end")
            self._loop_list.insert(0, {"bool": "On, Off", "choice": ", ".join(spec.choices)}.get(spec.kind, "0"))
        self._changed()

    def _loop_kind_changed(self, check: bool = True) -> None:
        ranged = self._loop_kind.get() == "range"
        self._loop_list.configure(state="disabled" if ranged else "normal")
        for entry in self._range.values():
            entry.configure(state="normal" if ranged else "disabled")
        if check:
            self._changed()

    # --- result ---

    def _start_for(self, device_id: str, setting: str) -> object:
        return self._values_before.get((device_id, setting))

    def _watch(self, widget: tk.Widget) -> None:
        widget.bind("<KeyRelease>", lambda _e: self._changed(), add="+")
        widget.bind("<<ComboboxSelected>>", lambda _e: self._changed(), add="+")

    def _build_row(self) -> OutlineRow:
        row, name = self._row, self._name.get()
        if isinstance(row, SetStep):
            return SetStep(tuple(self._read_cards()), name, self._read_hold())
        if isinstance(row, LoopStep):
            spec = self._loop_target.spec()
            if self._loop_kind.get() == "range":
                try:
                    start, stop, step = (float(self._range[k].get()) for k in ("start", "stop", "step"))
                except ValueError:
                    raise ValueError("From, To and Step must all be numbers") from None
                typed = expand_range(start, stop, step)
                values = tuple(self._loop_units.from_display(v, spec) for v in typed)
            else:
                values = self._loop_units.read_values(self._loop_list.get(), spec)  # A typed unit wins.
            shown = ", ".join(self._loop_units.show(v, spec) for v in values[:20])
            unit = self._loop_units.suffix(spec)
            self._loop_values.configure(
                text=f"→ {len(values)} pass{'es' if len(values) != 1 else ''}: {shown}"
                     f"{', …' if len(values) > 20 else ''}{' ' + unit if unit else ''}")
            try:
                ramp = self._loop_ramp.read()
            except ValueError as error:
                raise ValueError(f"Ramp: {error}") from None
            return LoopStep(self._loop_target.device_id(), spec.name, values, unit_for(spec),
                            name=name, pass_names=parse_pass_names(self._pass_names.get()), ramp=ramp)
        if isinstance(row, WaitStep):
            try:
                amount = float(self._duration.get())
            except ValueError:
                raise ValueError("Duration must be a number") from None
            return WaitStep(amount * TIME_UNITS.get(self._duration_unit.get(), 1.0),
                            WaitPurpose(self._purpose.get()), name)
        try:
            return RepeatStep(int(self._repeat_count.get()), name=name)
        except ValueError:
            raise ValueError("Number of times must be a whole number of at least 1") from None

    def _read_hold(self) -> WaitStep | None:
        text = self._hold.get().strip()
        if not text:
            return None
        try:
            amount = float(text)
        except ValueError:
            raise ValueError("Hold must be a number (or empty for no hold)") from None
        seconds = amount * TIME_UNITS.get(self._hold_unit.get(), 1.0)
        return WaitStep(seconds, WaitPurpose(self._hold_purpose.get())) if seconds else None

    def _changed(self) -> None:
        """Re-check the draft as the user edits; problems show, nothing is applied yet."""
        if not hasattr(self, "_error"):
            return
        try:
            row = self._build_row()
        except (ValueError, TypeError) as error:
            self._error.configure(text=str(error)); self._warning.configure(text=""); return
        self._error.configure(text="")
        warnings = self._check(row)
        self._warning.configure(text="⚠ " + warnings[0] if warnings else "")

    def _ok(self) -> None:
        try:
            row = self._build_row()
        except (ValueError, TypeError) as error:
            self._error.configure(text=str(error)); return
        self._close(row)

    def _cancel(self) -> None:
        self._close(None)

    def _close(self, row: OutlineRow | None) -> None:
        if self._closed:
            return
        self._closed = True
        self._top.grab_release(); self._top.destroy()
        self._on_done(row)

    # --- placement ---

    def _place(self, parent: tk.Misc) -> None:
        """Open over the right-hand side of the builder, leaving the step list visible."""
        self._top.update_idletasks()
        owner = parent.winfo_toplevel()
        x = owner.winfo_rootx() + max(owner.winfo_width() - self._top.winfo_reqwidth() - 30, 0)
        y = owner.winfo_rooty() + 80
        self._top.geometry(f"+{x}+{y}")

    def _focus_first(self) -> None:
        row = self._row
        if isinstance(row, SetStep):
            card = next((c for c in self._cards if c.device_id == self._focus_device), self._cards[0])
            card.focus(); return
        widget = (self._loop_list if isinstance(row, LoopStep)
                  else self._duration if isinstance(row, WaitStep) else self._repeat_count)
        if str(widget.cget("state")) != "disabled":
            widget.focus_set(); widget.select_range(0, "end")
