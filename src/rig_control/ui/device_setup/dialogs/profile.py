import tkinter as tk
from pathlib import Path
import re
from tkinter import filedialog, messagebox, ttk

from rig_control.app_paths import profiles_directory


from rig_control.ui.device_setup.dialogs.base import SetupDialog


class ProfileDialogs(SetupDialog):
    def _switch_profile(self) -> None:
        selected = filedialog.askopenfilename(
            parent=self._root,
            filetypes=(("TOML rig profiles", "*.toml"),),
        )
        if not selected:
            return
        self._record_result(self._view_model.switch_profile(selected))
        self.refresh()

    def _new_profile(self) -> None:
        dialog = tk.Toplevel(self._root)
        dialog.title("Create new rig")
        dialog.transient(self._root)
        dialog.grab_set()
        dialog.resizable(False, False)
        frame = ttk.Frame(dialog, padding=14)
        frame.grid(row=0, column=0, sticky="nsew")
        frame.columnconfigure(1, weight=1)
        friendly_name = tk.StringVar()
        profile_id = tk.StringVar()
        destination = tk.StringVar()
        destination.set(str(profiles_directory() / "rig-profile.toml"))
        for row, (label, variable) in enumerate(
            (
                ("Display name", friendly_name),
                ("Internal ID", profile_id),
                ("Profile filename", destination),
            )
        ):
            ttk.Label(frame, text=f"{label}:").grid(row=row, column=0, sticky="w")
            entry = ttk.Entry(frame, textvariable=variable, width=52)
            entry.grid(
                row=row, column=1, sticky="ew", padx=(8, 4), pady=3
            )
            if variable is destination:
                entry.bind(
                    "<FocusOut>",
                    lambda _event: update_id_from_filename(),
                )

        ttk.Label(
            frame,
            text="Shown in the app; spaces and capitals are allowed.",
        ).grid(row=0, column=2, sticky="w")
        ttk.Label(
            frame,
            text="Stable software label, for example main_echem_rig.",
        ).grid(row=1, column=2, sticky="w")

        def id_from_filename(filename: str) -> str:
            stem = Path(filename).stem.casefold()
            for prefix in ("rig-profile.", "rig_profile_", "rig-profile-"):
                if stem.startswith(prefix):
                    stem = stem[len(prefix):]
                    break
            value = re.sub(r"[^a-z0-9_-]+", "_", stem).strip("_-")
            if value and not value[0].isalpha():
                value = "rig_" + value
            return value

        def update_id_from_filename() -> None:
            derived = id_from_filename(destination.get())
            if derived:
                profile_id.set(derived)

        def browse() -> None:
            suggested_id = profile_id.get().strip() or id_from_filename(
                friendly_name.get()
            )
            selected = filedialog.asksaveasfilename(
                parent=dialog,
                defaultextension=".toml",
                initialfile=(
                    f"rig-profile.{suggested_id}.toml"
                    if suggested_id
                    else "rig-profile.toml"
                ),
                filetypes=(("TOML rig profiles", "*.toml"),),
            )
            if selected:
                destination.set(selected)
                update_id_from_filename()

        ttk.Button(frame, text="Browse…", command=browse).grid(
            row=2, column=2, pady=3
        )
        ttk.Label(
            frame,
            text=(
                "The new profile starts with no devices or connections. "
                "Devices can be added after creation."
            ),
            wraplength=480,
        ).grid(row=3, column=0, columnspan=3, sticky="w", pady=(8, 4))

        def create() -> None:
            result = self._view_model.create_new_profile(
                profile_id.get(),
                friendly_name.get(),
                destination.get(),
            )
            self._record_result(result)
            if result.succeeded:
                dialog.destroy()
                self.refresh()
            else:
                messagebox.showerror(
                    "Could not create rig",
                    result.technical_details or result.summary,
                    parent=dialog,
                )

        buttons = ttk.Frame(frame)
        buttons.grid(row=4, column=0, columnspan=3, sticky="e", pady=(10, 0))
        ttk.Button(buttons, text="Cancel", command=dialog.destroy).grid(
            row=0, column=0, padx=(0, 8)
        )
        ttk.Button(buttons, text="Create rig", command=create).grid(row=0, column=1)

    def _save_profile_as(self) -> None:
        selected = filedialog.asksaveasfilename(
            parent=self._root,
            defaultextension=".toml",
            filetypes=(("TOML rig profiles", "*.toml"),),
        )
        if not selected:
            return
        self._record_result(self._view_model.save_profile_as(selected))
        self.refresh()

    def _reload_profile(self) -> None:
        if not messagebox.askyesno(
            "Reload profile",
            "Reload this profile from disk? Any unsaved dialog entries will "
            "not be applied.",
            parent=self._root,
        ):
            return
        self._record_result(
            self._view_model.switch_profile(self._view_model.profile_path)
        )
        self.refresh()

    def _edit_profile(self) -> None:
        dialog = tk.Toplevel(self._root)
        dialog.title("Edit rig profile details")
        dialog.transient(self._root)
        dialog.grab_set()
        frame = ttk.Frame(dialog, padding=14)
        frame.grid(row=0, column=0, sticky="nsew")
        values = {
            "Display name": tk.StringVar(
                value=self._view_model.profile.friendly_name
            ),
            "Internal ID": tk.StringVar(value=self._view_model.profile.profile_id),
        }
        for row, (label, variable) in enumerate(values.items()):
            ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w")
            ttk.Entry(frame, textvariable=variable, width=42).grid(
                row=row, column=1, sticky="ew", padx=(8, 0), pady=3
            )
        ttk.Label(
            frame,
            text="Shown in the app; spaces and capitals are allowed.",
        ).grid(row=0, column=2, sticky="w", padx=(8, 0))
        ttk.Label(
            frame,
            text="Stable software label, for example main_echem_rig.",
        ).grid(row=1, column=2, sticky="w", padx=(8, 0))

        def save() -> None:
            result = self._view_model.update_profile_identity(
                values["Internal ID"].get(),
                values["Display name"].get(),
            )
            self._record_result(result)
            if result.succeeded:
                dialog.destroy()
                self.refresh()

        ttk.Button(frame, text="Save", command=save).grid(
            row=2, column=1, sticky="e", pady=(10, 0)
        )
