from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from math import isfinite
from types import MappingProxyType


type AppSettingValue = str | int | float | bool


@dataclass(frozen=True, slots=True)
class SettingDefinition:
    key: str
    label: str
    description: str
    default: AppSettingValue
    validator: Callable[[object], AppSettingValue]


def _positive_number(value: object) -> AppSettingValue:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError("must be a number")
    number = float(value)
    if not isfinite(number) or number <= 0:
        raise ValueError("must be finite and greater than zero")
    return number


def _history_limit(value: object) -> AppSettingValue:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("must be an integer")
    if value < 2 or value > 100_000:
        raise ValueError("must be between 2 and 100000")
    return value


def _non_empty_text(value: object) -> AppSettingValue:
    if not isinstance(value, str):
        raise TypeError("must be text")
    if not value.strip():
        raise ValueError("cannot be empty")
    return value


def _boolean(value: object) -> AppSettingValue:
    if not isinstance(value, bool):
        raise TypeError("must be true or false")
    return value


SETTING_DEFINITIONS = (
    SettingDefinition(
        key="trend_history_readings",
        label="Default trend history (readings)",
        description="Initial retained history for each newly opened trend chart.",
        default=500,
        validator=_history_limit,
    ),
    SettingDefinition(
        key="publish_interval_seconds",
        label="Screen publish interval (seconds)",
        description=(
            "How often cached readings redraw on screen; recording remains "
            "at each device's own measurement interval."
        ),
        default=1.0,
        validator=_positive_number,
    ),
    SettingDefinition(
        key="default_output_directory",
        label="Default recording output folder",
        description="Folder initially offered when starting an experiment recording.",
        default="experiments",
        validator=_non_empty_text,
    ),
    SettingDefinition(
        key="technical_log_path",
        label="Technical log file",
        description="Rotating application event-log location.",
        default="logs/rig-control.log",
        validator=_non_empty_text,
    ),
    SettingDefinition(
        key="power_supply_default_current_amps",
        label="Default manual current setpoint (A)",
        description=(
            "Low, everyday starting current applied when manual power-supply "
            "control begins. Always clamped to the active current ceiling."
        ),
        default=20.0,
        validator=_positive_number,
    ),
    SettingDefinition(
        key="power_supply_default_voltage_volts",
        label="Default manual voltage setpoint (V)",
        description=(
            "Low, everyday starting voltage applied when manual power-supply "
            "control begins."
        ),
        default=10.0,
        validator=_positive_number,
    ),
    SettingDefinition(
        key="power_supply_high_current_mode",
        label="High current mode",
        description=(
            "Allows the manual power-supply current ceiling to be raised "
            "above the normal 45 A wiring rating. Only enable this if the "
            "cables in use are actually rated for it."
        ),
        default=False,
        validator=_boolean,
    ),
    SettingDefinition(
        key="power_supply_wiring_current_ceiling_amps",
        label="High current mode ceiling (A)",
        description=(
            "Current ceiling used only while High current mode is enabled. "
            "Has no effect and is not shown anywhere else while High "
            "current mode is off, when the ceiling is always 45 A."
        ),
        default=45.0,
        validator=_positive_number,
    ),
)
_DEFINITIONS_BY_KEY = {
    definition.key: definition for definition in SETTING_DEFINITIONS
}


@dataclass(frozen=True, slots=True)
class AppSettings:
    """Named, swappable application-level preferences."""

    settings_id: str
    friendly_name: str
    values: Mapping[str, AppSettingValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for value, name in (
            (self.settings_id, "Settings ID"),
            (self.friendly_name, "Settings name"),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} cannot be empty")

        supplied = dict(self.values)
        unknown = set(supplied) - set(_DEFINITIONS_BY_KEY)
        if unknown:
            raise ValueError(
                "Unknown application setting(s): " + ", ".join(sorted(unknown))
            )

        validated: dict[str, AppSettingValue] = {}
        for definition in SETTING_DEFINITIONS:
            value = supplied.get(definition.key, definition.default)
            try:
                validated[definition.key] = definition.validator(value)
            except (TypeError, ValueError) as error:
                raise type(error)(
                    f"Application setting {definition.key!r} {error}"
                ) from error
        object.__setattr__(self, "values", MappingProxyType(validated))

    @property
    def publish_interval_seconds(self) -> float:
        return float(self.values["publish_interval_seconds"])

    @property
    def technical_log_path(self) -> str:
        return str(self.values["technical_log_path"])

    @property
    def default_output_directory(self) -> str:
        return str(self.values["default_output_directory"])

    @property
    def trend_history_readings(self) -> int:
        return int(self.values["trend_history_readings"])

    @property
    def power_supply_default_current_amps(self) -> float:
        return float(self.values["power_supply_default_current_amps"])

    @property
    def power_supply_default_voltage_volts(self) -> float:
        return float(self.values["power_supply_default_voltage_volts"])

    @property
    def power_supply_high_current_mode(self) -> bool:
        return bool(self.values["power_supply_high_current_mode"])

    @property
    def power_supply_wiring_current_ceiling_amps(self) -> float:
        return float(self.values["power_supply_wiring_current_ceiling_amps"])


def default_app_settings() -> AppSettings:
    return AppSettings(
        settings_id="default",
        friendly_name="Default application settings",
    )


def parse_setting_text(key: str, text: str) -> AppSettingValue:
    try:
        definition = _DEFINITIONS_BY_KEY[key]
    except KeyError as error:
        raise KeyError(f"Unknown application setting {key!r}") from error
    if isinstance(definition.default, bool):
        # Checked before int: bool is a subclass of int in Python, so this
        # branch would otherwise be unreachable for boolean settings.
        normalized = text.strip().casefold()
        if normalized not in {"true", "false"}:
            raise ValueError(f"Application setting {key!r} must be true or false")
        value: object = normalized == "true"
    elif isinstance(definition.default, float):
        try:
            value = float(text)
        except ValueError as error:
            raise ValueError(f"Application setting {key!r} must be a number") from error
    elif isinstance(definition.default, int):
        try:
            value = int(text)
        except ValueError as error:
            raise ValueError(f"Application setting {key!r} must be an integer") from error
    else:
        value = text
    return definition.validator(value)
