from rig_control.controllers.client import ControllerClient
from rig_control.models import DeviceStatus, Event, EventSeverity
from rig_control.transports.base import Transport
from rig_control.protocol import PROTOCOL_VERSION

class ControllerIdentityError(RuntimeError):
    """The connected controller is not the one expected by this session."""

class ControllerSession:
    """Manage a PC-side connection to one remote controller."""

    def __init__(
        self,
        controller_id: str,
        transport: Transport,
    ) -> None:
        self._controller_id = controller_id
        self._transport = transport
        self._client = ControllerClient(transport)
        self._status = DeviceStatus.DISCONNECTED
        self._events: list[Event] = []

    @property
    def status(self) -> DeviceStatus:
        return self._status

    @property
    def client(self) -> ControllerClient:
        return self._client

    @property
    def events(self) -> tuple[Event, ...]:
        """Return events recorded during this session."""

        return tuple(self._events)

    def connect(self) -> None:
        """Connect and verify that the controller responds."""

        self._status = DeviceStatus.CONNECTING

        try:
            self._transport.connect()
            self._status = DeviceStatus.CONNECTED
            identity = self._client.identify()
            self._verify_identity(identity)
            self._client.heartbeat()
            self._client.get_status()
        except Exception:
            self._transport.disconnect()
            self._status = DeviceStatus.DISCONNECTED
            self._record_event(
                "Controller connection failed",
                EventSeverity.ERROR,
            )
            raise

        self._status = DeviceStatus.READY
        self._record_event(
            "Controller connected and ready",
            EventSeverity.INFO,
        )

    def disconnect(self) -> None:
        """Close the controller connection."""

        self._transport.disconnect()
        self._status = DeviceStatus.DISCONNECTED
        self._record_event(
            "Controller disconnected",
            EventSeverity.INFO,
        )

    def _verify_identity(self, identity: dict[str, object]) -> None:
        controller_id = identity.get("controller_id")
        protocol_version = identity.get("protocol_version")

        if controller_id != self._controller_id:
            raise ControllerIdentityError(
                f"Expected controller {self._controller_id!r}, "
                f"but connected to {controller_id!r}"
            )

        if protocol_version != PROTOCOL_VERSION:
            raise ControllerIdentityError(
                f"Controller protocol version {protocol_version!r} "
                f"is incompatible with PC protocol version {PROTOCOL_VERSION}"
            )

    def _record_event(
        self,
        message: str,
        severity: EventSeverity,
    ) -> None:
        self._events.append(
            Event(
                source=self._controller_id,
                message=message,
                severity=severity,
            )
        )