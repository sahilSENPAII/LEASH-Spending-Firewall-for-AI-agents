"""Payment state machine. Every status change must go through `transition`."""

from enum import StrEnum


class PaymentStatus(StrEnum):
    REQUESTED = "REQUESTED"  # agent asked; checks running
    BLOCKED = "BLOCKED"  # a check said no; nothing touched PayPal
    AWAITING_APPROVAL = "AWAITING_APPROVAL"  # STEP_UP: waiting for the human
    DENIED = "DENIED"  # human said no
    EXPIRED = "EXPIRED"  # human never answered
    AUTHORIZED = "AUTHORIZED"  # money held at PayPal
    CAPTURED = "CAPTURED"  # money taken (final)
    VOIDED = "VOIDED"  # hold released (saga compensation)
    FAILED = "FAILED"  # PayPal error before a hold existed


TERMINAL = {
    PaymentStatus.BLOCKED,
    PaymentStatus.DENIED,
    PaymentStatus.EXPIRED,
    PaymentStatus.CAPTURED,
    PaymentStatus.VOIDED,
    PaymentStatus.FAILED,
}

ALLOWED: dict[PaymentStatus, set[PaymentStatus]] = {
    PaymentStatus.REQUESTED: {
        PaymentStatus.BLOCKED,
        PaymentStatus.AWAITING_APPROVAL,
        PaymentStatus.AUTHORIZED,
        PaymentStatus.FAILED,
    },
    PaymentStatus.AWAITING_APPROVAL: {
        PaymentStatus.AUTHORIZED,
        PaymentStatus.DENIED,
        PaymentStatus.EXPIRED,
        PaymentStatus.FAILED,
    },
    PaymentStatus.AUTHORIZED: {PaymentStatus.CAPTURED, PaymentStatus.VOIDED},
}


class IllegalTransition(Exception):
    def __init__(self, current: PaymentStatus, target: PaymentStatus):
        super().__init__(f"illegal payment transition {current} -> {target}")
        self.current = current
        self.target = target


def can_transition(current: PaymentStatus, target: PaymentStatus) -> bool:
    return target in ALLOWED.get(current, set())


def transition(current: PaymentStatus, target: PaymentStatus) -> PaymentStatus:
    """Return `target` if the move is legal, else raise IllegalTransition."""
    if not can_transition(current, target):
        raise IllegalTransition(current, target)
    return target
