from rig_control.data.export import build_wide_rows


def measurement(timestamp: str, channel: str, value: float) -> dict[str, object]:
    return {
        "device_id": "re72_1",
        "channel": channel,
        "timestamp": timestamp,
        "value": value,
        "unit": "degC" if "setpoint" in channel or channel == "process_value" else "%",
        "quality": "good",
        "sequence_step_id": None,
    }


def test_wide_export_uses_regular_bins_and_preserves_missing_values() -> None:
    headers, rows = build_wide_rows(
        [
            measurement("2026-09-17T10:00:00.080+00:00", "process_value", 20.0),
            measurement("2026-09-17T10:00:00.300+00:00", "process_value", 22.0),
            measurement("2026-09-17T10:00:01.100+00:00", "target_setpoint", 30.0),
        ]
    )

    assert headers == [
        "timestamp_utc",
        "re72_1.process_value [degC]",
        "re72_1.target_setpoint [degC]",
    ]
    assert rows == [
        ["2026-09-17T10:00:00+00:00", 21.0, ""],
        ["2026-09-17T10:00:01+00:00", "", 30.0],
    ]


def test_wide_export_uses_last_value_for_discrete_setpoint() -> None:
    _headers, rows = build_wide_rows(
        [
            measurement("2026-09-17T10:00:00.100+00:00", "target_setpoint", 30.0),
            measurement("2026-09-17T10:00:00.200+00:00", "target_setpoint", 35.0),
        ]
    )

    assert rows[0][1] == 35.0


def test_wide_export_allows_subsecond_bins() -> None:
    headers, rows = build_wide_rows(
        [
            measurement("2026-09-17T10:00:00.100+00:00", "process_value", 20.0),
            measurement("2026-09-17T10:00:00.300+00:00", "process_value", 22.0),
            measurement("2026-09-17T10:00:00.700+00:00", "process_value", 24.0),
        ],
        bin_seconds=0.5,
    )

    assert headers == ["timestamp_utc", "re72_1.process_value [degC]"]
    assert rows == [
        ["2026-09-17T10:00:00+00:00", 20.0],
        ["2026-09-17T10:00:00.500000+00:00", 23.0],
    ]
