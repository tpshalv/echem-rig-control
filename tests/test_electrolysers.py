import json
import tkinter as tk
from datetime import UTC, date, datetime
from functools import partial

import pytest
from openpyxl import load_workbook

from rig_control.control.service import RigControlService
from rig_control.data.directory_writer import DirectoryExperimentWriter
from rig_control.data.electrolysers import (
    ELECTROLYSER_FILE,
    Electrolyser,
    create_electrolyser,
    latest_by,
    list_electrolysers,
    load_electrolyser,
    make_electrolyser_name,
    next_number,
    parse_name,
    run_folder,
    update_electrolyser,
)
from rig_control.data.experiment_naming import existing_recording
from rig_control.devices.manager import DeviceManager
from rig_control.devices.simulated_sensor import SimulatedSensor
from rig_control.experiment_recording import ExperimentRecorder
from rig_control.polling import PollingService
from rig_control.ui.operation.model import OperationViewModel

BUILT = date(2026, 11, 3)
STARTED = datetime(2026, 11, 3, 10, 15, 30).astimezone()


def build(root, name, builder="TPS", **details):
    return create_electrolyser(root, Electrolyser(name=name, builder=builder, built_on=BUILT,
                                                  details=details))


# --- names and numbers ---

def test_name_is_builder_three_digit_number_and_build_month() -> None:
    assert make_electrolyser_name("TPS", 57, BUILT) == "TPS-E057-2026-11"
    assert make_electrolyser_name("T.P.S", 3, BUILT) == "TPS-E003-2026-11"
    assert make_electrolyser_name("TPS", 1000, BUILT) == "TPS-E1000-2026-11"
    assert parse_name("TPS-E057-2026-11") == ("TPS", 57)
    assert parse_name("TPS_2026-11-03_10h15") is None  # A run is never an electrolyser.
    with pytest.raises(ValueError, match="initials"):
        make_electrolyser_name(" ", 1, BUILT)


def test_next_number_follows_the_builders_highest_and_never_reuses(tmp_path) -> None:
    assert next_number(tmp_path, "TPS") == 1
    build(tmp_path, "TPS-E055-2026-09")
    (tmp_path / "TPS-E056-2026-10").mkdir()  # Abandoned, no electrolyser.json: still taken.
    build(tmp_path, "JDS-E090-2026-10", builder="JDS")
    build(tmp_path, "B142")  # Older, hand-named cells are ignored.

    assert next_number(tmp_path, "TPS") == 57
    assert next_number(tmp_path, "tps") == 57
    assert next_number(tmp_path, "JDS") == 91
    assert next_number(tmp_path / "missing", "TPS") == 1


# --- the record ---

def test_record_round_trips_and_keeps_fields_it_does_not_know(tmp_path) -> None:
    folder = build(tmp_path, "TPS-E001-2026-11", membrane="N115", **{"Membrane batch": "L42"})
    data = json.loads((folder / ELECTROLYSER_FILE).read_text(encoding="utf-8"))
    data["added_later"] = {"rigorous": True}
    (folder / ELECTROLYSER_FILE).write_text(json.dumps(data), encoding="utf-8")

    loaded = load_electrolyser(folder)
    assert loaded is not None
    assert (loaded.builder, loaded.built_on, loaded.number) == ("TPS", BUILT, 1)
    assert loaded.details == {"membrane": "N115", "Membrane batch": "L42"}
    update_electrolyser(tmp_path, loaded, by="TPS", change="tightened")
    assert json.loads((folder / ELECTROLYSER_FILE).read_text(encoding="utf-8"))["added_later"] == {
        "rigorous": True}


def test_nothing_but_the_name_is_compulsory(tmp_path) -> None:
    folder = create_electrolyser(tmp_path, Electrolyser(name="TPS-E001-2026-11"))
    loaded = load_electrolyser(folder)
    assert loaded is not None and loaded.details == {} and loaded.built_on is None


