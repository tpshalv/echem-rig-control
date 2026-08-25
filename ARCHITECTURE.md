# Echem Rig Control Architecture

This document explains the main structure of the rig-control software and where future functionality should be added.

For *why* a given piece of it is shaped the way it is — including
alternatives that were considered and rejected — see `decisions/`, a
retrospective log kept separate from this document so reasoning that gets
superseded stays readable rather than being overwritten.

## Main objective

The program controls and monitors an electrochemistry rig while keeping the user interface, hardware drivers, safety logic, experiment data, and recipes separate.

This allows the rig hardware and user interface to change without requiring the entire program to be rewritten.

## Overall flow

The normal control path is:

    User interface
        -> Control commands
        -> Rig control service
        -> Generic device interfaces
        -> Instrument-specific drivers
        -> Communication transports
        -> Physical hardware

Measurements travel in the opposite direction:

    Physical hardware
        -> Instrument driver
        -> Measurement model
        -> Control and data systems
        -> Live user interface and experiment files

## Rig profile

`rig-profile.toml` describes the hardware arrangement expected for a particular rig.

It contains:

- Stable device IDs used by software and recipes.
- Friendly names shown to operators.
- Device capabilities.
- Real or simulated backends.
- Required, optional, and disabled devices.
- Expected manufacturer, model, and serial number.
- Physical connections.
- Device addresses or ESP32 channels.
- Software safety limits and other settings.

The profile does not contain Python code. Adding another configured MFC should eventually require editing the profile through the Rig Setup screen rather than editing the program.

Several devices can share one physical connection. For example:

    Alicat serial connection
        - Nitrogen MFC at address A
        - Wet CO2 MFC at address B
        - Dry CO2 MFC at address C

Similarly, several sensors can share an ESP32 connection while using different channels.

## Device factory

The device factory will read the active rig profile and construct the required Python device objects.

For each enabled role it will:

1. Select the requested driver.
2. Find or create the required physical connection.
3. Apply the configured limits and settings.
4. Create either a real or simulated device.
5. Register the device with the `DeviceManager`.

The current `bootstrap.py` is an earlier hard-coded version of this process. It creates a fixed simulated rig. It will be replaced by a profile-driven `device_factory.py`.

## Device manager

The `DeviceManager` is the program's registry of active devices.

It:

- Stores devices by stable ID.
- Prevents duplicate IDs.
- Finds devices for control and diagnostics.
- Connects and disconnects devices.
- Reports device status.
- Adds useful context when a device operation fails.

It does not need to know the details of Keithley, Alicat, or ESP32 protocols.

## Generic device interfaces

Generic interfaces describe what equipment can do.

Examples include:

- `Device`
- `Sensor`
- `MassFlowController`
- `PowerSupply`
- `SafeStateCapable`

The rest of the program uses these capabilities rather than depending directly on a particular hardware model.

For example, manual MFC controls should work with any future driver that correctly implements `MassFlowController`.

## Instrument drivers

Instrument-specific drivers translate generic operations into commands understood by physical hardware.

Examples include:

- Keithley 2260B driver.
- Future Alicat MFC driver.
- Future potentiostat driver.
- ESP32-connected sensor implementations.

A driver is responsible for:

- Sending correct hardware commands.
- Parsing responses.
- Validating reported values.
- Reporting communication failures clearly.
- Reading the actual instrument state after connecting.
- Requesting a safe state where supported.

A driver should not contain GUI layout or recipe logic.

## ESP32 subsystem

The ESP32 is a remote controller rather than one individual sensor.

It can provide access to several connected sensors and outputs. Its subsystem contains:

- Structured message protocol.
- PC-side client.
- Connection session.
- Watchdog behavior.
- Safe-state handling.
- A simulated ESP32 for tests.

Individual ESP32 channels can still appear as separate logical devices in the rig profile and user interface.

## Transports

Transports move messages between the computer and hardware.

Examples include:

- Serial text.
- TCP socket SCPI.
- Duplex text communication.
- Simulated transports.
- Loopback transports used in tests.

Transports do not decide what a voltage, flow rate, or temperature means. They only move commands and responses.

Keeping transports separate allows a driver to use a different connection method without changing the UI or control service.

## Control layer

Control commands describe requested actions such as:

- Set MFC flow.
- Set power-supply voltage.
- Set power-supply current limit.
- Enable or disable power output.
- Enter a safe state.

The `RigControlService` validates whether control is currently permitted and then executes commands on the correct device.

This keeps safety and mode checks out of button handlers.

Future recipes should use the same control service as manual control. This prevents recipes and the UI from implementing different safety rules.

## Safety behavior

Safety is layered.

The PC software provides:

- Configured operating limits.
- Control-mode restrictions.
- Global safe-state requests.
- Device and communication error reporting.
- Recipe pause or stop behavior.

The ESP32 provides:

- Heartbeat monitoring.
- Local watchdog behavior.
- Locally controlled output safe states.

Physical hardware should provide the final independent protection wherever possible. PC software must not be treated as the only protection against hazardous electrical, pressure, flow, or temperature conditions.

## User interface

The UI displays device state and sends requests through the control layer.

It should not communicate directly with serial ports, Ethernet sockets, or instrument protocols.

The Home window is the single application entry point. It selects a rig profile
and a named application-settings file, then opens one feature screen at a time:

- Device Setup.
- Diagnostics.
- Operation, containing monitoring, recording, trends, and manual controls.

One `ApplicationSession` owns the device manager, control service, polling
service, experiment recorder, and technical logger. Diagnostics and Operation
therefore see the same device objects and connection state. Device Setup is the
exception: it temporarily closes that session while editing the profile, then
Home rebuilds the session from the saved profile.

