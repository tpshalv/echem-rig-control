import argparse
import tkinter as tk
from collections.abc import Sequence
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from rig_control.app_selection import load_app_selection
from rig_control.ui.common.theme import SECTION_FONT, TITLE_FONT
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
from rig_control.ui.operation.model import OperationViewModel
from rig_control.ui.operation.window import OperationWindow


class HomeWindow:
    """Single launcher and owner-facing view for the rig application."""

    def __init__(self, root: tk.Tk, view_model: HomeViewModel) -> None:
        self._root = root
        self._view_model = view_model
        self._operation_child: tk.Toplevel | None = None
        self._diagnostics_child: tk.Toplevel | None = None
        self._device_setup_child: tk.Toplevel | None = None
        self._setting_entries: dict[str, ttk.Entry] = {}
        root.title("Echem Rig Control")
        root.geometry("850x620")
        root.minsize(720, 520)
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

        files = ttk.LabelFrame(main, text="Active configuration", padding=10)
        files.grid(row=1, column=0, sticky="ew", pady=(0, 12))
        files.columnconfigure(1, weight=1)
        ttk.Label(files, text="Rig profile:").grid(row=0, column=0, sticky="w")
        self._profile_label = ttk.Label(files)
        self._profile_label.grid(row=0, column=1, sticky="w", padx=8)
        ttk.Button(files, text="Select…", command=self._select_profile).grid(
            row=0, column=2
        )
        ttk.Label(files, text="App settings:").grid(row=1, column=0, sticky="w")
        self._settings_label = ttk.Label(files)
        self._settings_label.grid(row=1, column=1, sticky="w", padx=8)
        ttk.Button(files, text="Select…", command=self._select_settings).grid(
            row=1, column=2
        )

        settings = ttk.LabelFrame(main, text="Application settings", padding=10)
        settings.grid(row=2, column=0, sticky="ew", pady=(0, 12))
        settings.columnconfigure(1, weight=1)
        for row, setting in enumerate(self._view_model.setting_rows()):
            ttk.Label(settings, text=f"{setting.label}:").grid(
                row=row, column=0, sticky="w", pady=3
            )
            entry = ttk.Entry(settings)
            entry.grid(row=row, column=1, sticky="ew", padx=8, pady=3)
            self._setting_entries[setting.key] = entry
            ttk.Label(settings, text=setting.description).grid(
                row=row, column=2, sticky="w", pady=3
            )
        setting_buttons = ttk.Frame(settings)
        setting_buttons.grid(
            row=len(self._setting_entries),
            column=0,
            columnspan=3,
            sticky="w",
            pady=(8, 0),
        )
        ttk.Button(setting_buttons, text="Apply", command=self._apply_settings).grid(
            row=0, column=0, padx=(0, 8)
        )
        ttk.Button(setting_buttons, text="Save", command=self._save_settings).grid(
            row=0, column=1, padx=(0, 8)
        )
        ttk.Button(
            setting_buttons,
            text="Save as…",
            command=self._save_settings_as,
        ).grid(row=0, column=2)

        launch = ttk.LabelFrame(main, text="Open feature", padding=10)
        launch.grid(row=3, column=0, sticky="ew")
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

        self._status = ttk.Label(main, text="", font=SECTION_FONT)
        self._status.grid(row=4, column=0, sticky="w", pady=(12, 0))

    def refresh(self) -> None:
        self._profile_label.configure(
            text=(
                f"{self._view_model.profile.friendly_name} — "
                f"{self._view_model.rig_profile_path}"
            )
        )
        self._settings_label.configure(
            text=(
                f"{self._view_model.settings.friendly_name} — "
                f"{self._view_model.settings_path}"
            )
        )
        for row in self._view_model.setting_rows():
            entry = self._setting_entries[row.key]
            entry.delete(0, "end")
            entry.insert(0, row.value)
        for feature, button in self._launch_buttons.items():
            button.configure(
                state=(
                    "normal"
                    if self._view_model.can_open_feature(feature)
                    else "disabled"
                )
            )

    def _select_profile(self) -> None:
        selected = filedialog.askopenfilename(
            parent=self._root,
            filetypes=(("TOML files", "*.toml"), ("All files", "*.*")),
        )
        if selected:
            self._show_result(self._view_model.select_rig_profile(selected))
            self.refresh()

    def _select_settings(self) -> None:
        selected = filedialog.askopenfilename(
            parent=self._root,
            filetypes=(("TOML files", "*.toml"), ("All files", "*.*")),
        )
        if selected:
            self._show_result(self._view_model.select_settings(selected))
            self.refresh()

    def _apply_settings(self) -> bool:
        result = self._view_model.apply_setting_text(
            {key: entry.get() for key, entry in self._setting_entries.items()}
        )
        self._show_result(result)
        self.refresh()
        return result.succeeded

    def _save_settings(self) -> None:
        if not self._apply_settings():
            return
        self._show_result(self._view_model.save_settings())
        self.refresh()

    def _save_settings_as(self) -> None:
        selected = filedialog.asksaveasfilename(
            parent=self._root,
            defaultextension=".toml",
            filetypes=(("TOML files", "*.toml"),),
        )
        if selected:
            if not self._apply_settings():
                return
            self._show_result(self._view_model.save_settings(selected))
            self.refresh()

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
        )
        window = OperationWindow(
            child,
            model,
            profile_name=session.profile.friendly_name,
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
        DeviceSetupWindow(
            self._device_setup_child,
            DeviceSetupViewModel(
                self._view_model.profile,
                profile_path=self._view_model.rig_profile_path,
            ),
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
        if self._device_setup_child is not None:
            self._device_setup_child.destroy()
            self._device_setup_child = None
        self._show_result(self._view_model.resume_after_device_setup())
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
