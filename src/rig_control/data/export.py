from collections import defaultdict
import csv
from datetime import datetime, timezone
import json
from math import floor
from pathlib import Path
from typing import Any


LAST_VALUE_CHANNELS = {
    "active_setpoint",
    "target_setpoint",
    "alarm_state",
    "error_status",
}


def export_experiment_files(
    experiment_directory: str | Path,
    *,
    bin_seconds: float = 1.0,
) -> tuple[Path, Path]:
    """Create convenient wide CSV and Excel views from authoritative journals."""
    directory = Path(experiment_directory)
    metadata = json.loads((directory / "metadata.json").read_text(encoding="utf-8"))
    measurements = _read_jsonl(directory / "measurements.journal.jsonl")
    events = _read_jsonl(directory / "events.journal.jsonl")
    headers, rows = build_wide_rows(measurements, bin_seconds=bin_seconds,
                                    channel_labels=_channel_labels(metadata))

    csv_path = directory / "measurements-wide.csv"
    _write_csv(csv_path, headers, rows)
    xlsx_path = directory / "experiment.xlsx"
    _write_xlsx(
        xlsx_path,
        metadata=metadata,
        wide_headers=headers,
        wide_rows=rows,
        measurements=measurements,
        events=events,
        bin_seconds=bin_seconds,
    )
    return csv_path, xlsx_path


def export_wide_csv(
    experiment_directory: str | Path,
    *,
    bin_seconds: float = 1.0,
) -> Path:
    """Refresh the compact wide CSV view without touching the Excel workbook."""
    directory = Path(experiment_directory)
    measurements = _read_jsonl(directory / "measurements.journal.jsonl")
    metadata_path = directory / "metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8")) if metadata_path.exists() else {}
    headers, rows = build_wide_rows(measurements, bin_seconds=bin_seconds,
                                    channel_labels=_channel_labels(metadata))
    csv_path = directory / "measurements-wide.csv"
    _write_csv(csv_path, headers, rows)
    return csv_path


