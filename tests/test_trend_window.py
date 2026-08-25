from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from rig_control.ui.operation.trend_window import (
    TrendCanvasRenderer,
    padded_value_range,
    subsample_readings,
)
from rig_control.ui.operation.dashboard_window import (
    DashboardSignal,
    MultiTraceCanvasRenderer,
    quadrant_layout,
    time_axis_ticks,
)


class FakeCanvas:
    def __init__(self) -> None:
        self.created = 0

    def _create(self, *_args, **_kwargs) -> int:
        self.created += 1
        return self.created

    create_line = _create
    create_text = _create
    create_oval = _create

    def coords(self, *_args) -> None:
        pass

    def itemconfigure(self, *_args, **_kwargs) -> None:
        pass


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


def test_chart_range_adds_space_around_nearly_flat_temperature() -> None:
    minimum, maximum = padded_value_range(21.9, 22.1, "degC")

    assert minimum < 21.9
    assert maximum > 22.1


def test_chart_range_keeps_percentage_axis_physical() -> None:
    assert padded_value_range(0.0, 2.0, "%") == (0.0, 2.2)
    assert padded_value_range(99.0, 100.0, "%RH") == (98.0, 100.0)


def test_chart_renderer_reuses_items_between_refreshes() -> None:
    canvas = FakeCanvas()
    renderer = TrendCanvasRenderer(canvas)

    def draw(point_count: int) -> None:
        renderer.draw(
            bounds=(10, 10, 100, 100),
            points=[(float(index), float(index)) for index in range(point_count)],
            colours=["blue"] * point_count,
            maximum_text="10",
            minimum_text="0",
            first_time_text="12:00:00",
            last_time_text="12:01:00",
        )

    assert canvas.created == 7
    draw(3)
    assert canvas.created == 10
    draw(3)
    draw(2)
    renderer.show_empty(200, 100)
    assert canvas.created == 10
    draw(5)
    assert canvas.created == 12


def test_dashboard_quadrants_fill_available_layout() -> None:
    assert quadrant_layout(1) == ((0, 0, 2),)
    assert quadrant_layout(2) == ((0, 0, 1), (0, 1, 1))
    assert quadrant_layout(3)[0] == (0, 0, 2)
    assert len(quadrant_layout(4)) == 4


def test_dashboard_time_ticks_use_clean_clock_boundaries() -> None:
    start = datetime(2026, 8, 24, 12, 0, 3).timestamp()
    end = datetime(2026, 8, 24, 13, 0, 3).timestamp()

    ticks = time_axis_ticks(start, end)

    assert [label for _timestamp, label in ticks] == [
        "12:10",
        "12:20",
        "12:30",
        "12:40",
        "12:50",
        "13:00",
    ]


def test_multi_trace_renderer_reuses_one_line_per_signal() -> None:
    canvas = FakeCanvas()
    renderer = MultiTraceCanvasRenderer(canvas)
    start = datetime(2026, 8, 24, tzinfo=UTC)
    signal = DashboardSignal("sensor", "temperature", "Temperature", "degC", "Temperatures")
    readings = tuple(
        SimpleNamespace(value=float(index), timestamp=start + timedelta(seconds=index))
        for index in range(3)
    )

    renderer.draw(
        width=600,
        height=300,
        traces=((signal, readings, "blue"),),
        view_start=None,
        view_end=None,
    )
    created = canvas.created
    renderer.draw(
        width=600,
        height=300,
        traces=((signal, readings, "blue"),),
        view_start=None,
        view_end=None,
    )

    assert canvas.created == created
