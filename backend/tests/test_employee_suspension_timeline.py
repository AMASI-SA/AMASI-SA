"""Payroll state changes must preserve a chronological, half-open leave history."""
from copy import deepcopy
from datetime import date

import pytest

from employee_payroll_status import transition_suspensions


def change(periods, state, day):
    return transition_suspensions(
        periods, target_state=state, effective_date=date.fromisoformat(day),
        period_id="new", changed_at="2026-10-16T00:00:00+00:00", changed_by="owner",
    )


@pytest.mark.parametrize("state", ["inactive", "unpaid_leave"])
@pytest.mark.parametrize("day", ["2026-10-04", "2026-10-07", "2026-10-09"])
def test_new_leave_cannot_precede_previous_return(state, day):
    history = [{"started_on": "2026-10-05", "returned_on": "2026-10-10"}]
    original = deepcopy(history)
    with pytest.raises(ValueError, match="employee_payroll_transition_before_previous"):
        change(history, state, day)
    assert history == original


@pytest.mark.parametrize("state", ["inactive", "unpaid_leave"])
def test_unpaid_state_switch_cannot_precede_open_leave(state):
    with pytest.raises(ValueError, match="employee_payroll_transition_before_previous"):
        change([{"started_on": "2026-10-05", "returned_on": None}], state, "2026-10-04")


@pytest.mark.parametrize("history", [
    [{"started_on": "2026-10-05", "returned_on": "2026-10-10"},
     {"started_on": "2026-10-07", "returned_on": None}],
    [{"started_on": "2026-10-05", "returned_on": None},
     {"started_on": "2026-10-10", "returned_on": "2026-10-12"}],
])
def test_existing_overlap_cannot_be_silently_rewritten(history):
    with pytest.raises(ValueError, match="employee_payroll_suspension_overlap"):
        change(history, "active", "2026-10-16")


@pytest.mark.parametrize("history", [
    [{"started_on": "invalid", "returned_on": None}],
    [{"started_on": "2026-10-10", "returned_on": "2026-10-05"}],
    [{"started_on": "2026-10-05", "returned_on": "invalid"}],
    [None], {},
])
def test_invalid_history_cannot_be_discarded_when_changing_status(history):
    with pytest.raises(ValueError, match="employee_payroll_suspension_history_invalid"):
        change(history, "active", "2026-10-16")


def test_adjacent_leaves_and_state_switch_preserve_paid_return_boundary():
    history = [{"started_on": "2026-10-05", "returned_on": "2026-10-10"}]
    history = change(history, "unpaid_leave", "2026-10-10")
    history = change(history, "inactive", "2026-10-11")
    assert history[-1]["started_on"] == "2026-10-10"
    assert history[-1]["reason"] == "inactive"
    history = change(history, "active", "2026-10-12")
    assert history[-1]["returned_on"] == "2026-10-12"


def test_same_day_leave_and_return_remain_valid():
    history = change([], "unpaid_leave", "2026-10-10")
    assert change(history, "active", "2026-10-10")[-1]["returned_on"] == "2026-10-10"


def test_repeated_same_day_stop_and_undo_does_not_depend_on_random_ids():
    history = []
    for period_id in ("z-first", "m-second", "a-third"):
        history = transition_suspensions(
            history, target_state="inactive", effective_date=date(2026, 10, 17),
            period_id=period_id, changed_at="2026-10-16T00:00:00+00:00", changed_by="owner",
        )
        assert sum(row["returned_on"] is None for row in history) == 1
        history = change(history, "active", "2026-10-17")
    assert len(history) == 3
    assert all(row["started_on"] == row["returned_on"] == "2026-10-17" for row in history)


def test_same_start_nonempty_ranges_still_reject_overlap():
    with pytest.raises(ValueError, match="employee_payroll_suspension_overlap"):
        change([
            {"id": "z", "started_on": "2026-10-10", "returned_on": "2026-10-12"},
            {"id": "a", "started_on": "2026-10-10", "returned_on": None},
        ], "active", "2026-10-16")