Application-wide preferences are defined by a central settings registry and
stored in swappable TOML files. Rig profiles continue to describe the hardware;
application settings describe software behavior such as the screen publishing
interval and technical-log location.

The installed command `echem-rig-control` starts Home. Feature windows do not
provide separate application entry points.

## Diagnostics

Diagnostics perform limited, clearly described checks.

Examples include:

- Connect to an instrument.
- Request its identity.
- Compare reported and expected hardware.
- Display connection status.
- Copy detailed technical error information.

Read-only diagnostics must not enable outputs or change operating setpoints.

The future Rig Setup screen will use these diagnostic capabilities to verify the configured hardware.

Physical device configuration and experiment selection are separate. The
local device library is the single source of truth for connection details,
driver choice, hardware labels, and safety limits. Experiment profiles contain
only device IDs plus experiment-specific purpose, required, and enabled
settings. Resolving the two produces the existing `RigProfile` consumed by the
device factory, control services, diagnostics, and user interfaces. This avoids
copying COM ports, network addresses, or safety limits into every experiment.

## Data layer

The data layer is independent of the UI and hardware drivers.

It contains:

- Experiment metadata.
- Measurement records.
- Event records.
- Writer interfaces.
- Directory-based experiment writing.
- Recovery from partially written journal files.
- In-memory writers used by tests.

Measurements can be recorded consistently regardless of which driver produced them.

Planned export formats include:

- Parquet as the complete structured dataset.
- Wide CSV as a convenient human-facing export.
- Metadata and event information alongside measurements.

## Units and conversions

Numbers must not move through the system without a clear physical meaning.
Each measured or controlled quantity will therefore have a documented
canonical unit used by control logic and saved experiment data.

The system distinguishes three representations:

1. The canonical unit used internally and in saved data.
2. The unit used by a particular instrument or protocol.
3. A convenient display or input unit selected for the user interface.

For example, a recipe duration may be entered as `2 h` in the UI. The UI
converts it to the canonical duration value of `7200 s` before sending it to
the control or recipe layer. Saved data uses `7200` with the unit `s`, rather
than depending on the UI choice.

Initial canonical units are:

| Quantity | Canonical unit | Possible display units |
| --- | --- | --- |
| Duration | second (`s`) | s, min, h, day |
| Timestamp | UTC ISO 8601 timestamp | local date and time |
| Voltage | volt (`V`) | V, mV |
| Current | ampere (`A`) | A, mA |
| Power | watt (`W`) | W, kW |
| Temperature | to be selected before temperature control is added | degC, K |
| Gas flow | rig standard, currently `sccm` | supported flow units |

Canonical does not always mean an SI base unit. A well-defined laboratory
unit such as `sccm` can be more useful than an awkward SI representation.
Consistency and unambiguous metadata are more important than converting every
quantity to an SI base unit.

Conversions belong at system boundaries:

- A driver converts instrument values to canonical units before creating
  shared measurement models.
- A driver converts canonical control values to instrument units before
  sending commands when necessary.
- The UI converts user input to canonical units and converts canonical values
  to the selected display unit.
- Data writers save canonical values and explicit unit identifiers. They do
  not depend on UI display preferences.

Unit identifiers must use one standardized spelling. Values labelled `V`,
`volts`, and `mV` must not be mixed without conversion. The current
`Measurement` model stores a value and a free-text unit; future unit support
will validate these identifiers and conversions centrally rather than
spreading conversion formulas across drivers, recipes, and UI code.

Standard-volume gas-flow units require extra care because the meaning of
"standard" depends on reference temperature and pressure. Those reference
conditions must be defined in the rig configuration or measurement metadata
before conversions between standard-volume flow units are supported.

## Simulation

Simulation is a supported backend, not a temporary collection of throwaway tests.

Simulated devices implement the same interfaces as real devices. This allows:

- UI development without hardware.
- Recipe testing without activating equipment.
- Reproducible automated tests.
- Fault and disconnection testing.

A real profile must never silently fall back to simulation. Simulated devices must always be clearly identified in the UI and saved experiment metadata.

## Testing

Automated tests verify small pieces of behavior and integrations.

Tests do not run during normal rig operation and therefore do not slow the application down.

Hardware tests will be marked separately so the normal test suite can run without connected instruments.

The expected routine is:

    python -m pytest

All tests should pass before committing a completed change.

## Dependency direction

Higher-level code can depend on lower-level interfaces, but hardware-specific details should not leak upward.

Preferred direction:

    UI -> control -> device interfaces
    Drivers -> device interfaces and transports
    Data -> shared measurement and event models
    Factory -> profile, drivers, and transports

Avoid:

    UI -> raw serial commands
    Recipe -> Keithley-specific SCPI
    Data writer -> Tkinter widgets
    Driver -> manual-control window

## Adding future hardware

Adding another unit of an already supported device type should normally mean:

1. Add it to the rig profile.
2. Assign its connection, address, or channel.
3. Record its expected identity.
4. Give it a stable ID and friendly name.
5. Verify it in Rig Setup.

Adding a new hardware model should normally mean:

1. Add or reuse a generic capability interface.
2. Implement the instrument protocol.
3. Implement its driver.
4. Add a device-factory registration.
5. Add simulated and real tests.
6. Add it to a rig profile.

The UI, data layer, and unrelated drivers should not require significant rewrites.

## Near-term consolidation plan

Before recipes are implemented:

1. Connect and verify real Keithley, Alicat, and ESP32 hardware.
2. Add a polling broadcast/snapshot boundary before allowing multiple live
   feature screens to consume readings simultaneously.
3. Design and implement recipe execution through the existing control service.
