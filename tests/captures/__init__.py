"""Load instrument captures so tests replay what the hardware really said."""

import ast
import re
from pathlib import Path

CAPTURES = Path(__file__).resolve().parent

#: A dump line: the command, an arrow, then the reply as a Python repr.
_LINE = re.compile(r"^(?P<command>\S+(?: \S+)*?)\s+(?:->|!!)\s+(?P<reply>'.*')\s*\(")


def load_capture(name: str) -> dict[str, str]:
    """Return {command: reply} from one capture file.

    Comment lines starting with # carry the provenance of the capture and are
    not replies.
    """

    path = CAPTURES / name
    replies: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        found = _LINE.match(line)
        if found is None:
            raise ValueError(f"Unreadable capture line in {name}: {line!r}")
        replies[found["command"]] = ast.literal_eval(found["reply"])
    if not replies:
        raise ValueError(f"Capture {name} contains no replies")
    return replies
