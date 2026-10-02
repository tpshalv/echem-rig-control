"""Pop-out editor for a recipe's end state: how the rig is left afterwards.

Each device is set to its safe state (off), left as it is, or set to
particular values.  The end state applies when the run finishes, when Stop
is pressed and if the recipe fails.  Choices that could cause back-suction,
pressurisation or a live supply ask for confirmation.
"""
from collections.abc import Callable
import tkinter as tk
from tkinter import messagebox, ttk

from rig_control.recipes import Assignment, EndAction, EndDevice, EndState
from rig_control.rig_profile import DeviceCapability
from rig_control.ui.common.theme import ERROR_TEXT, SECTION_FONT, WARNING_TEXT
from rig_control.ui.common.widgets import VerticalScrolledFrame
from rig_control.ui.operation.recipe_settings import (
    END_ACTION_TEXT, SETTINGS, ValueDisplay, capability_has_safe_state, end_state_roles, default_value, device_settings, end_state_warnings, unit_for,
)
from rig_control.ui.operation.recipe_step_dialog import _DeviceCard

_WRAP = 460
_ACTIONS = (EndAction.SAFE, EndAction.LEAVE, EndAction.SET)
_SAFE_WARNINGS = {
    DeviceCapability.MASS_FLOW_CONTROLLER: (
        "Safe state sets {name}'s flow to 0.\n\nWith the gas flow stopped, liquid can be drawn back "
        "towards the MFC, and air can leak in. Recipes normally leave the MFC as it is, so you can "
        "redirect the hoses before turning it off by hand.\n\nUse safe state for {name} anyway?"),
    DeviceCapability.BACK_PRESSURE_CONTROLLER: (
        "Safe state closes {name}'s valve, isolating the outlet.\n\nIf gas is still flowing, the cell "
        "pressurises; when flow stops, liquid can be drawn back towards the MFC. Recipes normally leave "
        "the back-pressure controller as it is.\n\nUse safe state for {name} anyway?"),
}


