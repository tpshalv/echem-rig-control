from pathlib import Path

import pytest

from rig_control.data.recovery import (
    JournalReadError,
    read_json_lines,
)


def write_journal(
    temporary_directory: Path,
    contents: str,
) -> Path:
    path = temporary_directory / "journal.jsonl"
    path.write_text(contents, encoding="utf-8")
    return path


def test_empty_journal_returns_no_records(
    tmp_path: Path,
) -> None:
    path = write_journal(tmp_path, "")

    result = read_json_lines(path)

    assert result.records == ()
    assert result.incomplete_final_line_ignored is False


def test_complete_records_are_recovered(
    tmp_path: Path,
) -> None:
    path = write_journal(
        tmp_path,
        '{"value":1}\n{"value":2}\n{"value":3}\n',
    )

    result = read_json_lines(path)

    assert result.records == (
        {"value": 1},
        {"value": 2},
        {"value": 3},
    )
    assert result.incomplete_final_line_ignored is False


def test_valid_final_record_without_newline_is_kept(
    tmp_path: Path,
) -> None:
    path = write_journal(
        tmp_path,
        '{"value":1}\n{"value":2}',
    )

    result = read_json_lines(path)

    assert result.records == (
        {"value": 1},
        {"value": 2},
    )
    assert result.incomplete_final_line_ignored is False


def test_incomplete_final_line_is_ignored(
    tmp_path: Path,
) -> None:
    path = write_journal(
        tmp_path,
        '{"value":1}\n{"value":2}\n{"value":',
    )

    result = read_json_lines(path)

    assert result.records == (
        {"value": 1},
        {"value": 2},
    )
    assert result.incomplete_final_line_ignored is True


def test_invalid_json_in_middle_is_reported(
    tmp_path: Path,
) -> None:
    path = write_journal(
        tmp_path,
        '{"value":1}\nnot-json\n{"value":2}\n',
    )

    with pytest.raises(
        JournalReadError,
        match="line 2",
    ):
        read_json_lines(path)


def test_invalid_final_line_with_newline_is_reported(
    tmp_path: Path,
) -> None:
    path = write_journal(
        tmp_path,
        '{"value":1}\n{"value":\n',
    )

    with pytest.raises(
        JournalReadError,
        match="line 2",
    ):
        read_json_lines(path)


def test_non_object_json_value_is_rejected(
    tmp_path: Path,
) -> None:
    path = write_journal(
        tmp_path,
        '{"value":1}\n[1,2,3]\n',
    )

    with pytest.raises(
        JournalReadError,
        match="must contain a JSON object",
    ):
        read_json_lines(path)


def test_empty_line_is_reported(
    tmp_path: Path,
) -> None:
    path = write_journal(
        tmp_path,
        '{"value":1}\n\n{"value":2}\n',
    )

    with pytest.raises(
        JournalReadError,
        match="empty line at line 2",
    ):
        read_json_lines(path)


def test_missing_journal_has_informative_error(
    tmp_path: Path,
) -> None:
    path = tmp_path / "missing.jsonl"

    with pytest.raises(
        FileNotFoundError,
        match="Recovery journal was not found",
    ):
        read_json_lines(path)


def test_non_utf8_journal_is_rejected(
    tmp_path: Path,
) -> None:
    path = tmp_path / "journal.jsonl"
    path.write_bytes(b'{"value":"\xff"}\n')

    with pytest.raises(
        JournalReadError,
        match="not valid UTF-8",
    ):
        read_json_lines(path)