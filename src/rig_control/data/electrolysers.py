"""Electrolyser folders, which hold every run made on that electrolyser.

The output folder holds one folder per electrolyser, named
``BUILDER-E###-YYYY-MM`` (for example ``TPS-E057-2026-11``): the builder's
initials, the builder's own three-digit build number and the month it was
built. Runs go inside, named as in ``experiment_naming``; runs on no
electrolyser go in ``No-electrolyser``. Electrolyser names use hyphens only,
so they never look like a run ID.

``electrolyser.json`` marks a folder as an electrolyser and holds its build
details. No detail is compulsory, and fields the app does not know about
are kept, so the list can grow or become stricter later.
"""

import json
import os
import re
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime
from pathlib import Path

ELECTROLYSER_FILE = "electrolyser.json"
NO_ELECTROLYSER_FOLDER = "No-electrolyser"
#: Run metadata key holding the electrolyser's record as it was at run start.
ELECTROLYSER_METADATA_KEY = "electrolyser_json"
RECORD_FORMAT = 1

#: The usual build details, in the order the form shows them. Any other
#: field an electrolyser has is shown after these.
DETAIL_FIELDS = (
    ("design", "Cell design / type"),
    ("active_area_cm2", "Active area (cm²)"),
    ("membrane", "Membrane"),
    ("anode_catalyst", "Anode catalyst"),
    ("anode_loading", "Anode loading (mg/cm²)"),
    ("anode_ptl", "Anode PTL"),
    ("cathode_catalyst", "Cathode catalyst"),
    ("cathode_loading", "Cathode loading (mg/cm²)"),
    ("cathode_gdl", "Cathode GDL"),
    ("gaskets", "Gaskets"),
    ("torque", "Torque (N·m)"),
    ("notes", "Notes"),
)
AREA_FIELD = "active_area_cm2"
#: Details specific to one build, so a copy starts without them.
NOT_COPIED = frozenset({"notes"})

_NAME = re.compile(r"^(?P<builder>[A-Za-z0-9]+)-E(?P<number>\d{3,})-(?P<year>\d{4})-(?P<month>\d{2})$")


def builder_part(initials: str) -> str:
    """Initials with anything other than letters and digits removed."""

    return re.sub(r"[^A-Za-z0-9]", "", initials)


def make_electrolyser_name(builder: str, number: int, built_on: date) -> str:
    initials = builder_part(builder)
    if not initials:
        raise ValueError("Enter the builder's initials.")
    return f"{initials}-E{number:03d}-{built_on:%Y-%m}"


def clean_electrolyser_name(text: str) -> str:
    """A safe folder name: letters, digits and single hyphens only."""

    return re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-")


def parse_name(name: str) -> tuple[str, int] | None:
    """(builder, number) for a name in the standard form, otherwise None."""

    match = _NAME.match(name)
    return (match["builder"], int(match["number"])) if match else None


def next_number(root: str | Path, builder: str) -> int:
    """One more than the builder's highest number, so none is ever reused.

    Every folder in the standard form counts, retired or not, and with or
    without ``electrolyser.json``.
    """

    wanted = builder_part(builder).casefold()
    highest = 0
    try:
        folders = [path.name for path in Path(root).iterdir() if path.is_dir()]
    except OSError:
        folders = []
    for name in folders:
        parsed = parse_name(name)
        if parsed is not None and parsed[0].casefold() == wanted:
            highest = max(highest, parsed[1])
    return highest + 1


def suggest_name(root: str | Path, builder: str, built_on: date) -> str:
    return make_electrolyser_name(builder, next_number(root, builder), built_on)