def build_wide_rows(
    measurements: list[dict[str, Any]],
    *,
    bin_seconds: float = 1.0,
    channel_labels: dict[str, dict[str, str]] | None = None,
) -> tuple[list[str], list[list[object]]]:
    """Bin long-form observations onto a regular grid without carrying values."""
    if bin_seconds <= 0:
        raise ValueError("Export bin size must be positive")
    signals: dict[tuple[str, str], str] = {}
    buckets: dict[int, dict[tuple[str, str], list[dict[str, Any]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for record in measurements:
        key = (str(record["device_id"]), str(record["channel"]))
        unit = str(record["unit"])
        previous_unit = signals.setdefault(key, unit)
        if previous_unit != unit:
            raise ValueError(f"Signal {key} changed unit from {previous_unit} to {unit}")
        timestamp = datetime.fromisoformat(str(record["timestamp"]))
        if timestamp.tzinfo is None:
            raise ValueError("Measurement timestamp has no timezone")
        bucket = floor(timestamp.timestamp() / bin_seconds + 0.5)
        buckets[bucket][key].append(record)

    ordered_signals = sorted(signals)
    headers = ["timestamp_utc"] + [
        f"{device_id}.{channel}"
        + (f" ({channel_labels[device_id][channel]})"
           if channel_labels and channel in channel_labels.get(device_id, {}) else "")
        + f" [{signals[(device_id, channel)]}]"
        for device_id, channel in ordered_signals
    ]
    rows: list[list[object]] = []
    for bucket in sorted(buckets):
        timestamp = datetime.fromtimestamp(
            bucket * bin_seconds, tz=timezone.utc
        ).isoformat()
        row: list[object] = [timestamp]
        for signal in ordered_signals:
            records = buckets[bucket].get(signal, [])
            if not records:
                row.append("")
                continue
            if signal[1] in LAST_VALUE_CHANNELS:
                selected = max(records, key=lambda item: str(item["timestamp"]))
                row.append(float(selected["value"]))
            else:
                values = [float(record["value"]) for record in records]
                row.append(sum(values) / len(values))
        rows.append(row)
    return headers, rows


def _channel_labels(metadata: dict[str, Any]) -> dict[str, dict[str, str]]:
    """Labels are snapshotted at recording start; raw channel IDs never change."""
    labels = json.loads(metadata.get("extra", {}).get("channel_labels_json", "{}"))
    if not isinstance(labels, dict) or any(
        not isinstance(channels, dict)
        or any(not isinstance(label, str) for label in channels.values())
        for channels in labels.values()
    ):
        raise ValueError("Invalid recorded channel labels")
    return labels


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"{path.name} line {line_number} is not an object")
            records.append(value)
    return records


def _write_csv(path: Path, headers: list[str], rows: list[list[object]]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(headers)
        writer.writerows(rows)
    temporary.replace(path)


def _write_xlsx(
    path: Path,
    *,
    metadata: dict[str, Any],
    wide_headers: list[str],
    wide_rows: list[list[object]],
    measurements: list[dict[str, Any]],
    events: list[dict[str, Any]],
    bin_seconds: float,
) -> None:
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill
        from openpyxl.utils import get_column_letter
    except ImportError as error:
        raise RuntimeError(
            "Excel export requires openpyxl; reinstall the project dependencies"
        ) from error

    workbook = Workbook(write_only=False)
    overview = workbook.active
    overview.title = "Overview"
    overview.append(["Experiment export"])
    overview.append([])
    overview.append(["Field", "Value"])
    for key, value in metadata.items():
        if key == "extra":
            for extra_key, extra_value in dict(value).items():
                overview.append([f"extra.{extra_key}", extra_value])
        else:
            overview.append([key, value])
    overview.append([])
    overview.append(["Wide-data bin size (seconds)", bin_seconds])
    overview.append([
        "Binning method",
        "Nearest regular time bin; multiple observations are averaged; missing signals remain blank.",
    ])
    overview.append([
        "Source of truth",
        "measurements.journal.jsonl and events.journal.jsonl",
    ])

    data = workbook.create_sheet("Data")
    data.append(wide_headers)
    for row in wide_rows:
        data.append([_excel_datetime(row[0]), *row[1:]])

    raw = workbook.create_sheet("Raw measurements")
    raw_headers = [
        "timestamp", "device_id", "channel", "value", "unit", "quality",
        "sequence_step_id",
    ]
    raw.append(raw_headers)
    for record in measurements:
        raw.append([
            _excel_datetime(record.get("timestamp")),
            *(record.get(key) for key in raw_headers[1:]),
        ])

    event_sheet = workbook.create_sheet("Events")
    event_headers = ["timestamp", "severity", "source", "message"]
    event_sheet.append(event_headers)
    for event in events:
        event_sheet.append([
            _excel_datetime(event.get("timestamp")),
            *(event.get(key) for key in event_headers[1:]),
        ])

    header_fill = PatternFill("solid", fgColor="1F4E78")
    header_font = Font(color="FFFFFF", bold=True)
    for sheet, header_row in ((overview, 3), (data, 1), (raw, 1), (event_sheet, 1)):
        sheet.freeze_panes = f"A{header_row + 1}"
        sheet.sheet_view.showGridLines = False
        for cell in sheet[header_row]:
            cell.fill = header_fill
            cell.font = header_font
        sheet.auto_filter.ref = sheet.dimensions if sheet is not overview else None

    overview["A1"].font = Font(size=14, bold=True)
    overview.column_dimensions["A"].width = 32
    overview.column_dimensions["B"].width = 85
    for sheet in (data, raw, event_sheet):
        for cell in sheet["A"][1:]:
            cell.number_format = "yyyy-mm-dd hh:mm:ss.000"
        for column_index in range(1, sheet.max_column + 1):
            letter = get_column_letter(column_index)
            sample = [
                str(sheet.cell(row, column_index).value or "")
                for row in range(1, min(sheet.max_row, 200) + 1)
            ]
            sheet.column_dimensions[letter].width = min(
                max(map(len, sample), default=10) + 2,
                36,
            )

    temporary = path.with_name(f"{path.stem}.tmp{path.suffix}")
    workbook.save(temporary)
    temporary.replace(path)


def _excel_datetime(value: object) -> datetime | object:
    if not isinstance(value, str):
        return value
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed
