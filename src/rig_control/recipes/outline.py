"""The flat ``Loop … Next`` view of a recipe that the builder edits.

A recipe is a tree, but people edit it as a list where each loop is opened
by its own row and closed by a ``Next`` row, like::

    Loop current setpoint: 1, 2, 4 A
        Loop flow: 10, 20 sccm
            Wait 60 s
        Next flow
    Next current setpoint

In the outline, loop rows are ``LoopStep``/``RepeatStep`` with no steps of
their own, and ``LoopEnd`` is the ``Next`` row.  A ``Next`` always belongs to
the nearest open loop above it, so the innermost loop finishes all of its
values before the loop outside it moves on.

Every edit below keeps the outline balanced, so it can always be turned
back into a recipe.
"""
from dataclasses import dataclass, replace
from typing import TypeAlias

from rig_control.recipes.model import LoopStep, RecipeStep, RepeatStep, SetStep, WaitStep


@dataclass(frozen=True, slots=True)
class LoopEnd:
    """The ``Next`` row that closes the nearest open loop above it."""


OutlineRow: TypeAlias = SetStep | WaitStep | LoopStep | RepeatStep | LoopEnd
LoopRow: TypeAlias = LoopStep | RepeatStep


class OutlineError(ValueError):
    pass


def is_loop(row: OutlineRow) -> bool:
    return isinstance(row, (LoopStep, RepeatStep))


def _header(row: LoopRow) -> LoopRow:
    return replace(row, steps=())


def to_outline(steps: tuple[RecipeStep, ...]) -> list[OutlineRow]:
    rows: list[OutlineRow] = []
    for step in steps:
        if isinstance(step, (LoopStep, RepeatStep)):
            rows.append(_header(step))
            rows.extend(to_outline(step.steps))
            rows.append(LoopEnd())
        else:
            rows.append(step)
    return rows


def from_outline(rows: list[OutlineRow]) -> tuple[RecipeStep, ...]:
    stack: list[tuple[LoopRow, list[RecipeStep]]] = []
    current: list[RecipeStep] = []
    for row in rows:
        if is_loop(row):
            stack.append((row, current))  # type: ignore[arg-type]
            current = []
        elif isinstance(row, LoopEnd):
            if not stack:
                raise OutlineError("A Next row has no loop to close")
            header, parent = stack.pop()
            parent.append(replace(header, steps=tuple(current)))
            current = parent
        else:
            current.append(row)
    if stack:
        raise OutlineError("A loop has no Next row")
    return tuple(current)


def depths(rows: list[OutlineRow]) -> list[int]:
    """Indent level of each row; a loop row and its Next share a level."""
    result: list[int] = []
    depth = 0
    for row in rows:
        if isinstance(row, LoopEnd):
            depth = max(depth - 1, 0)
        result.append(depth)
        if is_loop(row):
            depth += 1
    return result


def partner(rows: list[OutlineRow], index: int) -> int:
    """The Next row for a loop row, or the loop row for a Next row."""
    step = 1 if is_loop(rows[index]) else -1
    if step == -1 and not isinstance(rows[index], LoopEnd):
        raise ValueError("Only loop and Next rows have partners")
    level = 0
    i = index
    while True:
        i += step
        row = rows[i]
        opens = is_loop(row) if step == 1 else isinstance(row, LoopEnd)
        closes = isinstance(row, LoopEnd) if step == 1 else is_loop(row)
        if opens:
            level += 1
        elif closes:
            if level == 0:
                return i
            level -= 1


def _span(rows: list[OutlineRow], index: int) -> tuple[int, int]:
    """Rows that move together: a whole loop for a loop row, else one row."""
    return (index, partner(rows, index)) if is_loop(rows[index]) else (index, index)


def insert(rows: list[OutlineRow], after: int | None, row: SetStep | WaitStep | LoopRow) -> tuple[list[OutlineRow], int]:
    """Insert after row ``after`` (or at the end); a new loop gets its Next."""
    at = len(rows) if after is None else after + 1
    new = [row, LoopEnd()] if is_loop(row) else [row]
    return rows[:at] + new + rows[at:], at


def delete(rows: list[OutlineRow], index: int) -> list[OutlineRow]:
    """Delete one step, or a loop's two rows while keeping its contents."""
    if isinstance(rows[index], LoopEnd):
        index = partner(rows, index)
    if is_loop(rows[index]):
        end = partner(rows, index)
        return rows[:index] + rows[index + 1:end] + rows[end + 1:]
    return rows[:index] + rows[index + 1:]


def duplicate(rows: list[OutlineRow], index: int) -> tuple[list[OutlineRow], int]:
    """Copy a step, or a whole loop with its contents, directly below it."""
    if isinstance(rows[index], LoopEnd):
        index = partner(rows, index)
    first, last = _span(rows, index)
    return rows[:last + 1] + rows[first:last + 1] + rows[last + 1:], last + 1


def move(rows: list[OutlineRow], index: int, direction: int) -> tuple[list[OutlineRow], int]:
    """Move row ``index`` one place up (-1) or down (+1).

    - A Set or Wait row moves past one row.  Passing a Loop row takes it into
      that loop; passing a Next row takes it out.
    - A Loop row carries its whole loop with it, moving in and out of other
      loops the same way.
    - A Next row changes where its loop ends.  It passes a Set or Wait row, or
      a whole inner loop, but cannot pass the outer loop's Next or its own
      loop row.

    Returns the new outline and the new index of the moved row.  An
    impossible move raises ``OutlineError`` with a reason for the user.
    """
    if direction not in (-1, 1):
        raise ValueError("direction must be -1 or 1")
    if isinstance(rows[index], LoopEnd):
        return _move_next(rows, index, direction)
    first, last = _span(rows, index)
    neighbour = last + 1 if direction == 1 else first - 1
    if not 0 <= neighbour < len(rows):
        raise OutlineError("Already at the " + ("end" if direction == 1 else "start"))
    block = rows[first:last + 1]
    if direction == 1:
        result = rows[:first] + [rows[neighbour]] + block + rows[neighbour + 1:]
        return result, first + 1
    result = rows[:neighbour] + block + [rows[neighbour]] + rows[last + 1:]
    return result, neighbour


def _move_next(rows: list[OutlineRow], index: int, direction: int) -> tuple[list[OutlineRow], int]:
    neighbour = index + direction
    if not 0 <= neighbour < len(rows):
        raise OutlineError("Already at the " + ("end" if direction == 1 else "start"))
    other = rows[neighbour]
    if direction == 1:
        if isinstance(other, LoopEnd):
            raise OutlineError("This loop cannot end after the loop outside it; move the outer Next first")
        target = partner(rows, neighbour) if is_loop(other) else neighbour
        result = rows[:index] + rows[index + 1:target + 1] + [rows[index]] + rows[target + 1:]
        return result, target
    if is_loop(other):
        raise OutlineError("A loop needs at least one step; delete the loop instead")
    target = partner(rows, neighbour) if isinstance(other, LoopEnd) else neighbour
    result = rows[:target] + [rows[index]] + rows[target:index] + rows[index + 1:]
    return result, target