@pytest.mark.parametrize("name, problem", [
    ("", "name"), ("No-electrolyser", "kept for runs"), ("TPS_E001", "underscores"),
])
def test_unusable_names_are_refused(tmp_path, name, problem) -> None:
    with pytest.raises(ValueError, match=problem):
        create_electrolyser(tmp_path, Electrolyser(name=name))


def test_an_existing_electrolyser_is_never_overwritten(tmp_path) -> None:
    build(tmp_path, "TPS-E001-2026-11", membrane="N115")
    with pytest.raises(FileExistsError):
        build(tmp_path, "TPS-E001-2026-11", membrane="other")
    assert load_electrolyser(tmp_path / "TPS-E001-2026-11").details == {"membrane": "N115"}


def test_a_copy_keeps_the_build_details_but_not_the_notes(tmp_path) -> None:
    build(tmp_path, "TPS-E001-2026-10", membrane="N115", notes="leaked at 2 bar",
          **{"Membrane batch": "L42"})
    build(tmp_path, "TPS-E002-2026-11", membrane="N117")
    build(tmp_path, "JDS-E009-2026-11", builder="JDS", membrane="other")
    found = list_electrolysers(tmp_path)

    latest = latest_by(found, "TPS")
    assert latest is not None and latest.name == "TPS-E002-2026-11"
    copy = found[1].copied_as("TPS-E003-2026-11", "TPS", BUILT)
    assert copy.details == {"membrane": "N115", "Membrane batch": "L42"}
    assert copy.copied_from == "TPS-E001-2026-10"
    assert latest_by(found, "XYZ") is None


def test_changes_are_logged_and_retired_cells_leave_the_list(tmp_path) -> None:
    build(tmp_path, "TPS-E001-2026-11", membrane="N115")
    original = load_electrolyser(tmp_path / "TPS-E001-2026-11")

    assert update_electrolyser(tmp_path, original, by="TPS") == original  # Nothing changed.
    from dataclasses import replace
    updated = update_electrolyser(
        tmp_path, replace(original, details={"membrane": "N117"}, retired=True),
        by="TPS", change="membrane replaced",
    )

    assert [(entry["by"], entry["change"], entry["fields"]) for entry in updated.history] == [
        ("TPS", "membrane replaced", "membrane, retired")]
    assert list_electrolysers(tmp_path) == []
    assert [item.name for item in list_electrolysers(tmp_path, include_retired=True)] == [
        "TPS-E001-2026-11"]
    assert next_number(tmp_path, "TPS") == 2


def test_runs_are_filed_in_their_electrolyser(tmp_path) -> None:
    assert run_folder(tmp_path, "TPS-E001-2026-11") == tmp_path / "TPS-E001-2026-11"
    assert run_folder(tmp_path, "") == tmp_path / "No-electrolyser"
    assert run_folder(tmp_path, None) == tmp_path / "No-electrolyser"


# --- recordings ---

def make_model():
    manager = DeviceManager()
    manager.register(SimulatedSensor("probe", 25.0, "degC"))
    recorder = ExperimentRecorder(writer_factory=partial(
        DirectoryExperimentWriter, live_export_interval_seconds=0))
    polling = PollingService(manager, interval_seconds=60.0, batch_handler=recorder.record_batch)
    model = OperationViewModel(manager, polling, recorder, RigControlService(manager),
                               profile_id="simulation")
    model.start_monitoring()
    return model


