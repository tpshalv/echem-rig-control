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


def test_saved_nplc_reaches_keithley_on_connection_not_on_each_poll(tmp_path, monkeypatch):
    from rig_control.app_settings_loading import load_app_settings
    from rig_control.app_settings_writing import write_app_settings
    from test_keithley_2260b_driver import FakeScpiTransport
    from test_keithley_2280s_configuration import make_profile

    transport = FakeScpiTransport()
    monkeypatch.setattr(
        "rig_control.device_factory.SocketScpiTransport", lambda **kwargs: transport,
    )
    settings_path = tmp_path / "settings.toml"
    write_app_settings(AppSettings("test", "Test", {
        "technical_log_path": "session.log", "keithley_2280s_nplc": 0.25,
    }), settings_path)
    session = ApplicationSession(
        make_profile(), load_app_settings(settings_path), settings_directory=tmp_path,
    )
    try:
        assert transport.writes == []
        for command, response in (
            ("*IDN?", "KEITHLEY INSTRUMENTS,MODEL 2280S-32-6,12345,01.06"),
            ("SOUR:VOLT:LEV:IMM:AMPL?", "1"),
            ("SOUR:CURR:LEV:IMM:AMPL?", "0.05"),
            ("OUTP:STAT?", "1"),
        ):
            transport.queue_response(command, response)
        supply = session.device_manager.get("supply")
        supply.connect()
        assert transport.writes == [
            'SENS:FUNC "CONC"', "SENS:CONC:NPLC 0.25", 'FORM:ELEM "READ,SOUR,MODE"',
            "ARM:SOUR IMM", "TRIG:SOUR IMM", "INIT:CONT ON", "OUTP:DEL:STAT OFF",
        ]
        setup_writes = transport.writes.copy()
        for _ in range(2):
            transport.queue_response("FETC?", "0.05,0.505,CC")
            supply.read_measurements()
        assert transport.writes == setup_writes
    finally:
        session.close()
