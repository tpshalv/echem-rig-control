from queue import Queue

from rig_control.ui.operation.window import OperationWindow


class FailingModel:
    def collect_polling_results(self) -> int:
        raise RuntimeError("simulated UI tick failure")


def test_failed_ui_tick_is_logged_and_rescheduled() -> None:
    recorded = []
    window = OperationWindow.__new__(OperationWindow)
    window._connection_results = Queue()
    window._view_model = FailingModel()
    window._event_sink = lambda event, details: recorded.append((event, details))
    window._refresh_failed = False
    scheduled = []
    window._schedule_update = lambda: scheduled.append(True)

    window._poll_ui_queue()

    assert scheduled == [True]
    assert len(recorded) == 1
    assert "simulated UI tick failure" in recorded[0][0].message
    assert "Traceback" in recorded[0][1]


def test_repeated_ui_failure_is_not_logged_every_tick() -> None:
    recorded = []
    window = OperationWindow.__new__(OperationWindow)
    window._connection_results = Queue()
    window._view_model = FailingModel()
    window._event_sink = lambda event, details: recorded.append((event, details))
    window._refresh_failed = False
    window._schedule_update = lambda: None

    window._poll_ui_queue()
    window._poll_ui_queue()

    assert len(recorded) == 1
