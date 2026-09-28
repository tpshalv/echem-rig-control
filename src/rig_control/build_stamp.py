"""Identify which build is running.

A built folder is named after the version, date and commit, but two builds
made on one day from one commit are named identically. The stamp is shown in
the application title so a stale copy on another machine is obvious without
opening any file.
"""

import json
import sys
from pathlib import Path


def build_directory() -> Path:
    """Return the folder holding the running executable, or the checkout."""

    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent.parent


def build_stamp() -> str:
    """Return a short description of this build, or "" when unknown.

    Never raises: a missing or damaged build-info.json only means the stamp
    cannot be shown, which must not stop the application starting.
    """

    try:
        text = (build_directory() / "build-info.json").read_text(encoding="utf-8")
        info = json.loads(text)
        version = str(info.get("version", "")).strip()
        built_at = str(info.get("built_at", "")).strip()
        commit = str(info.get("git_commit", "")).strip()
    except (OSError, ValueError, TypeError):
        return ""

    if not version and not built_at:
        return ""
    parts = [part for part in (version, built_at.replace("T", " ")) if part]
    stamp = " ".join(parts)
    if commit:
        stamp += f" ({commit}{'-dirty' if info.get('uncommitted_changes') else ''})"
    return stamp
