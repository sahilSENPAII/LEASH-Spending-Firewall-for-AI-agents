"""Anomaly model: how unusual a payment looks for its mandate (docs/CONTRACTS.md section 4).

`score_anomaly(features)` returns a score from 0 (normal) to 1 (anomalous) and the top reasons in
plain words. The model is XGBoost, trained on SYNTHETIC data (scripts/train_anomaly.py); no real
agent fraud data exists. Reasons come from per-feature SHAP values, which XGBoost computes itself
(`pred_contribs`), so the backend needs no `shap` package.

The model only adds signal after the policy said ALLOW; it can make a decision stricter, never
looser (D2). If scoring fails, the orchestrator uses STEP_UP (D3).
"""

import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from functools import lru_cache
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import BudgetHold, MandateRow, Payment
from app.state import PaymentStatus
from app.times import as_utc, require_utc

MODEL_PATH = Path(__file__).with_name("anomaly_model.json")

# Column order the model was trained with. Change it only together with a retrain.
FEATURES = (
    "amount",
    "amount_to_max_ratio",
    "budget_used_ratio",
    "payment_count",
    "recent_payment_count",
    "seconds_since_last_payment",
    "merchant_seen_before",
    "hour_of_day_utc",
)
# Payments that hold or took money; the same rule as the velocity count in check_and_reserve.
_LIVE = (
    PaymentStatus.REQUESTED,
    PaymentStatus.AWAITING_APPROVAL,
    PaymentStatus.AUTHORIZED,
    PaymentStatus.CAPTURED,
)
_TAKEN = (PaymentStatus.AUTHORIZED, PaymentStatus.CAPTURED)


@dataclass(frozen=True)
class AnomalyFeatures:
    amount: Decimal
    amount_to_max_ratio: float  # amount / mandate.max_payment_amount
    budget_used_ratio: float  # budget_used / total_budget, before this payment
    payment_count: int  # payments already counted against the mandate, before this one
    recent_payment_count: int  # other live payments in the velocity window
    seconds_since_last_payment: float | None  # None: no earlier payment under this mandate
    merchant_seen_before: bool  # this mandate paid this merchant before
    hour_of_day_utc: int


@dataclass(frozen=True)
class AnomalyResult:
    score: float
    reasons: tuple[str, ...]
    model_version: str


def feature_row(f: AnomalyFeatures) -> list[float]:
    """Model input in FEATURES order. Raises ValueError for values the model was never shown."""
    if not isinstance(f, AnomalyFeatures):
        raise TypeError("features must be AnomalyFeatures")
    if not isinstance(f.amount, Decimal) or not f.amount.is_finite() or f.amount <= 0:
        raise ValueError("amount must be a positive finite Decimal")
    for name in ("amount_to_max_ratio", "budget_used_ratio"):
        value = getattr(f, name)
        if not isinstance(value, int | float) or not math.isfinite(value) or value < 0:
            raise ValueError(f"{name} must be a finite number >= 0")
    for name in ("payment_count", "recent_payment_count"):
        value = getattr(f, name)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError(f"{name} must be an integer >= 0")
    seconds = f.seconds_since_last_payment
    if seconds is not None and (not math.isfinite(seconds) or seconds < 0):
        raise ValueError("seconds_since_last_payment must be None or a finite number >= 0")
    if not isinstance(f.merchant_seen_before, bool):
        raise ValueError("merchant_seen_before must be a bool")
    if not isinstance(f.hour_of_day_utc, int) or not 0 <= f.hour_of_day_utc <= 23:
        raise ValueError("hour_of_day_utc must be an integer 0-23")
    return [
        float(f.amount),
        float(f.amount_to_max_ratio),
        float(f.budget_used_ratio),
        float(f.payment_count),
        float(f.recent_payment_count),
        math.nan if seconds is None else float(seconds),  # XGBoost treats NaN as "missing"
        1.0 if f.merchant_seen_before else 0.0,
        float(f.hour_of_day_utc),
    ]


