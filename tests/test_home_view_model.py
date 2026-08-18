from pathlib import Path

from rig_control.ui.home.model import HomeViewModel


class FakeSession:
    created: list["FakeSession"] = []

    def __init__(self, profile, settings, *, settings_directory) -> None:
        self.profile = profile
        self.settings = settings
        self.settings_directory = settings_directory
        self.closed = False
        self.created.append(self)

    def close(self) -> tuple[str, ...]:
        self.closed = True
        return ()


def make_model(tmp_path: Path) -> HomeViewModel:
    FakeSession.created.clear()
    return HomeViewModel(
        rig_profile_path="rig-profile.simulation.toml",
        settings_path="app-settings.default.toml",
        selection_path=tmp_path / "selection.toml",
        session_factory=FakeSession,
    )


def test_feature_screens_share_one_session_until_configuration_changes(
    tmp_path: Path,
) -> None:
    model = make_model(tmp_path)
    original = model.session

    assert model.begin_feature().succeeded is True
    model.end_feature()
    assert model.begin_feature().succeeded is True
    model.end_feature()

    assert model.session is original
    assert len(FakeSession.created) == 1


def test_applying_settings_rebuilds_the_session(tmp_path: Path) -> None:
    model = make_model(tmp_path)
    original = model.session

    result = model.apply_setting_text(
        {
            "publish_interval_seconds": "0.25",
            "technical_log_path": "logs/alternate.log",
        }
    )

    assert result.succeeded is True
    assert original.closed is True
    assert model.session is not original
    assert model.settings.values["publish_interval_seconds"] == 0.25


def test_device_setup_temporarily_releases_and_rebuilds_session(
    tmp_path: Path,
) -> None:
    model = make_model(tmp_path)
    original = model.session

    assert model.suspend_for_device_setup().succeeded is True
    assert original.closed is True
    assert model.feature_active is True

    assert model.resume_after_device_setup().succeeded is True
    assert model.feature_active is False
    assert model.session is not original
