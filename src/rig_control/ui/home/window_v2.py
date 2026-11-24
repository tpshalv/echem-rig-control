"""The v2 launcher: the classic home window with the v2 look and Operation screen.

Started with ``echem-rig-control --ui v2`` (or ``ECHEM_RIG_UI=v2``).  Every
other screen (Device Setup, Diagnostics, Settings) is the classic one, drawn
in the v2 theme.  To drop v2, delete the ``*_v2`` modules and the ``--ui``
option; to adopt it, make it the default and delete the classic ones.

The launcher itself is a small window: the active rig, then one tile per
feature saying what it is for, with Operation as the main way in.
"""

from pathlib import Path
from tkinter import ttk

from rig_control.build_stamp import build_stamp
from rig_control.ui.common.theme_v2 import apply_v2_theme
from rig_control.ui.home.model import FEATURE_DEVICE_SETUP, FEATURE_DIAGNOSTICS, FEATURE_OPERATION
from rig_control.ui.home.window import HomeWindow
from rig_control.ui.operation.window_v2 import OperationWindowV2

_TILES = (
    (FEATURE_OPERATION, "Operation", "Monitor and set the rig, record runs and build recipes.",
     "_open_operation", "Accent.TButton"),
    (FEATURE_DEVICE_SETUP, "Device setup", "Add, edit and check the devices in this rig profile.",
     "_open_device_setup", "TButton"),
    (FEATURE_DIAGNOSTICS, "Diagnostics", "Test connections, read settings and watch runtime health.",
     "_open_diagnostics", "TButton"),
)


class HomeWindowV2(HomeWindow):
    apply_theme = staticmethod(apply_v2_theme)
    operation_window_class = OperationWindowV2

    def _create_widgets(self) -> None:
        root = self._root
        stamp = build_stamp()
        root.title("Echem Rig Control" + (f"  -  build {stamp}" if stamp else "") + "  (v2 layout)")
        root.geometry("780x290")
        root.minsize(640, 280)
        main = ttk.Frame(root, padding=(20, 16, 20, 14))
        main.grid(row=0, column=0, sticky="nsew")
        main.columnconfigure(0, weight=1)

        ttk.Label(main, text="Echem rig control", style="Title.TLabel").grid(row=0, column=0, sticky="w")

        rig = ttk.Frame(main, style="Card.TFrame", padding=(12, 8))
        rig.grid(row=1, column=0, sticky="ew", pady=(10, 12))
        rig.columnconfigure(1, weight=1)
        ttk.Label(rig, text="Rig", style="Muted.Card.TLabel").grid(row=0, column=0, rowspan=2,
                                                                     sticky="nw", padx=(0, 12))
        self._profile_name = ttk.Label(rig, style="Heading.Card.TLabel")
        self._profile_name.grid(row=0, column=1, sticky="w")
        self._profile_path = ttk.Label(rig, style="Caption.Card.TLabel")
        self._profile_path.grid(row=1, column=1, sticky="w")
        ttk.Button(rig, text="Change…", command=self._select_profile).grid(row=0, column=2, rowspan=2)
        # The classic refresh writes the profile here; kept so it has somewhere to go.
        self._profile_label = ttk.Label(rig)

        tiles = ttk.Frame(main)
        tiles.grid(row=2, column=0, sticky="nsew")
        self._launch_buttons = {}
        for column, (feature, title, blurb, method, style) in enumerate(_TILES):
            tiles.columnconfigure(column, weight=1, uniform="tile")
            tile = ttk.Frame(tiles, style="Card.TFrame", padding=(12, 10))
            tile.grid(row=0, column=column, sticky="nsew", padx=(0 if column == 0 else 8, 0))
            tile.columnconfigure(0, weight=1)
            tile.rowconfigure(1, weight=1)
            ttk.Label(tile, text=title, style="Heading.Card.TLabel").grid(row=0, column=0, sticky="w")
            ttk.Label(tile, text=blurb, style="Muted.Card.TLabel", wraplength=200,
                      justify="left").grid(row=1, column=0, sticky="nw", pady=(4, 10))
            button = ttk.Button(tile, text=f"Open {title.lower()}", style=style,
                                command=getattr(self, method))
            button.grid(row=2, column=0, sticky="w")
            self._launch_buttons[feature] = button

        footer = ttk.Frame(main)
        footer.grid(row=3, column=0, sticky="ew", pady=(12, 0))
        footer.columnconfigure(1, weight=1)
        self._settings_button = ttk.Button(footer, text="Settings…", command=self._open_settings)
        self._settings_button.grid(row=0, column=0, sticky="w")
        self._status = ttk.Label(footer, text="", style="Muted.TLabel")
        self._status.grid(row=0, column=1, sticky="w", padx=(12, 0))

    def refresh(self) -> None:
        super().refresh()
        path = Path(str(self._view_model.rig_profile_path)).resolve()
        self._profile_name.configure(text=self._view_model.profile.friendly_name)
        self._profile_path.configure(text=f"{path.name}   ·   {path.parent}")
