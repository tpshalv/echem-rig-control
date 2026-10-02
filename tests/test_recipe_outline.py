import pytest

from rig_control.recipes import LoopStep, RepeatStep, SetStep, WaitStep
from rig_control.recipes.outline import (
    LoopEnd, OutlineError, delete, depths, duplicate, from_outline, insert, move, partner, to_outline,
)

A = SetStep.one("mfc", "flow", 1)
B = SetStep.one("mfc", "flow", 2)
W = WaitStep(60)
NEXT = LoopEnd()
CURRENT = LoopStep("supply", "current_setpoint", (1, 2))
FLOW = LoopStep("mfc", "flow", (10, 20))


def nested():
    """Loop current / Loop flow / W / Next flow / Next current, then A."""
    return [CURRENT, FLOW, W, NEXT, NEXT, A]


def test_outline_round_trips_nested_loops():
    steps = (LoopStep("supply", "current_setpoint", (1, 2), steps=(
        B, LoopStep("mfc", "flow", (10, 20), steps=(W,)), RepeatStep(2, steps=(A,)))), A)
    rows = to_outline(steps)
    assert rows == [CURRENT, B, FLOW, W, NEXT, RepeatStep(2), A, NEXT, NEXT, A]
    assert from_outline(rows) == steps
    assert depths(rows) == [0, 1, 1, 2, 1, 1, 2, 1, 0, 0]


def test_next_closes_the_nearest_open_loop():
    rows = nested()
    assert partner(rows, 3) == 1 and partner(rows, 4) == 0
    assert partner(rows, 0) == 4


def test_unbalanced_outline_is_refused():
    with pytest.raises(OutlineError):
        from_outline([CURRENT, W])
    with pytest.raises(OutlineError):
        from_outline([W, NEXT])


def test_step_moves_into_and_out_of_loops_one_boundary_at_a_time():
    rows = [A, CURRENT, W, NEXT]
    rows, i = move(rows, 0, 1)          # past Loop row: now first step inside
    assert rows == [CURRENT, A, W, NEXT] and i == 1 and depths(rows)[i] == 1
    rows, i = move(rows, 1, 1)
    rows, i = move(rows, 2, 1)          # past Next row: back out
    assert rows == [CURRENT, W, NEXT, A] and i == 3 and depths(rows)[i] == 0


def test_loop_row_moves_its_whole_loop():
    rows = [A, FLOW, W, NEXT]
    rows, i = move(rows, 1, -1)
    assert rows == [FLOW, W, NEXT, A] and i == 0
    # Moving a loop down past another loop's row nests it inside.
    rows = [FLOW, W, NEXT, CURRENT, B, NEXT]
    rows, i = move(rows, 0, 1)
    assert rows == [CURRENT, FLOW, W, NEXT, B, NEXT] and i == 1
    assert from_outline(rows)[0].steps[0].steps == (W,)


def test_next_row_changes_what_the_loop_covers():
    rows = [FLOW, W, NEXT, A]
    rows, i = move(rows, 2, 1)          # take A in
    assert rows == [FLOW, W, A, NEXT] and i == 3
    rows, i = move(rows, 3, -1)         # leave A out again
    assert rows == [FLOW, W, NEXT, A] and i == 2


def test_next_row_takes_in_or_leaves_out_a_whole_inner_loop():
    rows = [CURRENT, W, NEXT, FLOW, B, NEXT]
    rows, i = move(rows, 2, 1)
    assert rows == [CURRENT, W, FLOW, B, NEXT, NEXT] and i == 5
    rows, i = move(rows, 5, -1)
    assert rows == [CURRENT, W, NEXT, FLOW, B, NEXT] and i == 2


def test_impossible_next_moves_explain_why():
    with pytest.raises(OutlineError, match="outer Next"):
        move(nested(), 3, 1)
    with pytest.raises(OutlineError, match="at least one step"):
        move([FLOW, NEXT], 1, -1)
    with pytest.raises(OutlineError, match="start"):
        move([A], 0, -1)


def test_every_move_keeps_the_outline_valid():
    rows = [A, CURRENT, B, FLOW, W, NEXT, RepeatStep(2), A, NEXT, NEXT, W]
    for index in range(len(rows)):
        for direction in (-1, 1):
            try:
                moved, _ = move(rows, index, direction)
            except OutlineError:
                continue
            from_outline(moved)
            assert sorted(map(repr, moved)) == sorted(map(repr, rows))


def test_insert_delete_duplicate():
    rows, i = insert([A], 0, FLOW)
    assert rows == [A, FLOW, NEXT] and i == 1
    rows, i = insert(rows, 1, W)       # after a Loop row = first step inside it
    assert rows == [A, FLOW, W, NEXT] and depths(rows)[i] == 1
    assert delete(rows, 3) == [A, W]   # deleting via Next removes the loop, keeps W
    rows, i = duplicate(rows, 1)
    assert rows == [A, FLOW, W, NEXT, FLOW, W, NEXT] and i == 4