def test_a_run_carries_its_electrolysers_details_as_they_were(tmp_path) -> None:
    build(tmp_path, "TPS-E001-2026-11", membrane="N115", active_area_cm2="5")
    model = make_model()
    try:
        assert model.start_recording(operator="TPS", output_directory=tmp_path, started_at=STARTED,
                                     electrolyser_id="TPS-E001-2026-11").succeeded
        model.stop_recording()
    finally:
        model.shutdown()
    # Editing the cell afterwards does not change what the run recorded.
    from dataclasses import replace
    update_electrolyser(tmp_path, replace(load_electrolyser(tmp_path / "TPS-E001-2026-11"),
                                          details={"membrane": "N117"}), by="TPS")

    run = tmp_path / "TPS-E001-2026-11" / "TPS_2026-11-03_10h15"
    metadata = json.loads((run / "metadata.json").read_text(encoding="utf-8"))
    assert json.loads(metadata["extra"]["electrolyser_json"])["details"]["membrane"] == "N115"
    workbook = load_workbook(run / f"{run.name}.xlsx", read_only=True)
    try:
        overview = {row[0]: row[1] for row in workbook["Overview"].values if len(row) > 1}
    finally:
        workbook.close()
    assert overview["Electrolyser: Membrane"] == "N115"
    assert overview["Electrolyser build date"] == "2026-11-03"
    assert "extra.electrolyser_json" not in overview


def test_manual_and_recipe_runs_are_named_and_filed_the_same(tmp_path) -> None:
    build(tmp_path, "TPS-E001-2026-11")
    model = make_model()
    try:
        details = dict(operator="TPS", output_directory=tmp_path, started_at=STARTED,
                       description="humidity", electrolyser_id="TPS-E001-2026-11", notes="n")
        manual_root, manual = model.plan_recording(**details)
        recipe_root, recipe = model.plan_recording(**details, experiment_type="recipe")
    finally:
        model.shutdown()

    assert manual_root == recipe_root == tmp_path / "TPS-E001-2026-11"
    assert manual.experiment_id == recipe.experiment_id == "TPS_2026-11-03_10h15_humidity"
    assert (manual.experiment_type, recipe.experiment_type) == ("manual", "recipe")
    assert dict(manual.extra) == dict(recipe.extra)  # Profile, calibrations, build details.
    assert "rig_profile_id" in recipe.extra and "electrolyser_json" in recipe.extra


def test_planning_refuses_a_run_that_cannot_be_named(tmp_path) -> None:
    model = make_model()
    try:
        with pytest.raises(ValueError, match="initials"):
            model.plan_recording(operator=" ", output_directory=tmp_path, started_at=STARTED)
        with pytest.raises(ValueError, match="output folder"):
            model.plan_recording(operator="TPS", output_directory=" ", started_at=STARTED)
    finally:
        model.shutdown()


def test_a_recipe_replaces_an_old_recording_only_when_it_can_start(tmp_path) -> None:
    from tests.test_recipes import make_runner
    from rig_control.recipes import Recipe, WaitStep

    model = make_model()
    try:
        assert model.start_recording(operator="TPS", output_directory=tmp_path,
                                     started_at=STARTED).succeeded
        root, metadata = model.plan_recording(operator="TPS", output_directory=tmp_path,
                                              started_at=STARTED, experiment_type="recipe")
        runner, _supply, _mfc = make_runner()
        runner._recorder = model._experiment_recorder
        with pytest.raises(RuntimeError, match="recording is still running"):
            runner.start(Recipe("r", steps=(WaitStep(0),)), recording_root=root,
                         metadata=metadata, replace_existing=True)
        assert existing_recording(root, metadata.experiment_id) is not None  # Kept.

        model.stop_recording()
        runner.start(Recipe("r", steps=(WaitStep(0),)), recording_root=root,
                     metadata=metadata, replace_existing=True)
        runner.join(5)
    finally:
        model.shutdown()
    recorded = json.loads((root / metadata.experiment_id / "metadata.json").read_text(encoding="utf-8"))
    assert recorded["experiment_type"] == "recipe"


# --- the form ---

@pytest.fixture
def root():
    window = tk.Tk()
    window.withdraw()
    try:
        yield window
    finally:
        window.destroy()


