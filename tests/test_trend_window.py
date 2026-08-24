from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from rig_control.ui.operation.trend_window import subsample_readings


def test_chart_subsampling_caps_points_and_preserves_endpoints() -> None:
    start = datetime(2026, 8, 24, tzinfo=UTC)
    readings = tuple(
        SimpleNamespace(value=float(index), unit="V", quality="good",
                        timestamp=start + timedelta(seconds=index))
        for index in range(10_000)
    )

    displayed = subsample_readings(readings, 2_000)

    assert len(displayed) == 2_000
    assert displayed[0] is readings[0]
    assert displayed[-1] is readings[-1]
