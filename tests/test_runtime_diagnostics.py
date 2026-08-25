import json
from pathlib import Path

from rig_control.runtime_diagnostics import ProcessMemory, RuntimeDiagnostics


def test_sample_records_process_python_and_application_metrics(
    tmp_path: Path,
) -> None:
    diagnostics = RuntimeDiagnostics(
        tmp_path / "runtime-health.jsonl",
        metric_providers={"queue": lambda: {"size": 7}},
        process_sampler=lambda: ProcessMemory(10, 20, 30, 40, 5),
    )

    record = diagnostics.sample_now(include_allocations=False)

    assert record["process"] == {
        "resident_bytes": 10,
        "private_bytes": 20,
        "virtual_bytes": 30,
        "peak_resident_bytes": 40,
        "handle_count": 5,
        "gdi_object_count": None,
        "user_object_count": None,
        "thread_count": record["process"]["thread_count"],
    }
    assert record["application"] == {"queue": {"size": 7}}
    written = json.loads(
        (tmp_path / "runtime-health.jsonl").read_text(encoding="utf-8")
    )
    assert written["pid"] == record["pid"]


def test_provider_failure_is_captured_instead_of_breaking_sample(
    tmp_path: Path,
) -> None:
    def fail() -> dict[str, object]:
        raise RuntimeError("counter unavailable")

    diagnostics = RuntimeDiagnostics(
        tmp_path / "runtime-health.jsonl",
        metric_providers={"broken": fail},
        process_sampler=lambda: ProcessMemory(None, None, None, None, None),
    )

    record = diagnostics.sample_now()

    assert record["application"] == {
        "broken": {"diagnostic_error": "RuntimeError: counter unavailable"}
    }


def test_memory_warning_is_written_and_published(tmp_path: Path) -> None:
    events = []
    diagnostics = RuntimeDiagnostics(
        tmp_path / "runtime-health.jsonl",
        event_sink=lambda event, details: events.append((event, details)),
        warning_private_bytes=100,
        process_sampler=lambda: ProcessMemory(90, 101, 110, 120, 4),
    )

    record = diagnostics.sample_now()

    assert "private memory is" in record["warning"]
    assert events[0][0].source == "runtime_diagnostics"


def test_start_and_stop_write_lifecycle_records(tmp_path: Path) -> None:
    path = tmp_path / "runtime-health.jsonl"
    diagnostics = RuntimeDiagnostics(
        path,
        sample_interval_seconds=60,
        process_sampler=lambda: ProcessMemory(1, 2, 3, 4, 5),
    )

    diagnostics.start()
    diagnostics.stop()

    records = [json.loads(line) for line in path.read_text().splitlines()]
    assert records[0]["lifecycle"] == "started"
    assert records[-1]["lifecycle"] == "stopped"


def test_metric_provider_can_be_registered_and_removed(tmp_path: Path) -> None:
    diagnostics = RuntimeDiagnostics(
        tmp_path / "runtime-health.jsonl",
        process_sampler=lambda: ProcessMemory(1, 2, 3, 4, 5),
    )
    diagnostics.register_metric_provider("ui", lambda: {"events": 12})
    assert diagnostics.sample_now()["application"]["ui"] == {"events": 12}

    diagnostics.unregister_metric_provider("ui")
    assert "ui" not in diagnostics.sample_now()["application"]


def test_display_history_is_bounded_and_contains_live_counters(
    tmp_path: Path,
) -> None:
    diagnostics = RuntimeDiagnostics(
        tmp_path / "runtime-health.jsonl",
        display_history_limit=2,
        metric_providers={
            "polling": lambda: {
                "results_queue_size": 4,
                "results_queue_capacity": 120,
                "dropped_results_batches": 3,
            },
            "operation_ui": lambda: {"retained_event_count": 9},
        },
        process_sampler=lambda: ProcessMemory(10, 20_000_000, 30, 40, 5, 6, 7),
    )

    diagnostics.sample_now()
    diagnostics.sample_now()
    diagnostics.sample_now()

    history = diagnostics.health_history()
    assert len(history) == 2
    assert history[-1].value == 20.0
    assert history[-1].results_queue_size == 4
    assert history[-1].results_queue_capacity == 120
    assert history[-1].dropped_results_batches == 3
    assert history[-1].retained_event_count == 9
    assert history[-1].gdi_object_count == 6
    assert history[-1].user_object_count == 7
    assert diagnostics.latest_health_point() is history[-1]


def test_whole_run_overview_is_bounded_and_retains_memory_extremes(
    tmp_path: Path,
) -> None:
    private_values = iter([10, 20, 30, 900, 40, 50, 5, 60] * 4)
    diagnostics = RuntimeDiagnostics(
        tmp_path / "runtime-health.jsonl",
        display_history_limit=3,
        overview_history_limit=8,
        process_sampler=lambda: ProcessMemory(
            None, next(private_values), None, None, None
        ),
    )

    for _ in range(32):
        diagnostics.sample_now()

    overview = diagnostics.overview_health_history()
    assert len(overview) <= 8
    assert overview[0].private_bytes == 10
    assert overview[-1].private_bytes == 60
    assert min(point.private_bytes for point in overview) == 5
    assert max(point.private_bytes for point in overview) == 900


def test_growth_warning_can_trigger_bounded_detailed_snapshot(
    tmp_path: Path,
    monkeypatch,
) -> None:
    private_values = iter((10, 20))
    diagnostics = RuntimeDiagnostics(
        tmp_path / "runtime-health.jsonl",
        warning_private_bytes=1_000,
        warning_growth_bytes=1,
        process_sampler=lambda: ProcessMemory(
            None, next(private_values), None, None, None
        ),
    )
    monkeypatch.setattr("rig_control.runtime_diagnostics.tracemalloc.is_tracing", lambda: True)
    monkeypatch.setattr(
        diagnostics,
        "_safe_allocation_summary",
        lambda: {"captured": True},
    )

    diagnostics.sample_now()
    record = diagnostics.sample_now()

    assert record["allocations"] == {"captured": True}


def test_snapshot_is_skipped_when_private_memory_is_already_high(
    tmp_path: Path,
    monkeypatch,
) -> None:
    diagnostics = RuntimeDiagnostics(
        tmp_path / "runtime-health.jsonl",
        warning_private_bytes=100,
        process_sampler=lambda: ProcessMemory(None, 101, None, None, None),
    )
    monkeypatch.setattr("rig_control.runtime_diagnostics.tracemalloc.is_tracing", lambda: True)

    record = diagnostics.sample_now(include_allocations=True)

    assert "allocations" not in record
    assert "above the safety threshold" in record["allocation_snapshot_skipped"]


def test_snapshot_memory_error_disables_further_snapshots(
    tmp_path: Path,
    monkeypatch,
) -> None:
    diagnostics = RuntimeDiagnostics(
        tmp_path / "runtime-health.jsonl",
        process_sampler=lambda: ProcessMemory(None, 10, None, None, None),
    )
    monkeypatch.setattr("rig_control.runtime_diagnostics.tracemalloc.is_tracing", lambda: True)

    def fail() -> dict[str, object]:
        raise MemoryError

    monkeypatch.setattr(diagnostics, "_allocation_summary", fail)
    first = diagnostics.sample_now(include_allocations=True)
    second = diagnostics.sample_now(include_allocations=True)

    assert "MemoryError" in first["allocations"]["diagnostic_error"]
    assert "disabled" in second["allocation_snapshot_skipped"]
