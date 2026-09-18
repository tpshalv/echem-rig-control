# OHAUS Guardian 5000 (second generation G52)

Source: OHAUS **30910709, revision A (2024-08-22)**,
[official instruction manual](https://dmx.ohaus.com/assets/0/14/4294999331/4294999365/4294999375/9339cd47-8a26-47d0-9233-2d28851fdb4c.pdf).
Section 6, EN-19/20 defines the protocol; EN-25/26 defines the model ratings.

| MODEL | Heating ceiling | Stirring range |
| --- | --- | --- |
| e-G52HSRDA | 360 degC | 50-1800 rpm |
| e-G52HS10C | 500 degC | 50-1800 rpm |
| e-G52HS07C | 550 degC | 50-1800 rpm |
| e-G52HP07C | 550 degC | None |
| e-G52ST07C | None | 50-1800 rpm |

Model discovery uses `MODEL`, not the profile's expected model. Case,
surrounding whitespace, K1 kit suffixes and 120V/230V region descriptions are
normalised; unknown models are rejected before any control writes. G51 models
are not supported. Hardware specifications are immutable and independent of
optional rig ceilings (`GuardianLimits`) and separately assigned run ceilings
(`set_run_limits`). Neither ceiling can override the detected hardware rating.

The generic serial transport uses 9600 baud, 8 data bits, no parity, one stop
bit, XON/XOFF, no hardware flow control, ASCII and CRLF. Commands and arguments
are space-separated and limited to 80 bytes including termination. Use a
straight-through RS232 cable/USB-RS232 adapter and the unit's DB9 female port.

Minimal profile (replace COM8 with the local port):

```toml
[profile]
profile_id = "guardian"
friendly_name = "Guardian hotplate"

[[connections]]
connection_id = "guardian_serial"
connection_type = "serial_text"
[connections.parameters]
port = "COM8"
baud_rate = 9600
timeout_seconds = 2.0

[[devices]]
device_id = "hotplate"
friendly_name = "Guardian 5000"
capability = "hotplate_stirrer"
driver = "ohaus_guardian_5000"
backend = "real"
connection_id = "guardian_serial"
[devices.settings]
maximum_temperature = 300.0
maximum_speed = 1500.0
```

One role owns one serial connection, including single-function models. The
driver provides `Device`, `MeasurementSource` and `SafeStateCapable`; existing
polling and recording collect `temperature` (plate), optional
`probe_temperature`, and `stir_speed`. Channels are emitted only for supported
functions. Controls are driver methods; no new device-setup wizard or GUI
controls are introduced. Run ceilings must be set by the calling workflow;
they are not inferred from unrelated PSU run limits.

Implemented commands: `MODEL`, `SERIAL`, `VERSION`, `MODE`,
`TARGET_TEMPERATURE`, `TARGET_SPEED`, `MEASURED_TEMPERATURE`, `MEASURED_SPEED`,
`START_HEAT`, `STOP_HEAT`, `START_STIR`, `STOP_STIR`, `TIMER`, `TIMER_RESET`,
and the one-shot `PARAM 0` error readback. MODE is read-only; plate/probe mode
selection remains with the instrument. No undocumented error query is used.

Connection queries actual identity, targets and mode without starting/stopping
the instrument. Failed synchronisation closes the port and clears cached state.
Start commands recheck targets against all limits. Set commands require an
acknowledgement and matching readback; stop commands require MODE confirmation.
Safe state and disconnect attempt both supported stop commands even when the
first fails. Failure to confirm a stop is reported, never treated as success.
MODE 99 leaves heating/stirring state unknown and prevents starts. Serial loss
cannot guarantee a physical shutdown; the documented protocol has no watchdog.

## Physical verification still required

The official manual gives command names, acknowledgement syntax and parameter
descriptions, but no complete query-reply transcripts. Tests use explicit
fixtures, not captured hardware traffic. Verify these on the e-G52HSRDA:

- Queries return one CRLF line, either a bare value or a command-prefixed value;
  writes return `<command> A` and rejection is `L`. Separate acknowledgement
  lines, units, or other layouts are rejected rather than guessed.
- `MEASURED_TEMPERATURE` returns plate first and optional probe second, separated
  by whitespace or a comma. The manual promises both values in probe mode but
  does not specify their ordering or delimiters. Verify ordering against the
  display before relying on plate/probe labels in recorded data.
- `PARAM 0` is parsed as the documented eight comma-separated fields with an
  optional trailing comma. The last field is assumed to be `0` for no error,
  `E1`-`E5`, `E7`-`E10`, or `AC Err`. Probe-mode dump layout and error encoding
  need confirmation; this optional readback is not required for normal polling.
- Temperature targets use documented 0.5 degC increments. Nonnegative targets
  up to the model ceiling are accepted; the manual's achievable temperature
  range starts at ambient + 5 degC, not a fixed programmable minimum. Ambient
  is unavailable over this protocol. SmartHeat and probe/accessory limits can
  impose a lower ceiling; configure rig/run limits accordingly.
- RPM writes use whole numbers. Timer writes are restricted to whole minutes,
  1-5999, within the documented timer range despite the seconds field in syntax.
  Confirm target readback and MODE timing after commands, especially with RTA
  disabled. No retries are applied to start/stop or timer commands.

SmartHeat, ramp rates, calibration, power recovery and run-dry configuration
remain front-panel settings: section 6 does not document commands for them.
