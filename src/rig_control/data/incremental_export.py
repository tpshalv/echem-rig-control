"""Disk-backed time bins and append-only updates for the usual live CSV path.

The journals remain authoritative. This rebuildable SQLite cache bounds memory,
remembers the journal byte offset, and permits Excel to read a consistent snapshot
while recording continues. A new signal or a late correction to an exported bin
requires a streamed CSV rebuild; ordinary updates only append completed rows.
"""

from contextlib import contextmanager
import csv
from datetime import datetime, timezone
from itertools import groupby
import json
from math import ceil, floor, isfinite
from pathlib import Path
import os
import sqlite3

from rig_control.data.export import LAST_VALUE_CHANNELS, _channel_labels


class IncrementalCsvExporter:
    def __init__(self, directory: str | Path, bin_seconds: float = 1.0) -> None:
        if not isfinite(bin_seconds) or bin_seconds <= 0:
            raise ValueError("Export bin size must be finite and positive")
        self.directory = Path(directory)
        self.bin_seconds = bin_seconds
        self.path = self.directory / "measurements-wide.csv"
        self.database = self.directory / "export-cache.sqlite3"

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.database, timeout=60)
        try:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA cache_size=-4096")
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT);
                CREATE TABLE IF NOT EXISTS signals (
                    device TEXT, channel TEXT, unit TEXT,
                    PRIMARY KEY (device, channel));
                CREATE TABLE IF NOT EXISTS bins (
                    bucket INTEGER, device TEXT, channel TEXT,
                    total REAL, count INTEGER, latest_time REAL, latest_value REAL,
                    PRIMARY KEY (bucket, device, channel));
            """)
            yield connection
        finally:
            connection.close()

    @staticmethod
    def _state(connection) -> dict:
        return {key: json.loads(value) for key, value in connection.execute(
            "SELECT key, value FROM state"
        )}

    @staticmethod
    def _save(connection, **values) -> None:
        connection.executemany(
            "INSERT OR REPLACE INTO state VALUES (?, ?)",
            ((key, json.dumps(value)) for key, value in values.items()),
        )

    def update(self, *, final: bool = False) -> Path:
        """Consume complete new journal lines, then append settled CSV bins."""
        with self._connect() as connection:
            # Serialize writers, including independently requested exports.
            connection.execute("BEGIN IMMEDIATE")
            state = self._state(connection)
            journal = self.directory / "measurements.journal.jsonl"
            end = journal.stat().st_size
            if (state.get("bin_seconds") != self.bin_seconds
                    or state.get("offset", 0) > end):
                connection.execute("DELETE FROM bins")
                connection.execute("DELETE FROM signals")
                connection.execute("DELETE FROM state")
                state = {}
            offset = state.get("offset", 0)
            dirty = state.get("dirty")
            schema_changed = state.get("schema_changed", False)
            with journal.open("rb") as stream:
                stream.seek(offset)
                while stream.tell() < end:
                    line = stream.readline(end - stream.tell())
                    if not line.endswith(b"\n"):
                        break  # A concurrent writer may be partway through a line.
                    offset = stream.tell()
                    if not line.strip():
                        continue
                    record = json.loads(line)
                    device, channel = str(record["device_id"]), str(record["channel"])
                    unit = str(record["unit"])
                    existing = connection.execute(
                        "SELECT unit FROM signals WHERE device=? AND channel=?",
                        (device, channel),
                    ).fetchone()
                    if existing is None:
                        connection.execute("INSERT INTO signals VALUES (?, ?, ?)",
                                           (device, channel, unit))
                        schema_changed = True
                    elif existing[0] != unit:
                        raise ValueError(f"Signal {(device, channel)} changed unit")
                    timestamp = datetime.fromisoformat(str(record["timestamp"]))
                    if timestamp.tzinfo is None:
                        raise ValueError("Measurement timestamp has no timezone")
                    seconds = timestamp.timestamp()
                    bucket = floor(seconds / self.bin_seconds + 0.5)
                    value = float(record["value"])
                    connection.execute("""
                        INSERT INTO bins VALUES (?, ?, ?, ?, 1, ?, ?)
                        ON CONFLICT(bucket, device, channel) DO UPDATE SET
                            total=total+excluded.total, count=count+1,
                            latest_value=CASE WHEN excluded.latest_time >= latest_time
                                THEN excluded.latest_value ELSE latest_value END,
                            latest_time=MAX(latest_time, excluded.latest_time)
                        """, (bucket, device, channel, value, seconds, value))
                    dirty = bucket if dirty is None else min(dirty, bucket)
            self._save(connection, offset=offset, bin_seconds=self.bin_seconds,
                       dirty=dirty, schema_changed=schema_changed)
            # Persist ingestion first. If writing CSV fails, a retry still knows
            # which bins need exporting and does not count their readings twice.
            connection.commit()
            connection.execute("BEGIN IMMEDIATE")
            state = self._state(connection)
            maximum = connection.execute("SELECT MAX(bucket) FROM bins").fetchone()[0]
            through = (maximum if final or maximum is None
                       else maximum - max(1, ceil(2 / self.bin_seconds)))
            previous = state.get("through")
            if previous is not None and through is not None:
                through = max(previous, through)
            dirty = state.get("dirty")
            rebuild = (
                state.get("schema_changed", False)
                or not self.path.exists()
                or self.path.stat().st_size != state.get("csv_size")
                or (previous is not None and dirty is not None and dirty <= previous)
            )
            signals = self.signals(connection)
            metadata_path = self.directory / "metadata.json"
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            headers = self.headers(signals, metadata)
            if rebuild:
                temporary = self.path.with_suffix(".csv.tmp")
                with temporary.open("w", encoding="utf-8-sig", newline="") as stream:
                    writer = csv.writer(stream)
                    writer.writerow(headers)
                    writer.writerows(self.rows(connection, signals, through=through))
                    stream.flush()
                    os.fsync(stream.fileno())
                temporary.replace(self.path)
            elif through is not None and (previous is None or through > previous):
                with self.path.open("a", encoding="utf-8", newline="") as stream:
                    csv.writer(stream).writerows(
                        self.rows(connection, signals, after=previous, through=through)
                    )
                    stream.flush()
                    os.fsync(stream.fileno())
            self._save(connection, through=through, dirty=None, schema_changed=False,
                       csv_size=self.path.stat().st_size)
            connection.commit()
        return self.path

    @staticmethod
    def signals(connection) -> tuple:
        return tuple(connection.execute(
            "SELECT device, channel, unit FROM signals ORDER BY device, channel"
        ))

    @staticmethod
    def headers(signals, metadata) -> list[str]:
        labels = _channel_labels(metadata)
        return ["timestamp_utc"] + [
            f"{device}.{channel}"
            + (f" ({labels[device][channel]})" if channel in labels.get(device, {}) else "")
            + f" [{unit}]" for device, channel, unit in signals
        ]

    def rows(self, connection, signals, *, after=None, through=None):
        if through is None:
            return
        query = "SELECT bucket, device, channel, total, count, latest_value FROM bins WHERE bucket<=?"
        parameters = [through]
        if after is not None:
            query += " AND bucket>?"
            parameters.append(after)
        query += " ORDER BY bucket, device, channel"
        for bucket, records in groupby(connection.execute(query, parameters), key=lambda row: row[0]):
            values = {
                (device, channel): latest if channel in LAST_VALUE_CHANNELS else total / count
                for _, device, channel, total, count, latest in records
            }
            yield [datetime.fromtimestamp(bucket * self.bin_seconds, timezone.utc).isoformat(),
                   *(values.get((device, channel), "") for device, channel, _ in signals)]

    @contextmanager
    def snapshot(self):
        """A stable SQLite read transaction; live ingestion can continue in WAL."""
        with self._connect() as connection:
            connection.execute("BEGIN")
            signals = self.signals(connection)
            maximum = connection.execute("SELECT MAX(bucket) FROM bins").fetchone()[0]
            yield signals, self.rows(connection, signals, through=maximum)
