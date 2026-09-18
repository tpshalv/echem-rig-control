# TA612C protocol temperature probe

This driver speaks the TA612C protocol. Its physical brand is not an application
decision: a DANOPLUS DP-373 can use the same driver **if physical testing confirms
compatibility**. No DP-373 protocol support is claimed yet. Numeric identity is
retained for diagnostics, not restricted to model 612. A connection validates
both identity and four-channel frame structure before becoming ready.

The generic `TemperatureProbe` extends the existing `Sensor` / `MeasurementSource`
interfaces. One instrument owns one USB serial connection and returns stable
channels `tc1`, `tc2`, `tc3`, `tc4` in degC with one host timestamp per batch.
`read_measurement()` returns the first configured channel for single-sensor
callers. Only the factory, setup and this package know the selected protocol.

## Setup and channel labels

Use `rig-profile.ta612c.example.toml` as a standalone trial profile, or copy its
connection/device entries into an existing rig. Set the COM port and choose the
connected channels in `settings.channels`, e.g. `"tc1,tc2"`. Load the profile in
Device Setup. Use **Edit device > Signal labels** to change names:

```toml
[devices.channel_labels]
tc1 = "Water bath"
tc2 = "Cell"
```

These are generic per-device labels. The UI shows `Water bath (tc1)` and
`Cell (tc2)` in the Operation table and graph legend. Polling, journal records
and signal identity continue to use `tc1` and `tc2`. Recording snapshots labels
in metadata (`extra.channel_labels_json`); wide CSV and Excel headers include
the saved label alongside the stable ID. Renaming a probe later does not relabel
old recordings. Labels are optional, and old profiles continue to work.

The existing Edit device form edits connection parameters and selected channels
too. The device can be checked through Device Setup after loading its profile;
there is no separate model-specific add wizard. No additional dependency beyond
the project's `hardware` extra (pyserial) is required.

## Protocol source and implemented subset

[TA Series Communication Protocols, revision 2022-07-25](https://www.mikrocontroller.net/attachment/668956/TA_Series_Communication_Protocols.pdf)
is the circulated protocol document used here (hosted on a third-party forum;
not independently authenticated as an official TASI publication). Sections I,
3.3.1 and V specify the serial interface, channel ordering and worked examples.
TASI's [product specification](https://www.china-tasi.com/zh/product/detail/ta612-series-multi-channel-thermocouple-thermometers/)
confirms four channels and the broad -200..1372 degC measurement envelope.

- 9600 baud, 8N1, binary frames, no text terminators. Flow control is disabled;
  the protocol document does not specify flow-control settings.
- `AA 55 00 03 02`: stop data transfer and request numeric model/version.
- `AA 55 01 03 03`: request one live sample of all four channels.
- Responses begin `55 AA`, followed by command, length excluding the two-byte
  header, payload and sum of preceding bytes modulo 256.
- Channel words are little-endian, scaled by 10; firmware is scaled by 100.
- Identity replies are 9 bytes total (length 7), following the worked example;
  the summary table's length 6 conflicts with its payload size.

Stored-record downloads, clock setting and instrument settings are not sent.
No stream subscription or background serial reader is needed for this subset.
Protocol parsing belongs to `protocol.py`; the generic binary transport only
handles byte I/O, timeouts and port lifecycle. The driver serializes operations.

## Physical DP-373 trial

Close other applications using the port, then run (substitute its actual port):

```powershell
python -m rig_control.diagnostics.tasi_ta612c --port COM7 --channels tc1,tc2
```

The diagnostic prints transmitted/received bytes, identity and decoded values.
It uses the same parser as normal operation and closes the port even on failure.
It sends the documented stop/identity command, which can stop a pre-existing
PC transfer; it does not erase stored data or change instrument settings.

Compare each selected channel with the display, warm probes individually to
verify channel order, and capture an unplugged probe and a below-zero reading.
The document does not specify negative representation or missing-probe codes.
Current decoding provisionally uses signed 16-bit two's complement. `0xFFFF`
is conservatively rejected (it could mean unavailable rather than -0.1 degC);
out-of-envelope values are rejected too. Other in-range error codes cannot be
identified without a capture. Negative encoding, missing-probe encoding and
whether the device keeps reporting Celsius when its display is Fahrenheit
remain unverified. Keep the display in Celsius for the initial trial.

Readiness verifies communication, not whether every probe is connected. Any
invalid selected channel fails that poll without publishing a partial batch or
cached values. The existing polling/UI path reports failure and marks prior
readings stale. Exclude unused channels so they cannot spoil a valid poll.
Checksums, frame sizes and response commands are strict. Split serial reads are
assembled within one deadline; corrupt frames and timeouts fail visibly. A
fresh request clears leftover input, allowing recovery after a failed read.

Tests cover the literal document examples and synthetic error/negative cases;
they are not a substitute for a captured DP-373 compatibility check.
