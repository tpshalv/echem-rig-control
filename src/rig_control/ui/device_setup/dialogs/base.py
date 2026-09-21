"""Shared dependencies passed explicitly to Device Setup dialogs."""

from collections.abc import Callable
from dataclasses import dataclass
import tkinter as tk

from rig_control.ui.device_setup.model import DeviceSetupViewModel
from rig_control.ui.device_setup.types import ReadinessCheckResult


@dataclass(frozen=True)
class DialogContext:
    root: tk.Misc
    view_model: DeviceSetupViewModel
    refresh: Callable[[], None]
    record_result: Callable[[ReadinessCheckResult], None]
    selected_device_id: Callable[[], str | None]


class SetupDialog:
    def __init__(self, context: DialogContext) -> None:
        self._root = context.root
        self._view_model = context.view_model
        self.refresh = context.refresh
        self._record_result = context.record_result
        self._selected_device_id = context.selected_device_id