@dataclass(frozen=True)
class Electrolyser:
    name: str
    builder: str = ""
    built_on: date | None = None
    details: dict[str, str] = field(default_factory=dict)
    copied_from: str = ""
    retired: bool = False
    history: tuple[dict[str, str], ...] = ()
    #: Anything else in the file, kept as it was.
    other: dict[str, object] = field(default_factory=dict)

    @property
    def number(self) -> int | None:
        parsed = parse_name(self.name)
        return parsed[1] if parsed else None

    @property
    def area(self) -> str:
        return self.details.get(AREA_FIELD, "").strip()

    def to_dict(self) -> dict[str, object]:
        return {
            **self.other,
            "format": RECORD_FORMAT,
            "name": self.name,
            "builder": self.builder,
            "built_on": self.built_on.isoformat() if self.built_on else "",
            "copied_from": self.copied_from,
            "retired": self.retired,
            "details": dict(self.details),
            "history": list(self.history),
        }

    @classmethod
    def from_dict(cls, data: dict[str, object], name: str) -> "Electrolyser":
        known = {"format", "name", "builder", "built_on", "copied_from", "retired", "details", "history"}
        try:
            built_on = date.fromisoformat(str(data.get("built_on") or ""))
        except ValueError:
            built_on = None
        details = data.get("details")
        history = data.get("history")
        return cls(
            # The folder is the name; a hand-renamed folder still opens.
            name=name,
            builder=str(data.get("builder") or ""),
            built_on=built_on,
            details={str(key): str(value) for key, value in details.items()}
            if isinstance(details, dict) else {},
            copied_from=str(data.get("copied_from") or ""),
            retired=bool(data.get("retired", False)),
            history=tuple(entry for entry in history if isinstance(entry, dict))
            if isinstance(history, list) else (),
            other={key: value for key, value in data.items() if key not in known},
        )

    def copied_as(self, name: str, builder: str, built_on: date) -> "Electrolyser":
        """A new build starting from this one's details."""

        return Electrolyser(
            name=name, builder=builder, built_on=built_on,
            details={key: value for key, value in self.details.items() if key not in NOT_COPIED},
            copied_from=self.name,
        )


def load_electrolyser(folder: str | Path) -> Electrolyser | None:
    """The electrolyser in ``folder``, or None if it is not one (or unreadable)."""

    folder = Path(folder)
    try:
        data = json.loads((folder / ELECTROLYSER_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return Electrolyser.from_dict(data, folder.name) if isinstance(data, dict) else None


def list_electrolysers(root: str | Path, *, include_retired: bool = False) -> list[Electrolyser]:
    try:
        folders = sorted(path for path in Path(root).iterdir() if path.is_dir())
    except OSError:
        return []
    found = (load_electrolyser(folder) for folder in folders)
    return [item for item in found if item is not None and (include_retired or not item.retired)]


def latest_by(electrolysers: list[Electrolyser], builder: str) -> Electrolyser | None:
    """The builder's most recent build: highest number, then latest date."""

    wanted = builder_part(builder).casefold()
    own = [item for item in electrolysers if builder_part(item.builder).casefold() == wanted]
    if not own:
        return None
    return max(own, key=lambda item: (item.number or 0, item.built_on or date.min))


def create_electrolyser(root: str | Path, electrolyser: Electrolyser) -> Path:
    """Make the folder and its ``electrolyser.json``; an existing name is refused."""

    name = clean_electrolyser_name(electrolyser.name)
    if not name:
        raise ValueError("Enter a name for the electrolyser.")
    if name.casefold() == NO_ELECTROLYSER_FOLDER.casefold():
        raise ValueError(f"{NO_ELECTROLYSER_FOLDER} is kept for runs without an electrolyser.")
    if "_" in electrolyser.name:
        raise ValueError("Electrolyser names use hyphens, not underscores, so they never look like a run.")
    folder = Path(root) / name
    if folder.exists():
        raise FileExistsError(f"{name} already exists in {root}.")
    folder.mkdir(parents=True)
    _write(folder, replace(electrolyser, name=name))
    return folder


def update_electrolyser(root: str | Path, electrolyser: Electrolyser, *, by: str = "",
                        change: str = "") -> Electrolyser:
    """Save new details, logging what changed so the cell's history is kept."""

    folder = Path(root) / electrolyser.name
    previous = load_electrolyser(folder)
    if previous is None:
        raise FileNotFoundError(f"{folder} is not an electrolyser folder.")
    changed = sorted(
        key for key in set(previous.details) | set(electrolyser.details)
        if previous.details.get(key, "") != electrolyser.details.get(key, "")
    )
    if previous.built_on != electrolyser.built_on:
        changed.append("built_on")
    if previous.retired != electrolyser.retired:
        changed.append("retired")
    if not changed and not change.strip():
        return previous
    entry = {"at": datetime.now(UTC).isoformat(timespec="seconds"), "by": by.strip()}
    if change.strip():
        entry["change"] = change.strip()
    if changed:
        entry["fields"] = ", ".join(changed)
    updated = replace(electrolyser, history=(*previous.history, entry), other=previous.other)
    _write(folder, updated)
    return updated


def _write(folder: Path, electrolyser: Electrolyser) -> None:
    path = folder / ELECTROLYSER_FILE
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(electrolyser.to_dict(), indent=2, ensure_ascii=False) + "\n",
                         encoding="utf-8")
    os.replace(temporary, path)


def run_folder(output_directory: str | Path, electrolyser_id: str | None) -> Path:
    """Where a run's folder goes: inside its electrolyser, or ``No-electrolyser``."""

    name = clean_electrolyser_name(electrolyser_id or "")
    return Path(output_directory) / (name or NO_ELECTROLYSER_FOLDER)