class EndStateDialog:
    """Modal editor for the end state.  ``on_done`` gets the new EndState, or None on Cancel."""

    def __init__(self, parent: tk.Misc, end: EndState, *, roles, units: ValueDisplay,
                 on_done: Callable[[EndState | None], None], has_safe_state=capability_has_safe_state) -> None:
        self._roles, self._units, self._on_done, self._closed = tuple(roles), units, on_done, False
        self._has_safe_state = has_safe_state
        #: Read-only devices (sensors, meters) have nothing to shut down, so they are not listed.
        self._listed = end_state_roles(self._roles, has_safe_state)
        top = self._top = tk.Toplevel(parent)
        top.transient(parent.winfo_toplevel()); top.title("End state — when the run finishes or you press Stop")
        top.geometry("600x700"); top.columnconfigure(0, weight=1); top.rowconfigure(0, weight=1)
        body = ttk.Frame(top, padding=12); body.grid(row=0, column=0, sticky="nsew")
        body.columnconfigure(0, weight=1); body.rowconfigure(1, weight=1)
        ttk.Label(body, wraplength=_WRAP, justify="left", text=(
            "How the rig is left when the run finishes, when you press Stop (remaining steps are "
            "skipped), or if the recipe fails. Control then returns to manual. Devices not changed "
            "here go to their safe state. Read-only devices such as sensors are not listed.")
                  ).grid(row=0, column=0, sticky="w", pady=(0, 8))
        scroller = VerticalScrolledFrame(body); scroller.grid(row=1, column=0, sticky="nsew")
        self._rows: dict[str, tuple[ttk.Combobox, ttk.Frame]] = {}
        self._cards: dict[str, _DeviceCard] = {}
        self._actions: dict[str, EndAction] = {}
        self._initial = {d.device_id: d for d in end.devices}
        for index, role in enumerate(self._listed):
            self._add_device_row(scroller.content, index, role)
        self._warning = ttk.Label(body, foreground=WARNING_TEXT, wraplength=_WRAP, justify="left")
        self._warning.grid(row=2, column=0, sticky="w", pady=(8, 0))
        self._error = ttk.Label(body, foreground=ERROR_TEXT, wraplength=_WRAP, justify="left")
        self._error.grid(row=3, column=0, sticky="w")
        buttons = ttk.Frame(body); buttons.grid(row=4, column=0, sticky="e", pady=(10, 0))
        ttk.Button(buttons, text="OK", command=self._ok, default="active").pack(side="left", padx=3)
        ttk.Button(buttons, text="Cancel", command=self._cancel).pack(side="left")
        top.bind("<Escape>", lambda _e: self._cancel())
        top.protocol("WM_DELETE_WINDOW", self._cancel)
        self._changed()
        top.grab_set()

    # --- what _DeviceCard expects of its dialog ---

    def _watch(self, widget: tk.Widget) -> None:
        widget.bind("<KeyRelease>", lambda _e: self._changed(), add="+")
        widget.bind("<<ComboboxSelected>>", lambda _e: self._changed(), add="+")

    def _start_for(self, _device_id: str, _setting: str) -> object:
        return None  # The end state never ramps.

    # --- rows ---

    def _add_device_row(self, parent: ttk.Frame, index: int, role) -> None:
        frame = ttk.Frame(parent, padding=(0, 4, 4, 6)); frame.grid(row=index, column=0, sticky="ew")
        frame.columnconfigure(0, weight=1)
        safe = self._has_safe_state(role)
        title = ttk.Frame(frame); title.grid(row=0, column=0, sticky="w")
        ttk.Label(title, text=role.friendly_name, font=SECTION_FONT).pack(side="left")
        if not safe:
            ttk.Label(title, text="  no safe state: keeps its last settings unless set here",
                      foreground=WARNING_TEXT).pack(side="left")
        choices = [a for a in _ACTIONS if (a is not EndAction.SAFE or safe)
                   and (a is not EndAction.SET or role.capability in SETTINGS)]
        box = ttk.Combobox(frame, state="readonly", width=18, values=[END_ACTION_TEXT[a.value] for a in choices])
        box.grid(row=0, column=1, sticky="e")
        entry = self._initial.get(role.device_id, EndDevice(role.device_id, EndAction.SAFE))
        if not safe and entry.action is EndAction.SAFE:
            # With no safe state, "not listed" really means it stays as it is: say so.
            entry = EndDevice(role.device_id, EndAction.LEAVE)
        box.set(END_ACTION_TEXT[entry.action.value])
        self._actions[role.device_id] = entry.action
        card_frame = ttk.Frame(frame); card_frame.grid(row=1, column=0, columnspan=2, sticky="ew")
        card_frame.columnconfigure(0, weight=1)
        ttk.Separator(frame).grid(row=2, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        box.bind("<<ComboboxSelected>>", lambda _e, r=role: self._action_chosen(r))
        self._rows[role.device_id] = (box, card_frame)
        if entry.action is EndAction.SET:
            self._show_card(role, list(entry.assignments))

    def _show_card(self, role, assignments: list[Assignment]) -> None:
        _box, card_frame = self._rows[role.device_id]
        if role.device_id in self._cards:
            self._cards[role.device_id].frame.grid(); return
        if not assignments:
            # A supply's natural end-state setting is its output; otherwise the first setting.
            specs = device_settings(role.capability)
            spec = next((s for s in specs if s.name == "output_enabled"), specs[0])
            value = False if spec.name == "output_enabled" else default_value(spec)
            assignments = [Assignment(role.device_id, spec.name, value, unit_for(spec))]
        card = _DeviceCard(card_frame, self, role.device_id, assignments, fixed=True, allow_ramps=False)
        card.frame.grid(row=0, column=0, sticky="ew", padx=(16, 0))
        self._cards[role.device_id] = card

    def _action_chosen(self, role) -> None:
        box, _frame = self._rows[role.device_id]
        chosen = next(a for a in _ACTIONS if END_ACTION_TEXT[a.value] == box.get())
        previous = self._actions[role.device_id]
        warning = _SAFE_WARNINGS.get(role.capability) if chosen is EndAction.SAFE else None
        if warning and not messagebox.askokcancel("End state", warning.format(name=role.friendly_name),
                                                  icon="warning", parent=self._top):
            box.set(END_ACTION_TEXT[previous.value]); return
        if (chosen is EndAction.LEAVE and role.capability is DeviceCapability.DC_POWER_SUPPLY
                and not messagebox.askokcancel("End state", (
                    f"Leave {role.friendly_name} as it is?\n\nIf its output is on when the run ends or you "
                    "press Stop, it STAYS ON with current flowing through the cell until you turn it off by "
                    "hand."), icon="warning", parent=self._top)):
            box.set(END_ACTION_TEXT[previous.value]); return
        self._actions[role.device_id] = chosen
        if chosen is EndAction.SET:
            self._show_card(role, [])
        elif role.device_id in self._cards:
            self._cards[role.device_id].frame.grid_remove()
        self._changed()

    # --- result ---

    def _build(self) -> EndState:
        devices = []
        for role in self._listed:
            action = self._actions[role.device_id]
            if action is EndAction.SAFE:
                continue  # Not listed means safe state.
            assignments = tuple(self._cards[role.device_id].read()) if action is EndAction.SET else ()
            devices.append(EndDevice(role.device_id, action, assignments))
        return EndState(tuple(devices))

    def _changed(self) -> None:
        if not hasattr(self, "_error"):
            return
        try:
            end = self._build()
        except (ValueError, TypeError) as error:
            self._error.configure(text=str(error)); self._warning.configure(text=""); return
        self._error.configure(text="")
        warnings = end_state_warnings(end, self._roles, self._has_safe_state)
        self._warning.configure(text="\n".join("⚠ " + w for w in warnings))

    def _ok(self) -> None:
        try:
            end = self._build()
        except (ValueError, TypeError) as error:
            self._error.configure(text=str(error)); return
        live = [w for w in end_state_warnings(end, self._roles, self._has_safe_state) if "turned ON" in w]
        if live and not messagebox.askokcancel("End state", (
                "\n".join(live) + "\n\nThe supply would be left delivering current after the run ends or "
                "you press Stop. Keep this end state?"), icon="warning", parent=self._top):
            return
        self._close(end)

    def _cancel(self) -> None:
        self._close(None)

    def _close(self, end: EndState | None) -> None:
        if self._closed:
            return
        self._closed = True
        self._top.grab_release(); self._top.destroy()
        self._on_done(end)
