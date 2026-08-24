from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from rig_control.app_selection import AppSelection, write_app_selection
from rig_control.app_settings import (
    AppSettingValue,
    AppSettings,
    SETTING_DEFINITIONS,
    parse_setting_text,
)
from rig_control.app_settings_loading import load_app_settings
from rig_control.app_settings_writing import write_app_settings
from rig_control.application_session import ApplicationSession
from rig_control.rig_profile import RigProfile
from rig_control.rig_profile_loading import load_rig_profile


type SessionFactory = Callable[..., ApplicationSession]

FEATURE_OPERATION = "operation"
FEATURE_DIAGNOSTICS = "diagnostics"
FEATURE_DEVICE_SETUP = "device_setup"
_FEATURE_NAMES = frozenset(
    {FEATURE_OPERATION, FEATURE_DIAGNOSTICS, FEATURE_DEVICE_SETUP}
)


@dataclass(frozen=True, slots=True)
class HomeActionResult:
    succeeded: bool
    summary: str


@dataclass(frozen=True, slots=True)
class AppSettingRow:
    key: str
    label: str
    description: str
    value: str


class HomeViewModel:
    """Own configuration selection and the one active application session."""

    def __init__(
        self,
        *,
        rig_profile_path: str | Path,
        settings_path: str | Path,
        selection_path: str | Path = "app-selection.toml",
        session_factory: SessionFactory = ApplicationSession,
    ) -> None:
        self._selection_path = Path(selection_path)
        self._session_factory = session_factory
        self._active_features: set[str] = set()
        self._rig_profile_path = Path(rig_profile_path)
        self._settings_path = Path(settings_path)
        self._profile = load_rig_profile(self._rig_profile_path)
        self._settings = load_app_settings(self._settings_path)
        self._session = self._build_session()

    @property
    def session(self) -> ApplicationSession:
        return self._session

    @property
    def profile(self) -> RigProfile:
        return self._profile

    @property
    def settings(self) -> AppSettings:
        return self._settings

    @property
    def rig_profile_path(self) -> Path:
        return self._rig_profile_path

    @property
    def settings_path(self) -> Path:
        return self._settings_path

    @property
    def feature_active(self) -> bool:
        return bool(self._active_features)

    def is_feature_active(self, feature: str) -> bool:
        self._validate_feature(feature)
        return feature in self._active_features

    def can_open_feature(self, feature: str) -> bool:
        self._validate_feature(feature)
        if feature in self._active_features:
            return False
        if FEATURE_DEVICE_SETUP in self._active_features:
            return False
        return feature != FEATURE_DEVICE_SETUP or not self._active_features

    def setting_rows(self) -> tuple[AppSettingRow, ...]:
        return tuple(
            AppSettingRow(
                definition.key,
                definition.label,
                definition.description,
                str(self._settings.values[definition.key]),
            )
            for definition in SETTING_DEFINITIONS
        )

    def apply_setting_text(
        self,
        values: Mapping[str, str],
    ) -> HomeActionResult:
        if self.feature_active:
            return HomeActionResult(False, "Close the active feature screen first.")
        try:
            parsed: dict[str, AppSettingValue] = {
                key: parse_setting_text(key, text)
                for key, text in values.items()
            }
            settings = AppSettings(
                self._settings.settings_id,
                self._settings.friendly_name,
                parsed,
            )
            self._replace_session(settings=settings)
        except Exception as error:
            return HomeActionResult(False, f"Could not apply settings: {error}")
        return HomeActionResult(True, "Application settings applied.")

    def save_settings(self, path: str | Path | None = None) -> HomeActionResult:
        destination = Path(path) if path is not None else self._settings_path
        try:
            write_app_settings(self._settings, destination)
            self._settings_path = destination
            self._remember_selection()
        except Exception as error:
            return HomeActionResult(False, f"Could not save settings: {error}")
        return HomeActionResult(True, f"Saved settings to {destination}.")

    def select_settings(self, path: str | Path) -> HomeActionResult:
        if self.feature_active:
            return HomeActionResult(False, "Close the active feature screen first.")
        try:
            selected_path = Path(path)
            settings = load_app_settings(selected_path)
            self._replace_session(settings=settings, settings_path=selected_path)
            self._remember_selection()
        except Exception as error:
            return HomeActionResult(False, f"Could not load settings: {error}")
        return HomeActionResult(True, f"Loaded settings {settings.friendly_name!r}.")

    def select_rig_profile(self, path: str | Path) -> HomeActionResult:
        if self.feature_active:
            return HomeActionResult(False, "Close the active feature screen first.")
        try:
            selected_path = Path(path)
            profile = load_rig_profile(selected_path)
            self._replace_session(profile=profile, profile_path=selected_path)
            self._remember_selection()
        except Exception as error:
            return HomeActionResult(False, f"Could not load rig profile: {error}")
        return HomeActionResult(True, f"Loaded rig profile {profile.friendly_name!r}.")

    def begin_feature(self, feature: str) -> HomeActionResult:
        self._validate_feature(feature)
        if feature in self._active_features:
            return HomeActionResult(False, f"The {feature} screen is already open.")
        if FEATURE_DEVICE_SETUP in self._active_features:
            return HomeActionResult(False, "Device Setup must be closed first.")
        if feature == FEATURE_DEVICE_SETUP and self._active_features:
            return HomeActionResult(
                False,
                "Close Operation and Diagnostics before opening Device Setup.",
            )
        self._active_features.add(feature)
        return HomeActionResult(True, f"{feature} screen opened.")

    def end_feature(self, feature: str) -> None:
        self._validate_feature(feature)
        self._active_features.discard(feature)

    def suspend_for_device_setup(self) -> HomeActionResult:
        result = self.begin_feature(FEATURE_DEVICE_SETUP)
        if not result.succeeded:
            return result
        failures = self._session.close()
        if failures:
            self._active_features.discard(FEATURE_DEVICE_SETUP)
            return HomeActionResult(
                False,
                "Could not close the hardware session: " + "; ".join(failures),
            )
        return HomeActionResult(True, "Hardware session released for Device Setup.")

    def resume_after_device_setup(self) -> HomeActionResult:
        try:
            self._profile = load_rig_profile(self._rig_profile_path)
            self._session = self._build_session()
        except Exception as error:
            self._active_features.discard(FEATURE_DEVICE_SETUP)
            return HomeActionResult(False, f"Could not rebuild session: {error}")
        self._active_features.discard(FEATURE_DEVICE_SETUP)
        return HomeActionResult(True, "Hardware session rebuilt from saved profile.")

    def close(self) -> tuple[str, ...]:
        return self._session.close()

    def _replace_session(
        self,
        *,
        profile: RigProfile | None = None,
        settings: AppSettings | None = None,
        profile_path: Path | None = None,
        settings_path: Path | None = None,
    ) -> None:
        next_profile = profile if profile is not None else self._profile
        next_settings = settings if settings is not None else self._settings
        next_profile_path = (
            profile_path if profile_path is not None else self._rig_profile_path
        )
        next_settings_path = (
            settings_path if settings_path is not None else self._settings_path
        )
        replacement = self._session_factory(
            next_profile,
            next_settings,
            settings_directory=next_settings_path.parent,
        )
        failures = self._session.close()
        if failures:
            replacement.close()
            raise RuntimeError("; ".join(failures))
        self._profile = next_profile
        self._settings = next_settings
        self._rig_profile_path = next_profile_path
        self._settings_path = next_settings_path
        self._session = replacement

    def _build_session(self) -> ApplicationSession:
        return self._session_factory(
            self._profile,
            self._settings,
            settings_directory=self._settings_path.parent,
        )

    def _remember_selection(self) -> None:
        write_app_selection(
            AppSelection(
                settings_file=str(self._settings_path),
                rig_profile_file=str(self._rig_profile_path),
            ),
            self._selection_path,
        )

    @staticmethod
    def _validate_feature(feature: str) -> None:
        if feature not in _FEATURE_NAMES:
            raise ValueError(f"Unknown feature screen {feature!r}")
