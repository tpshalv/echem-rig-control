"""Build the standalone Windows app. Run from anywhere: python scripts/build_exe.py

Output: dist/echem-rig-control-<version>-<date>-<commit>[-dirty]/ containing the .exe,
the default/example .toml files, and build-info.json (build date, git commit, and
recent commit messages, so an old build can be identified without bumping the version).
Requires: pip install -e .[hardware,build]
"""

import json
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENTRY_POINT = ROOT / "src" / "rig_control" / "ui" / "home" / "window.py"
APP_NAME = "echem-rig-control"
# Modules PyInstaller cannot discover because they are imported dynamically.
HIDDEN_IMPORT_PACKAGES = ["pyvisa_py", "pyvisa", "serial"]
RECENT_COMMIT_COUNT = 20
# Files the app reads relative to its working directory; copied next to the .exe.
SIDECAR_FILES = [
    "app-settings.default.toml",
    "rig-profile.example.toml",
    "rig-profile.simulation.toml",
    "rig-profile.esp32.toml",
    "experiment-profile.example.toml",
]


def read_version() -> str:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    if match is None:
        raise SystemExit("Could not find version in pyproject.toml")
    return match.group(1)


def git(*args: str) -> str:
    try:
        result = subprocess.run(
            ["git", *args], cwd=ROOT, capture_output=True, text=True, check=True
        )
    except (OSError, subprocess.CalledProcessError):
        return ""
    return result.stdout.strip()


def collect_build_info(version: str, built_at: datetime) -> dict[str, object]:
    commit = git("rev-parse", "--short", "HEAD") or "unknown"
    return {
        "version": version,
        "built_at": built_at.isoformat(timespec="seconds"),
        "git_commit": commit,
        "git_branch": git("rev-parse", "--abbrev-ref", "HEAD") or "unknown",
        "uncommitted_changes": bool(git("status", "--porcelain", "--", ".", ":!build", ":!dist")),
        "recent_commits": git("log", f"-{RECENT_COMMIT_COUNT}", "--pretty=%h %ad %s", "--date=short").splitlines(),
    }


def main() -> int:
    version = read_version()
    built_at = datetime.now()
    info = collect_build_info(version, built_at)
    suffix = f"{built_at:%Y%m%d}-{info['git_commit']}" + ("-dirty" if info["uncommitted_changes"] else "")
    build_name = f"{APP_NAME}-{version}-{suffix}"
    dist_dir = ROOT / "dist"
    work_dir = ROOT / "build"

    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        str(ENTRY_POINT),
        "--noconfirm",
        "--clean",
        "--windowed",
        "--name",
        build_name,
        "--paths",
        str(ROOT / "src"),
        "--distpath",
        str(dist_dir),
        "--workpath",
        str(work_dir),
        "--specpath",
        str(work_dir),
    ]
    for package in HIDDEN_IMPORT_PACKAGES:
        command += ["--collect-submodules", package]

    print("Running:", " ".join(command))
    subprocess.run(command, check=True, cwd=ROOT)

    output_dir = dist_dir / build_name
    for name in SIDECAR_FILES:
        source = ROOT / name
        if source.exists():
            shutil.copy2(source, output_dir)

    (output_dir / "build-info.json").write_text(json.dumps(info, indent=2), encoding="utf-8")

    print(f"\nBuilt {build_name} -> {output_dir}")
    print(f"Run: {output_dir / (build_name + '.exe')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
