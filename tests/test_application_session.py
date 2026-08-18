from pathlib import Path

from rig_control.app_settings import AppSettings
from rig_control.application_session import ApplicationSession
from rig_control.rig_profile_loading import load_rig_profile


def test_session_owns_one_shared_runtime_graph(tmp_path: Path) -> None:
    profile = load_rig_profile("rig-profile.simulation.toml")
    settings = AppSettings(
        "test",
        "Test",
        {"technical_log_path": "session.log"},
    )

    session = ApplicationSession(
        profile,
        settings,
        settings_directory=tmp_path,
    )
    try:
        assert session.control_service._device_manager is (  # type: ignore[attr-defined]
            session.device_manager
        )
        assert session.polling_service._device_manager is (  # type: ignore[attr-defined]
            session.device_manager
        )
    finally:
        session.close()

    assert (tmp_path / "session.log").exists()


def test_close_stops_polling_and_disconnects(tmp_path: Path) -> None:
    session = ApplicationSession(
        load_rig_profile("rig-profile.simulation.toml"),
        AppSettings(
            "test",
            "Test",
            {"technical_log_path": "session.log"},
        ),
        settings_directory=tmp_path,
    )
    for device_id in session.device_manager.device_ids:
        session.device_manager.connect(device_id)
    session.polling_service.start()

    failures = session.close()

    assert failures == ()
    assert session.polling_service.is_running is False
    assert all(
        summary.status.value == "disconnected"
        for summary in session.device_manager.summaries()
    )
    assert session.close() == ()
