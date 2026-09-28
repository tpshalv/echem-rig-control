"""A read-only record of what one instrument answers to its setup queries.

Instruments with configuration that cannot be reconstructed from memory —
control loops, register maps, ranges and units — are worth capturing before
and after anyone changes them. A dump is the evidence for what a device was
set to on a date, and the source a parser is written against.

Nothing here sends a command that changes an instrument.
"""

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class DumpQuery:
    """One query and what the instrument answered, or why it did not."""

    command: str
    purpose: str
    reply: str
    failed: bool = False

    def __post_init__(self) -> None:
        for name in ("command", "purpose"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"Dump query {name} cannot be empty")
        if not isinstance(self.reply, str):
            raise TypeError("Dump query reply must be text")
        if not isinstance(self.failed, bool):
            raise TypeError("Dump query failed flag must be Boolean")

    def to_line(self) -> str:
        marker = "!!" if self.failed else "->"
        return f"{self.command:<10} {marker} {self.reply!r}   ({self.purpose})"


@dataclass(frozen=True, slots=True)
class DeviceDump:
    """Every setup query answered by one device, with enough provenance to
    identify the instrument it came from later."""

    device_id: str
    device_type: str
    queries: tuple[DumpQuery, ...]
    taken_at: datetime
    description: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.queries, tuple) or any(
            not isinstance(item, DumpQuery) for item in self.queries
        ):
            raise TypeError("Dump queries must be DumpQuery values")
        if not self.queries:
            raise ValueError("A dump must contain at least one query")
        if not isinstance(self.taken_at, datetime):
            raise TypeError("Dump timestamp must be a datetime")

    @property
    def failures(self) -> tuple[DumpQuery, ...]:
        return tuple(query for query in self.queries if query.failed)

    def to_text(self) -> str:
        lines = [
            f"Device settings dump: {self.device_id}",
            f"Device type: {self.device_type}",
            f"Taken at {self.taken_at.isoformat(timespec='seconds')}",
            "Every command below is a query. None of them changes a setting.",
        ]
        if self.description:
            lines.append(self.description)
        lines.append("")
        lines.extend(query.to_line() for query in self.queries)
        if self.failures:
            lines.append("")
            lines.append(
                f"{len(self.failures)} of {len(self.queries)} queries did not "
                "answer; those lines are marked !!."
            )
        return "\n".join(lines) + "\n"
