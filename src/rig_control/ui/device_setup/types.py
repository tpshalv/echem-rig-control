from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path


from rig_control.devices.alicat.configuration import AlicatMfcConfiguration
from rig_control.devices.ametek_asterion.configuration import AmetekAsterionConfiguration
from rig_control.devices.ametek_asterion.protocol import AmetekAsterionIdentity
from rig_control.devices.keithley_2260b.configuration import Keithley2260BConfiguration
from rig_control.devices.keithley_2280s.configuration import Keithley2280SConfiguration
from rig_control.devices.kamoer_m1_stp.configuration import KamoerM1StpConfiguration
from rig_control.devices.ohaus_guardian_5000.configuration import GuardianConfiguration
from rig_control.devices.atlas_ezo_hum.configuration import EzoHumConfiguration
from rig_control.diagnostics.kamoer_m1_stp import KamoerM1StpDiagnosticResult
from rig_control.diagnostics.ohaus_guardian import GuardianDiagnosticResult
from rig_control.diagnostics.alicat import AlicatDiagnosticResult, DiscoveredAlicat
from rig_control.diagnostics.esp32 import Esp32DiscoveryResult, Esp32ReadinessResult
from rig_control.devices.keithley_2260b.protocol import KeithleyIdentity
from rig_control.rig_profile import RigProfile


SCPI_POWER_SUPPLY_DRIVERS = {
    "keithley_2260b": {
        "display_name": "Keithley 2260B",
        "connection_prefix": "keithley",
        "manufacturer": "Keithley Instruments",
        "model": "2260B-30-108",
        "default_port": 2268,
        "default_voltage": 30.0,
        "default_current": 108.0,
        "default_power": 1080.0,
    },
    "keithley_2280s": {
        "display_name": "Keithley 2280S-32-6",
        "connection_prefix": "keithley",
        "manufacturer": "Keithley Instruments",
        "model": "2280S-32-6",
        "default_port": 5025,
        "default_voltage": 32.0,
        "default_current": 6.0,
        "default_power": 192.0,
    },
    "ametek_asterion": {
        "display_name": "AMETEK Sorensen Asterion DC",
        "connection_prefix": "ametek_asterion",
        "manufacturer": "AMETEK",
        "model": "Asterion DC",
        "default_port": 5025,
        "default_voltage": 30.0,
        "default_current": 5.0,
        "default_power": 100.0,
    },
}

SCPI_POWER_SUPPLY_DRIVER_LABELS = tuple(
    str(metadata["display_name"])
    for metadata in SCPI_POWER_SUPPLY_DRIVERS.values()
)
SCPI_POWER_SUPPLY_LABEL_TO_DRIVER = {
    str(metadata["display_name"]): driver
    for driver, metadata in SCPI_POWER_SUPPLY_DRIVERS.items()
}


@dataclass(frozen=True, slots=True)
class SerialPortInfo:
    device: str
    description: str
    hardware_id: str


@dataclass(frozen=True, slots=True)
class DeviceReadinessRow:
    device_id: str
    friendly_name: str
    device_type: str
    connection: str
    measurement_interval: str
    readiness: str
    enabled: bool


@dataclass(frozen=True, slots=True)
class EditDeviceRequest:
    device_id: str
    friendly_name: str
    enabled: bool
    required: bool
    system: str
    poll_interval_seconds: float | None
    connection_parameters: dict[str, object]
    device_connection_parameters: dict[str, object]
    settings: dict[str, object]
    channel_labels: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ReadinessCheckResult:
    succeeded: bool
    summary: str
    technical_details: str = ""
    #: Non-empty when the operator must acknowledge something before the
    #: action can be completed. The caller shows this text and retries.
    requires_acknowledgement: str = ""


#: Shown once while a detected back-pressure controller is being added, and
#: saved with the device so ordinary reconnects do not ask again.
BPR_INSTALLATION_WARNING = (
    "This Alicat reports back-pressure control configuration. The software "
    "cannot detect whether its physical valve position and plumbing are "
    "correct. Check that the valve is downstream of the sensing section and "
    "the instrument is installed for back-pressure regulation before continuing."
)


