# 0006 — ESP32 as a protocol gateway, not a peer instrument

Status: Accepted
Written by: Claude, from direct discussion with the project maintainer.
Implemented by: ChatGPT (Python side and firmware), across sessions
Claude was partly or not part of — see Gaps.

## Context

The rig needs many physically different local things wired to one ESP32
(sensors on I2C, digital outputs, and — confirmed by the maintainer — a
future set of Modbus PID heater controllers), plus, separately, whatever
instruments talk to the PC directly (Alicat, Keithley). The question was
how this should be organised so the PC-side software doesn't need to grow
a new concept every time a new local sensor or bus type gets added to the
ESP32.

## Decision

The ESP32 is a single connection from the PC's point of view (one JSON-
over-serial link), and everything physically local to it — I2C sensors,
digital outputs, and eventually Modbus-based controllers reached via the
ESP32 acting as a Modbus master — is exposed as more named channels over
that one existing connection, never as a new PC-side connection type.
Only something that talks to the PC *directly* (its own serial/Ethernet
link, bypassing the ESP32) would get its own `ConnectionDefinition`.

A related, narrower decision within this: `safe_state_active` on the
controller protocol is a derived status flag (true whenever current
outputs happen to match the configured safe values), recomputed after
every output change — never a latch, and never a precondition that blocks
a command. `watchdog_tripped` is the only real latch, cleared only by an
explicit `rearm`. This resolved a specific ambiguity the firmware
implementer flagged before writing code, by pointing at the existing
`SimulatedController` reference implementation's exact behaviour rather
than inventing a new rule.

## Reasoning

This keeps the PC-side software's model of "how many things can I talk
to" from growing every time the physical rig gains a new local sensor or
a bus type it wasn't specifically written for — the complexity of
bridging to I2C or Modbus is absorbed once, inside the ESP32 firmware,
rather than needing a new connection concept in the profile schema each
time.

## Alternatives considered

- Modelling each local sensor as its own PC-side connection — rejected,
  since I2C devices aren't independently reachable from the PC at all;
  only the ESP32 itself is.
- Having the PID controllers talk Modbus directly to the PC on their own
  connection — considered, but the maintainer confirmed they'll route
  through the ESP32 instead, so this wasn't pursued. The actual Modbus
  bridge itself was explicitly deferred (see 0012's neighbouring context)
  until the real controllers are in hand, since designing it blind risked
  guessing wrong about what they'd need.

## Gaps

The firmware implementation of the Modbus-master bridge does not exist
yet as of this entry. The exact wire shape of the ESP32's device-
discovery "capabilities" response (used by Device Setup's scan-and-add
flow, see 0012) was built by ChatGPT and only observed afterward by
Claude reading `devices/esp32_controller.py` and
`ui/device_setup/model.py` — the reasoning for its specific shape (e.g.
why only a `"dht11"` kind is currently recognised) was not part of any
conversation Claude had.
