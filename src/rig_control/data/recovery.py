import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class JournalReadError(RuntimeError):
    """A recovery journal contains invalid stored data."""


@dataclass(frozen=True, slots=True)
class JournalReadResult:
    """Recovered records plus any limited recovery information."""

    records: tuple[dict[str, Any], ...]
    incomplete_final_line_ignored: bool


def read_json_lines(
    path: str | Path,
) -> JournalReadResult:
    """Read a JSON-lines journal and tolerate a partial final line."""

    journal_path = Path(path)

    try:
        text = journal_path.read_text(encoding="utf-8")
    except FileNotFoundError as error:
        raise FileNotFoundError(
            f"Recovery journal was not found: {journal_path}"
        ) from error
    except UnicodeDecodeError as error:
        raise JournalReadError(
            f"Recovery journal is not valid UTF-8 text: "
            f"{journal_path}"
        ) from error

    lines = text.splitlines(keepends=True)
    records: list[dict[str, Any]] = []
    incomplete_final_line_ignored = False

    for index, line in enumerate(lines):
        line_number = index + 1
        is_final_line = index == len(lines) - 1
        has_line_ending = line.endswith(("\n", "\r"))

        stripped_line = line.strip()

        if not stripped_line:
            raise JournalReadError(
                f"Recovery journal {journal_path} contains "
                f"an empty line at line {line_number}"
            )

        try:
            value = json.loads(stripped_line)
        except json.JSONDecodeError as error:
            if is_final_line and not has_line_ending:
                incomplete_final_line_ignored = True
                break

            raise JournalReadError(
                f"Recovery journal {journal_path} contains "
                f"invalid JSON at line {line_number}: {error.msg}"
            ) from error

        if not isinstance(value, dict):
            raise JournalReadError(
                f"Recovery journal {journal_path} line "
                f"{line_number} must contain a JSON object"
            )

        records.append(value)

    return JournalReadResult(
        records=tuple(records),
        incomplete_final_line_ignored=(
            incomplete_final_line_ignored
        ),
    )