@dataclass(frozen=True, slots=True)
class AlicatScanRow:
    """One scanned address, carrying its structured detected role."""

    address: str
    raw_response: str
    configured_device_id: str | None = None
    configured_kind: str | None = None
    model: str | None = None
    serial_number: str | None = None
    detected_role: str | None = None
    is_controller: bool | None = None
    setpoint_unit: str | None = None
    maximum_setpoint: float | None = None
    inferred_maximum_flow_sccm: float | None = None
    control_description: str = "Control mode could not be read"
    configuration_error: str = ""

    @property
    def configuration_status(self) -> str:
        return "Already added" if self.configured_device_id else "New device"

    @property
    def usable(self) -> bool:
        return self.detected_role is not None or self.is_controller is False

    @property
    def detected_type(self) -> str:
        if self.detected_role == "bpr":
            return "BPR"
        if self.detected_role == "mfc":
            return "MFC"
        if self.is_controller is False:
            return "MFM"
        return "unknown"


@dataclass(frozen=True, slots=True)
class AddAlicatRequest:
    """Naming and settings for a new Alicat; its role is always detected."""

    device_id: str
    hardware_label: str
    purpose_label: str
    port: str
    unit_address: str
    #: None keeps the range read from the instrument, or its model range.
    maximum_flow: float | None = None
    poll_interval_seconds: float = 1.0
    #: Set only after the operator acknowledges the BPR installation warning.
    downstream_valve_acknowledged: bool = False


@dataclass(frozen=True, slots=True)
class AddKeithleyRequest:
    device_id: str
    hardware_label: str
    purpose_label: str
    host: str
    port: int = 2268
    timeout_seconds: float = 5.0
    maximum_voltage: float = 30.0
    maximum_current: float = 108.0
    maximum_power: float = 1080.0
    connection_method: str = "ethernet"
    resource_name: str = ""
    visa_baud_rate: int = 9600
    visa_backend: str = "@py"
    poll_interval_seconds: float = 0.1
    driver: str = "keithley_2260b"


@dataclass(frozen=True, slots=True)
class AddGuardianRequest:
    device_id: str
    hardware_label: str
    purpose_label: str
    port: str
    timeout_seconds: float = 2.0
    maximum_temperature: float | None = None
    maximum_speed: float | None = None
    poll_interval_seconds: float = 2.0


@dataclass(frozen=True, slots=True)
class AddTemperatureProbeRequest:
    device_id: str
    hardware_label: str
    purpose_label: str
    port: str
    channels: str = "tc1,tc2,tc3,tc4"
    timeout_seconds: float = 2.0
    poll_interval_seconds: float = 1.0


@dataclass(frozen=True, slots=True)
class AddEzoHumRequest:
    device_id: str
    hardware_label: str
    purpose_label: str
    port: str
    timeout_seconds: float = 2.0
    poll_interval_seconds: float = 1.0
    include_dew_point: bool = True


@dataclass(frozen=True, slots=True)
class AddKamoerM1StpRequest:
    device_id: str
    hardware_label: str
    purpose_label: str
    port: str
    slave: int = 1
    timeout_seconds: float = 2.0
    maximum_speed_rpm: float = 350.0


@dataclass(frozen=True, slots=True)
class AddEsp32Request:
    port: str
    baud_rate: int = 115200
    timeout_seconds: float = 2.0
    sensor_poll_interval_seconds: float = 1.5
    heartbeat_interval_seconds: float = 2.0


type SerialPortProvider = Callable[[], tuple[SerialPortInfo, ...]]
type AlicatChecker = Callable[
    [AlicatMfcConfiguration],
    AlicatDiagnosticResult,
]
type AlicatScanner = Callable[[str, int], tuple[DiscoveredAlicat, ...]]
type AlicatProbe = Callable[[str, str, int], DiscoveredAlicat]
type KeithleyChecker = Callable[
    [Keithley2260BConfiguration | Keithley2280SConfiguration | AmetekAsterionConfiguration],
    KeithleyIdentity | AmetekAsterionIdentity,
]
type GuardianChecker = Callable[
    [GuardianConfiguration],
    GuardianDiagnosticResult,
]
type EzoHumChecker = Callable[[EzoHumConfiguration], object]
type Esp32Checker = Callable[[RigProfile, str], Esp32ReadinessResult]
type Esp32Scanner = Callable[[str, int, float], Esp32DiscoveryResult]
type KamoerM1StpChecker = Callable[[KamoerM1StpConfiguration], KamoerM1StpDiagnosticResult]
type ProfileWriter = Callable[[RigProfile, str | Path], Path | None]
