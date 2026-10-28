"""Small values remembered between sessions, such as the last operator.

These are conveniences, not settings: losing the file only means a field
starts empty, so a missing or unreadable file is never an error.
"""

import json
from pathlib import Path

from rig_control.atomic_replace import replace_atomically


class RememberedValues:
    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    def get(self, key: str, default: str = "") -> str:
        value = self._load().get(key, default)
        return value if isinstance(value, str) else default

    def set(self, key: str, value: str) -> None:
        values = self._load()
        if values.get(key) == value:
            return
        values[key] = value
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self._path.with_suffix(self._path.suffix + ".tmp")
            temporary.write_text(
                json.dumps(values, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            replace_atomically(temporary, self._path)
        except OSError:
            pass  # Only a convenience; never block recording over it.

    def _load(self) -> dict[str, object]:
        try:
            values = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return values if isinstance(values, dict) else {}
