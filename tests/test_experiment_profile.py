from pathlib import Path
from dataclasses import replace

import pytest

from rig_control.experiment_profile import (
    ExperimentDevice,
    ExperimentProfile,
    load_experiment_profile,
    resolve_experiment_profile,
    write_experiment_profile,
)
from rig_control.rig_profile_loading import load_rig_profile


def test_experiment_selects_library_devices_without_copying_hardware() -> None:
    library = load_rig_profile("rig-profile.example.toml")
    experiment = ExperimentProfile(
        "dry_co2_run",
        "Dry CO2 experiment",
        (
            ExperimentDevice("nitrogen_mfc", "Nitrogen"),
            ExperimentDevice("dry_co2_mfc", "Dry CO2"),
        ),
    )

    resolved = resolve_experiment_profile(library, experiment)

    assert tuple(role.device_id for role in resolved.device_roles) == (
        "nitrogen_mfc",
        "dry_co2_mfc",
    )
    assert len(resolved.connections) == 1
    assert resolved.get_role("dry_co2_mfc").settings["maximum_flow"] == 200.0
    assert resolved.get_role("dry_co2_mfc").friendly_name == "Dry CO2"


def test_library_change_automatically_applies_to_every_experiment() -> None:
    library = load_rig_profile("rig-profile.example.toml")
    experiment = ExperimentProfile(
        "test",
        "Test",
        (ExperimentDevice("nitrogen_mfc"),),
    )
    original = resolve_experiment_profile(library, experiment)
    role = library.get_role("nitrogen_mfc")
    changed_role = replace(
        role,
        settings={**role.settings, "maximum_flow": 150.0},
    )
    changed_library = replace(
        library,
        device_roles=(changed_role,) + library.device_roles[1:],
    )

    changed = resolve_experiment_profile(changed_library, experiment)

    assert original.get_role("nitrogen_mfc").settings["maximum_flow"] == 200.0
    assert changed.get_role("nitrogen_mfc").settings["maximum_flow"] == 150.0


def test_unknown_library_device_is_rejected() -> None:
    library = load_rig_profile("rig-profile.example.toml")
    experiment = ExperimentProfile(
        "invalid",
        "Invalid",
        (ExperimentDevice("missing_device"),),
    )

    with pytest.raises(KeyError, match="missing_device"):
        resolve_experiment_profile(library, experiment)


def test_experiment_profile_round_trips_and_creates_backup(
    tmp_path: Path,
) -> None:
    profile = ExperimentProfile(
        "humidified",
        "Humidified CO2",
        (
            ExperimentDevice("nitrogen_mfc", "Nitrogen"),
            ExperimentDevice("wet_co2_mfc", "Wet CO2", required=False),
        ),
    )
    path = tmp_path / "humidified.toml"

    assert write_experiment_profile(profile, path) is None
    assert load_experiment_profile(path) == profile
    backup = write_experiment_profile(profile, path)

    assert backup == tmp_path / "humidified.toml.bak"
    assert backup.exists()


def test_duplicate_experiment_device_is_rejected() -> None:
    with pytest.raises(ValueError, match="Duplicate experiment device"):
        ExperimentProfile(
            "duplicate",
            "Duplicate",
            (
                ExperimentDevice("mfc_a"),
                ExperimentDevice("mfc_a"),
            ),
        )


def test_example_experiment_resolves_against_example_library() -> None:
    library = load_rig_profile("rig-profile.example.toml")
    experiment = load_experiment_profile(
        "experiment-profile.example.toml"
    )

    resolved = resolve_experiment_profile(library, experiment)

    assert tuple(role.device_id for role in resolved.enabled_roles) == (
        "nitrogen_mfc",
        "wet_co2_mfc",
        "main_power_supply",
    )
