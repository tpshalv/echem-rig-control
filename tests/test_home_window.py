from rig_control.ui.home.model import FEATURE_DEVICE_SETUP
from rig_control.ui.home.window import HomeWindow


class FakeViewModel:
    def __init__(self) -> None:
        self.ended_features: list[str] = []

    def end_feature(self, feature: str) -> None:
        self.ended_features.append(feature)


class FakeToplevel:
    def __init__(self) -> None:
        self.destroyed = False

    def destroy(self) -> None:
        self.destroyed = True


def _prepare_home_window() -> tuple[HomeWindow, FakeViewModel]:
    window = HomeWindow.__new__(HomeWindow)
    view_model = FakeViewModel()
    window._view_model = view_model
    window._diagnostics_close = None
    window._operation_close = None
    window._device_setup_child = None
    window._settings_child = None
    return window, view_model


def test_close_all_features_calls_open_screen_closers_in_order() -> None:
    window, _ = _prepare_home_window()
    calls: list[str] = []
    window._diagnostics_close = lambda: calls.append("diagnostics")
    window._operation_close = lambda: calls.append("operation")

    window.close_all_features()

    assert calls == ["diagnostics", "operation"]


def test_close_all_features_skips_screens_that_are_not_open() -> None:
    window, view_model = _prepare_home_window()

    window.close_all_features()

    assert view_model.ended_features == []


def test_close_all_features_destroys_device_setup_without_resuming() -> None:
    window, view_model = _prepare_home_window()
    device_setup_child = FakeToplevel()
    window._device_setup_child = device_setup_child

    window.close_all_features()

    assert device_setup_child.destroyed is True
    assert window._device_setup_child is None
    # Unlike the normal Device Setup close path, this must not try to
    # rebuild/reconnect a session - the whole application is exiting.
    assert view_model.ended_features == [FEATURE_DEVICE_SETUP]


def test_close_all_features_destroys_settings_window() -> None:
    window, _ = _prepare_home_window()
    settings_child = FakeToplevel()
    window._settings_child = settings_child

    window.close_all_features()

    assert settings_child.destroyed is True
    assert window._settings_child is None
