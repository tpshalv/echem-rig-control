import math
from datetime import UTC, datetime, timedelta

from rig_control.ui.operation.model import LiveMeasurementRow, _ChannelHistory


START = datetime(2026, 8, 18, 15, 0, tzinfo=UTC)


def reading(second: float, value: float | None = None) -> LiveMeasurementRow:
    return LiveMeasurementRow(
        device_id="probe",
        channel="humidity",
        value=math.sin(second / 900) if value is None else value,
        unit="%RH",
        quality="good",
        timestamp=START + timedelta(seconds=second),
    )


def filled(seconds: int, *, skip: range = range(0)) -> _ChannelHistory:
    history = _ChannelHistory(500)
    for second in range(seconds):
        if second not in skip:
            history.append(reading(second))
    return history


def test_period_point_count_is_steady_between_refreshes() -> None:
    # The old store flipped between ~500 and ~174 points on "Last hour" once
    # the run outgrew the hour, depending on where bucket boundaries fell.
    history = filled(2 * 3_600)
    counts = set()
    for second in range(2 * 3_600, 2 * 3_600 + 60):
        history.append(reading(second))
        counts.add(len(history.rows_for_period(3_600)))

    assert max(counts) <= 500
    assert max(counts) - min(counts) <= 4


def test_short_period_is_drawn_from_raw_readings_on_a_long_run() -> None:
    # The old store served "Last 10 minutes" from 10-second buckets
    # (~170 points, 9 s apart) because its raw buffer held only 500 readings.
    history = filled(3 * 3_600)

    ten_minutes = history.rows_for_period(600)

    assert 450 <= len(ten_minutes) <= 500
    assert max(
        (later.timestamp - earlier.timestamp).total_seconds()
        for earlier, later in zip(ten_minutes, ten_minutes[1:])
    ) <= 5.0


def test_long_period_on_a_shorter_run_uses_the_full_point_budget() -> None:
    history = filled(15 * 60)

    assert len(history.rows_for_period(28_800)) >= 450


def test_short_run_is_returned_unthinned() -> None:
    history = filled(400)

    assert [row.timestamp for row in history.rows_for_period(28_800)] == [
        START + timedelta(seconds=second) for second in range(400)
    ]


def test_whole_run_resolution_is_even_across_the_run() -> None:
    # The old overview thinned the oldest data again on every pass, leaving
    # the first half of a two-hour run with a handful of points.
    history = filled(12 * 3_600)

    whole = history.rows_for_period(None)
    midpoint = START + timedelta(hours=6)
    older = sum(row.timestamp < midpoint for row in whole)

    assert 450 <= len(whole) <= 500
    assert abs(older - (len(whole) - older)) <= 10
    assert whole[0].timestamp == START
    assert whole[-1].timestamp == START + timedelta(seconds=12 * 3_600 - 1)


def test_gap_in_readings_does_not_coarsen_the_period() -> None:
    # A dropout used to push a period onto the 10-minute level (~20 points).
    history = filled(3 * 3_600, skip=range(9_000, 9_600))

    last_hour = history.rows_for_period(3_600)

    assert len(last_hour) >= 400
    assert max(
        (later.timestamp - earlier.timestamp).total_seconds()
        for earlier, later in zip(last_hour, last_hour[1:])
    ) == 601.0


def test_spike_survives_whole_run_thinning() -> None:
    history = _ChannelHistory(500)
    for second in range(30_000):
        history.append(reading(second, 1_000.0 if second == 1_234 else 0.0))

    assert any(row.value == 1_000.0 for row in history.rows_for_period(None))


def test_memory_stays_bounded_beyond_the_raw_window() -> None:
    history = filled(24 * 3_600)

    # Eight hours of raw readings plus the fixed-size whole-run store.
    assert history.retained_count() <= 28_800 + 1_024 + 10_000 + 64
    whole = history.rows_for_period(None)
    assert whole[0].timestamp == START


def test_nearest_reading_uses_the_whole_run_store_for_old_times() -> None:
    history = filled(10 * 3_600)

    old = history.nearest((START + timedelta(minutes=30)).timestamp())
    recent = history.nearest((START + timedelta(hours=9, seconds=0.4)).timestamp())

    assert abs((old.timestamp - (START + timedelta(minutes=30))).total_seconds()) < 10
    assert recent.timestamp == START + timedelta(hours=9)
