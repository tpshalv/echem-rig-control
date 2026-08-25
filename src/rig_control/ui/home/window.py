import argparse
import tkinter as tk
from collections.abc import Sequence
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from rig_control.app_selection import load_app_selection
from rig_control.ui.common.theme import SECTION_FONT, TITLE_FONT, apply_blueprint_theme
from rig_control.ui.device_setup.model import DeviceSetupViewModel
from rig_control.ui.device_setup.window import DeviceSetupWindow
from rig_control.ui.diagnostics.model import DiagnosticViewModel
from rig_control.ui.diagnostics.window import DiagnosticWindow
from rig_control.ui.home.model import (
    FEATURE_DEVICE_SETUP,
    FEATURE_DIAGNOSTICS,
    FEATURE_OPERATION,
    HomeActionResult,
    HomeViewModel,
)
from rig_control.ui.home.settings_window import SettingsWindow
from rig_control.ui.operation.model import OperationViewModel
from rig_control.ui.operation.window import OperationWindow


class HomeWindow:
    """Single launcher and owner-facing view for the rig application."""

    def __init__(self, root: tk.Tk, view_model: HomeViewModel) -> None:
        apply_blueprint_theme(root)
        self._root = root
        self._view_model = view_model
        self._operation_child: tk.Toplevel | None = None
        self._diagnostics_child: tk.Toplevel | None = None
        self._device_setup_child: tk.Toplevel | None = None
        self._settings_child: tk.Toplevel | None = None
        root.title("Echem Rig Control")
        root.geometry("1100x620")
        root.minsize(900, 520)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(0, weight=1)
        self._create_widgets()
        self.refresh()

    def _create_widgets(self) -> None:
        main = ttk.Frame(self._root, padding=16)
        main.grid(row=0, column=0, sticky="nsew")
        main.columnconfigure(0, weight=1)

        ttk.Label(main, text="Echem rig control", font=TITLE_FONT).grid(
            row=0, column=0, sticky="w", pady=(0, 12)
        )
        files = ttk.LabelFrame(main, text="Active rig", padding=10)
        files.grid(row=1, column=0, sticky="ew", pady=(0, 12))
        files.columnconfigure(1, weight=1)
        ttk.Label(files, text="Rig profile:").grid(row=0, column=0, sticky="w")
        self._profile_label = ttk.Label(files)
        self._profile_label.grid(row=0, column=1, sticky="w", padx=8)
        ttk.Button(files, text="Select…", command=self._select_profile).grid(
            row=0, column=2
        )

        launch = ttk.LabelFrame(main, text="Open feature", padding=10)
        launch.grid(row=2, column=0, sticky="ew")
        self._launch_buttons = {
            FEATURE_DEVICE_SETUP: ttk.Button(
                launch, text="Device Setup", command=self._open_device_setup
            ),
            FEATURE_DIAGNOSTICS: ttk.Button(
                launch, text="Diagnostics", command=self._open_diagnostics
            ),
            FEATURE_OPERATION: ttk.Button(
                launch, text="Operation", command=self._open_operation
            ),
        }
        for column, button in enumerate(self._launch_buttons.values()):
            button.grid(row=0, column=column, padx=(0, 8))
        self._settings_button = ttk.Button(
            launch, text="Settings", command=self._open_settings
        )
        self._settings_button.grid(row=0, column=3)

        self._status = ttk.Label(main, text="", font=SECTION_FONT)
        self._status.grid(row=3, column=0, sticky="w", pady=(12, 0))

    def refresh(self) -> None:
        self._profile_label.configure(
            text=(
                f"{self._view_model.profile.friendly_name} — "
                f"{self._view_model.rig_profile_path}"
            )
        )
        for feature, button in self._launch_buttons.items():
            button.configure(
                state=(
                    "normal"
                    if self._view_model.can_open_feature(feature)
                    else "disabled"
                )
            )
        self._settings_button.configure(
            state="disabled" if self._view_model.feature_active else "normal"
        )

    def _select_profile(self) -> None:
        selected = filedialog.askopenfilename(
            parent=self._root,
            filetypes=(("TOML files", "*.toml"), ("All files", "*.*")),
        )
        if selected:
            self._show_result(self._view_model.select_rig_profile(selected))
            self.refresh()

    def _open_settings(self) -> None:
        if self._settings_child is not None and self._settings_child.winfo_exists():
            self._settings_child.lift()
            self._settings_child.focus_force()
            return
        if self._view_model.feature_active:
            self._show_result(
                HomeActionResult(False, "Close active feature screens first.")
            )
            return
        self._settings_child = tk.Toplevel(self._root)
        SettingsWindow(
            self._settings_child,
            self._view_model,
            on_change=self.refresh,
        )

        def close() -> None:
            if self._settings_child is not None:
                self._settings_child.destroy()
                self._settings_child = None
            self.refresh()

        self._settings_child.protocol("WM_DELETE_WINDOW", close)

    def _open_diagnostics(self) -> None:
        child = self._begin_child(FEATURE_DIAGNOSTICS)
        if child is None:
            return
        self._diagnostics_child = child
        window = DiagnosticWindow(
            child,
            DiagnosticViewModel(
                self._view_model.session.device_manager,
                self._view_model.session.runtime_diagnostics,
            ),
        )

        def close() -> None:
            window.cancel_updates()
            self._close_diagnostics()

        child.protocol("WM_DELETE_WINDOW", close)

    def _open_operation(self) -> None:
        child = self._begin_child(FEATURE_OPERATION)
        if child is None:
            return
        self._operation_child = child
        session = self._view_model.session
        model = OperationViewModel(
            session.device_manager,
            session.polling_service,
            session.experiment_recorder,
            session.control_service,
            profile_id=session.profile.profile_id,
            profile=session.profile,
            history_limit=session.settings.trend_history_readings,
        )
        window = OperationWindow(
            child,
            model,
            profile_name=session.profile.friendly_name,
            default_output_directory=session.settings.default_output_directory,
            event_sink=session.technical_log.record,
        )
        session.runtime_diagnostics.register_metric_provider(
            "operation_ui",
            window.diagnostic_metrics,
        )

        def close() -> None:
            window.cancel_updates()
            model.shutdown()
            session.runtime_diagnostics.unregister_metric_provider("operation_ui")
            self._close_operation()

        child.protocol("WM_DELETE_WINDOW", close)

    def _open_device_setup(self) -> None:
        result = self._view_model.suspend_for_device_setup()
        if not result.succeeded:
            self._show_result(result)
            return
        self._device_setup_child = tk.Toplevel(self._root)
        self._device_setup_model = DeviceSetupViewModel(
            self._view_model.profile,
            profile_path=self._view_model.rig_profile_path,
        )
        DeviceSetupWindow(
            self._device_setup_child,
            self._device_setup_model,
        )
        self._device_setup_child.protocol(
            "WM_DELETE_WINDOW", self._close_device_setup
        )
        self.refresh()

    def _begin_child(self, feature: str) -> tk.Toplevel | None:
        result = self._view_model.begin_feature(feature)
        if not result.succeeded:
            self._show_result(result)
            return None
        child = tk.Toplevel(self._root)
        self.refresh()
        return child

    def _close_operation(self) -> None:
        if self._operation_child is not None:
            self._operation_child.destroy()
            self._operation_child = None
        self._view_model.end_feature(FEATURE_OPERATION)
        self.refresh()

    def _close_diagnostics(self) -> None:
        if self._diagnostics_child is not None:
            self._diagnostics_child.destroy()
            self._diagnostics_child = None
        self._view_model.end_feature(FEATURE_DIAGNOSTICS)
        self.refresh()

    def _close_device_setup(self) -> None:
        profile_path = getattr(
            getattr(self, "_device_setup_model", None),
            "profile_path",
            None,
        )
        if self._device_setup_child is not None:
            self._device_setup_child.destroy()
            self._device_setup_child = None
        self._show_result(
            self._view_model.resume_after_device_setup(profile_path)
        )
        self._device_setup_model = None
        self.refresh()

    def _show_result(self, result: HomeActionResult) -> None:
        self._status.configure(text=result.summary)
        if not result.succeeded:
            messagebox.showerror("Action failed", result.summary, parent=self._root)


def main(arguments: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Start Echem Rig Control")
    parser.add_argument("--profile")
    parser.add_argument("--settings")
    parser.add_argument("--selection", default="app-selection.toml")
    parsed = parser.parse_args(arguments)
    profile_path = parsed.profile
    settings_path = parsed.settings
    if (profile_path is None or settings_path is None) and Path(
        parsed.selection
    ).exists():
        selection = load_app_selection(parsed.selection)
        profile_path = profile_path or selection.rig_profile_file
        settings_path = settings_path or selection.settings_file
    profile_path = profile_path or "rig-profile.simulation.toml"
    settings_path = settings_path or "app-settings.default.toml"

    model = HomeViewModel(
        rig_profile_path=profile_path,
        settings_path=settings_path,
        selection_path=parsed.selection,
    )
    root = tk.Tk()
    HomeWindow(root, model)

    def close() -> None:
        if model.feature_active:
            messagebox.showwarning(
                "Close feature first",
                "Close the active feature screen before exiting.",
                parent=root,
            )
            return
        failures = model.close()
        if failures:
            messagebox.showerror("Shutdown problems", "\n".join(failures), parent=root)
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", close)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
