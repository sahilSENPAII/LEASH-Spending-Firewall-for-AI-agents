import pytest

from app.state import TERMINAL, IllegalTransition, PaymentStatus, can_transition, transition

S = PaymentStatus


@pytest.mark.parametrize(
    "current,target",
    [
        (S.REQUESTED, S.AUTHORIZED),
        (S.REQUESTED, S.BLOCKED),
        (S.REQUESTED, S.AWAITING_APPROVAL),
        (S.AWAITING_APPROVAL, S.AUTHORIZED),
        (S.AWAITING_APPROVAL, S.DENIED),
        (S.AWAITING_APPROVAL, S.EXPIRED),
        (S.AUTHORIZED, S.CAPTURED),
        (S.AUTHORIZED, S.VOIDED),
    ],
)
def test_legal_moves(current, target):
    assert transition(current, target) == target


@pytest.mark.parametrize(
    "current,target",
    [
        (S.REQUESTED, S.CAPTURED),  # can't take money that was never held
        (S.BLOCKED, S.AUTHORIZED),  # a blocked payment can't be revived
        (S.CAPTURED, S.VOIDED),  # can't release money already taken
        (S.DENIED, S.AUTHORIZED),
        (S.AWAITING_APPROVAL, S.CAPTURED),
    ],
)
def test_illegal_moves_raise(current, target):
    with pytest.raises(IllegalTransition):
        transition(current, target)


def test_terminal_states_have_no_exits():
    for status in TERMINAL:
        assert not any(can_transition(status, t) for t in PaymentStatus)