def test_new_build_form_suggests_the_next_name_and_copies_the_latest(root, tmp_path) -> None:
    from rig_control.ui.operation.electrolyser_dialog import ElectrolyserDialog

    build(tmp_path, "TPS-E056-2026-10", membrane="N115", notes="old notes")
    build(tmp_path, "JDS-E002-2026-10", builder="JDS", membrane="JDS membrane")
    saved = []
    dialog = ElectrolyserDialog(root, tmp_path, operator="TPS", on_saved=saved.append, today=BUILT)
    try:
        assert dialog._name.get() == "TPS-E057-2026-11"
        assert dialog._source.get() == "TPS-E056-2026-10"
        assert dialog.details() == {"membrane": "N115"}

        # Another builder: the suggestion and copy follow, until either is changed by hand.
        dialog._builder.delete(0, "end"); dialog._builder.insert(0, "JDS")
        dialog._builder_or_date_typed()
        assert dialog._name.get() == "JDS-E003-2026-11"
        assert dialog.details() == {"membrane": "JDS membrane"}
        dialog._builder.delete(0, "end"); dialog._builder.insert(0, "TPS")
        dialog._builder_or_date_typed()

        dialog._detail_entries["anode_loading"].insert(0, "2.0")
        dialog._save()
    finally:
        dialog.close()

    assert [item.name for item in saved] == ["TPS-E057-2026-11"]
    created = load_electrolyser(tmp_path / "TPS-E057-2026-11")
    assert created.details == {"membrane": "N115", "anode_loading": "2.0"}
    assert (created.copied_from, created.built_on) == ("TPS-E056-2026-10", BUILT)


def test_operation_lists_electrolysers_fills_the_area_and_files_the_run(root, tmp_path, monkeypatch) -> None:
    from rig_control.ui.operation.window import OperationWindow

    build(tmp_path, "TPS-E001-2026-11", active_area_cm2="5")
    build(tmp_path, "TPS-E002-2026-11")
    monkeypatch.setattr("rig_control.ui.operation.window.RunReviewDialog", lambda *args, **kwargs: None)
    model = make_model()
    window = OperationWindow(root, model, profile_name="test", default_output_directory=str(tmp_path))
    try:
        window._entries["operator"].insert(0, "TPS")
        box = window._entries["electrolyser"]
        assert tuple(box.cget("values")) == ("No-electrolyser", "TPS-E001-2026-11", "TPS-E002-2026-11")
        assert window.recording_preview().startswith("No-electrolyser / TPS_")

        box.set("TPS-E001-2026-11"); window._electrolyser_chosen()
        assert window._entries["area"].get() == "5" and model.cell_area.cm2 == 5.0
        assert window.recording_preview().startswith("TPS-E001-2026-11 / TPS_")
        assert window.recording_details()["electrolyser"] == "TPS-E001-2026-11"
        box.set("TPS-E002-2026-11"); window._electrolyser_chosen()
        assert window._entries["area"].get() == ""  # Never carried over from another build.
        box.set("TPS-E001-2026-11"); window._electrolyser_chosen()

        window._run_operation_action = lambda _description, action: action()
        window._toggle_recording()
        assert model.is_recording
        assert model.experiment_directory.parent == tmp_path / "TPS-E001-2026-11"
        window._update_display()
        assert str(box.cget("state")) == "disabled"
        model.stop_recording()
    finally:
        model.shutdown()


def test_edit_form_logs_what_changed(root, tmp_path) -> None:
    from rig_control.ui.operation.electrolyser_dialog import ElectrolyserDialog

    build(tmp_path, "TPS-E001-2026-11", membrane="N115")
    saved = []
    dialog = ElectrolyserDialog(root, tmp_path, operator="JDS", on_saved=saved.append,
                                existing=load_electrolyser(tmp_path / "TPS-E001-2026-11"))
    try:
        dialog._detail_entries["membrane"].delete(0, "end")
        dialog._detail_entries["membrane"].insert(0, "N117")
        dialog._change.insert(0, "membrane replaced")
        dialog._save()
    finally:
        dialog.close()

    history = load_electrolyser(tmp_path / "TPS-E001-2026-11").history
    assert [(entry["by"], entry["change"]) for entry in history] == [("JDS", "membrane replaced")]
    assert saved[0].details == {"membrane": "N117"}
