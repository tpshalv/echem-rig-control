# Rig hardware wiring

This document records the electrically tested wiring and communications
configuration. Wire colours are intentionally omitted because the permanent
harness may use different colours.

## Arduino Nano ESP32 and Grove RS485

The Grove RS485 module is connected to the Nano ESP32 hardware UART1:

| Grove RS485 | Nano ESP32 |
| --- | --- |
| RX | D6 (UART1 RX) |
| TX | D7 (UART1 TX) |
| VCC | 3V3 |
| GND | GND |

The MAX13478E-based Grove module handles RS485 transmit/receive direction
automatically. No DE/RE control wire is required.

## Grove RS485 and Lumel RE72

The following polarity has been physically tested and confirmed working:

| Grove RS485 | Lumel RE72 terminal |
| --- | --- |
| A | 7 |
| B | 8 |

Do not reverse these based only on another manufacturer's A/B naming. RS485
A/B labels are not used consistently across vendors, and the mapping above is
the proven mapping for this rig.

Both RE72 controllers share the same daisy-chained A/B bus. They must have
unique slave addresses.

## RE72 communications configuration

| Setting | Value |
| --- | --- |
| Protocol | Modbus RTU |
| Baud rate | 9600 |
| Data bits | 8 |
| Parity | None |
| Stop bits | 2 |
| Framing | 8N2 |
| First controller address | 1 |
| Planned second controller address | 2 |

The RE72 front-panel values corresponding to the confirmed configuration are
`BAUD = 1` and `PROT = 1`. Using 8N1 instead of 8N2 causes requests to time out
without a response.

## Temporary DHT11

The DHT11 is temporary and is not expected to be part of the permanent rig:

| DHT11 signal | Nano ESP32 |
| --- | --- |
| DATA | D3 |
| VCC | 3V3 |
| GND | GND |

Three-pin DHT11 modules can use different physical pin orders. Follow the
signal labels on the particular module rather than relying on package order.

## Permanent-installation notes

- Keep mains and heater wiring physically separated from USB, sensor, and
  RS485 wiring.
- Use a twisted pair for RS485 A/B.
- Daisy-chain multiple RE72 controllers rather than using long star branches.
- Label conductors by signal and terminal at both ends; do not rely solely on
  colour.
- Record any later changes to slave addresses, framing, or terminal mapping in
  this file and in the rig profile.