@lru_cache(maxsize=2)
def load_model(path: str = str(MODEL_PATH)):
    """The trained booster, loaded once. Raises if the file is missing or unreadable."""
    import xgboost

    booster = xgboost.Booster()
    booster.load_model(path)
    if tuple((booster.attr("features") or "").split(",")) != FEATURES:
        raise ValueError(f"{path} was trained on different features; retrain it")
    return booster


def score_anomaly(features: AnomalyFeatures, *, model_path: Path = MODEL_PATH) -> AnomalyResult:
    """Score one payment. Raises on bad input or a missing model; the caller then uses STEP_UP."""
    import numpy
    import xgboost

    row = feature_row(features)
    booster = load_model(str(model_path))
    matrix = xgboost.DMatrix(numpy.array([row], dtype=float), missing=math.nan,
                             feature_names=list(FEATURES))
    score = float(booster.predict(matrix)[0])
    contributions = booster.predict(matrix, pred_contribs=True)[0][: len(FEATURES)]
    pushing_up = sorted(
        ((c, name) for c, name in zip(contributions, FEATURES, strict=True) if c > 0),
        reverse=True,
    )[:3]
    reasons = tuple(_reason(name, features) for _, name in pushing_up)
    return AnomalyResult(score=score, reasons=reasons,
                         model_version=booster.attr("version") or "unknown")


def _reason(name: str, f: AnomalyFeatures) -> str:
    seconds = f.seconds_since_last_payment
    return {
        "amount": f"amount ${f.amount}",
        "amount_to_max_ratio": f"amount is {f.amount_to_max_ratio:.0%} of the per-payment limit",
        "budget_used_ratio": f"{f.budget_used_ratio:.0%} of the budget was already used",
        "payment_count": f"{f.payment_count} earlier payments under this mandate",
        "recent_payment_count": f"{f.recent_payment_count} other payments in the last few minutes",
        "seconds_since_last_payment": (
            "first payment under this mandate"
            if seconds is None
            else f"only {seconds:.0f}s since the previous payment"
        ),
        "merchant_seen_before": (
            "repeat merchant" if f.merchant_seen_before else "first payment to this merchant"
        ),
        "hour_of_day_utc": f"unusual time ({f.hour_of_day_utc:02d}:00 UTC)",
    }[name]


async def build_features(
    session: AsyncSession,
    *,
    payment_id: str,
    mandate_id: str,
    merchant_id: str,
    amount: Decimal,
    now: datetime,
    window_seconds: int,
) -> AnomalyFeatures:
    """Features for a payment that check_and_reserve already allowed (its own hold excluded).

    Raises LookupError if the mandate does not exist.
    """
    now = require_utc(now)
    row = await session.get(MandateRow, mandate_id)
    if row is None:
        raise LookupError(f"mandate {mandate_id!r} not found")
    own_hold = await session.get(BudgetHold, payment_id)
    held_now = own_hold is not None and own_hold.released_at is None
    budget_used = row.budget_used - (own_hold.amount if held_now else Decimal("0"))
    payment_count = row.payment_count - (1 if held_now else 0)

    others = (Payment.mandate_id == mandate_id) & (Payment.id != payment_id)
    recent = await session.scalar(
        select(func.count()).where(
            others,
            Payment.status.in_(_LIVE),
            Payment.created_at >= now - timedelta(seconds=window_seconds),
        )
    )
    last = await session.scalar(
        select(func.max(Payment.created_at)).where(others, Payment.status.in_(_LIVE))
    )
    seen = await session.scalar(
        select(func.count()).where(
            others, Payment.merchant_id == merchant_id, Payment.status.in_(_TAKEN)
        )
    )
    last = as_utc(last)
    return AnomalyFeatures(
        amount=amount,
        amount_to_max_ratio=float(amount / row.max_payment_amount),
        budget_used_ratio=float(budget_used / row.total_budget),
        payment_count=max(payment_count, 0),
        recent_payment_count=recent,
        seconds_since_last_payment=None if last is None else max((now - last).total_seconds(), 0),
        merchant_seen_before=seen > 0,
        hour_of_day_utc=now.hour,
    )
