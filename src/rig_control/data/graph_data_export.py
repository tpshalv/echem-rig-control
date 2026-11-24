"""Save the live graph history held in memory to plain CSV files.

This is the rescue path for readings that were never recorded: everything the
graphs can show, for a chosen period, written with the time it was saved and
nothing else. Values are in the display units, as on the graphs.
"""

from __future__ import annotations

import csv
from array import array
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


@dataclass(frozen=True, slots=True)
class GraphDataChannel:
    """A copy of one channel's retained readings for a period, oldest first.

    The first ``thinned_count`` readings come from the whole-run store, which
    keeps the min and max of groups of readings; the rest are every reading.
    """

    device_id: str
    channel: str
    unit: str
    times: array
    values: array
    quality_codes: bytes
    quality_names: tuple[str, ...]
    thinned_count: int
    convert: Callable[[float], float] | None = None


def write_graph_data(
    channels: Iterable[GraphDataChannel],
    directory: str | Path,
    *,
    period: str,
    saved_at: datetime | None = None,
) -> Path:
    """Write one CSV per channel plus ``all channels.csv``; return the folder."""

    channels = sorted(
        (c for c in channels if len(c.times)),
        key=lambda c: (c.device_id, c.channel),
    )
    if not channels:
        raise ValueError("There is no graph data yet: monitoring has not recorded any readings.")
    saved_at = (saved_at or datetime.now(UTC)).astimezone()
    folder = Path(directory) / f"Graph data {saved_at:%Y-%m-%d %H%M%S} ({period})"
    folder.mkdir(parents=True, exist_ok=True)

    header = ("time_local", "time_utc", "device", "channel", "value", "unit", "quality", "resolution")
    with open(folder / "all channels.csv", "w", encoding="utf-8", newline="") as combined_file:
        combined = csv.writer(combined_file)
        combined.writerow(header)
        for channel in channels:
            rows = list(_rows(channel))
            combined.writerows(rows)
            with open(folder / f"{_file_name(channel)}.csv", "w", encoding="utf-8", newline="") as own:
                writer = csv.writer(own)
                writer.writerow(header)
                writer.writerows(rows)
    return folder


def _rows(channel: GraphDataChannel):
    convert = channel.convert
    for index, (timestamp, value) in enumerate(zip(channel.times, channel.values)):
        when = datetime.fromtimestamp(timestamp, UTC)
        yield (
            when.astimezone().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
            when.isoformat(),
            channel.device_id,
            channel.channel,
            repr(convert(value) if convert is not None else value),
            channel.unit,
            channel.quality_names[channel.quality_codes[index]],
            "thinned" if index < channel.thinned_count else "full",
        )


def _file_name(channel: GraphDataChannel) -> str:
    text = f"{channel.device_id} {channel.channel}"
    return "".join(c if c.isalnum() or c in "-_ ." else "_" for c in text).strip(" .") or "channel"
