import math
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import numpy
import pytest
import xgboost

from app import anomaly, mandates
from app.anomaly import (
    FEATURES,
    MODEL_PATH,
    AnomalyFeatures,
    build_features,
    feature_row,
    load_model,
    score_anomaly,
)
from app.config import Settings
from app.mandates import MandateTerms, check_and_reserve, create_mandate, generate_signing_key
from app.models import Payment
from app.policy import PaymentRequest
from app.state import PaymentStatus
from scripts.train_anomaly import evaluate, make_samples, train

# The sync-1 demo purchase: first payment, $179 of a $200 limit, afternoon IST.
DEMO = AnomalyFeatures(
    amount=Decimal("179.00"),
    amount_to_max_ratio=0.895,
    budget_used_ratio=0.0,
    payment_count=0,
    recent_payment_count=0,
    seconds_since_last_payment=None,
    merchant_seen_before=False,
    hour_of_day_utc=9,
)
# A hijacked agent: 4th payment in under a minute, new shop, near the limit, late night IST.
BURST = AnomalyFeatures(
    amount=Decimal("190.00"),
    amount_to_max_ratio=0.95,
    budget_used_ratio=0.8,
    payment_count=3,
    recent_payment_count=3,
    seconds_since_last_payment=40.0,
    merchant_seen_before=False,
    hour_of_day_utc=21,
)


# --- the committed model ------------------------------------------------------


def test_committed_model_is_synthetic_and_matches_the_features():
    booster = load_model()
    assert booster.attr("data") == "synthetic"
    assert booster.attr("features") == ",".join(FEATURES)
    assert booster.attr("version").startswith("synthetic-v1-")
    assert float(booster.attr("auc")) > 0.95


def test_demo_purchase_scores_low():
    result = score_anomaly(DEMO)
    assert 0 <= result.score < 0.3
    assert result.model_version == load_model().attr("version")


def test_burst_scores_high_with_readable_reasons():
    result = score_anomaly(BURST)
    assert result.score > 0.9
    assert 1 <= len(result.reasons) <= 3
    assert all(isinstance(r, str) and r for r in result.reasons)
    assert "only 40s since the previous payment" in result.reasons


def test_same_input_same_output():
    assert score_anomaly(BURST) == score_anomaly(BURST)


def test_reasons_are_the_features_pushing_the_score_up():
    # SHAP values from XGBoost: bias + per-feature contributions add up to the raw margin.
    booster = load_model()
    matrix = xgboost.DMatrix(numpy.array([feature_row(BURST)]), missing=math.nan,
                             feature_names=list(FEATURES))
    contribs = booster.predict(matrix, pred_contribs=True)[0]
    margin = booster.predict(matrix, output_margin=True)[0]
    assert math.isclose(float(contribs.sum()), float(margin), rel_tol=1e-4, abs_tol=1e-4)
    top = sorted(zip(contribs[:-1], FEATURES, strict=True), reverse=True)[0][1]
    assert anomaly._reason(top, BURST) == score_anomaly(BURST).reasons[0]


def test_unknown_first_payment_is_missing_not_zero():
    assert math.isnan(feature_row(DEMO)[FEATURES.index("seconds_since_last_payment")])


@pytest.mark.parametrize(
    "change",
    [
        {"amount": Decimal("0")},
        {"amount": Decimal("NaN")},
        {"amount": 179.0},
        {"amount_to_max_ratio": -0.1},
        {"budget_used_ratio": math.inf},
        {"payment_count": -1},
        {"recent_payment_count": True},
        {"seconds_since_last_payment": -5.0},
        {"merchant_seen_before": 1},
        {"hour_of_day_utc": 24},
    ],
)
def test_bad_features_are_refused(change):
    with pytest.raises(ValueError):
        score_anomaly(replace(DEMO, **change))


def test_missing_model_raises_so_the_caller_steps_up(tmp_path):
    with pytest.raises(Exception):  # noqa: B017 - any error is a STEP_UP for the orchestrator
        score_anomaly(DEMO, model_path=tmp_path / "missing.json")


def test_model_trained_on_other_features_is_refused(tmp_path):
    x = numpy.array([[1.0, 2.0], [3.0, 4.0]])
    booster = xgboost.train({"objective": "binary:logistic"},
                            xgboost.DMatrix(x, label=[0, 1]), num_boost_round=1)
    booster.set_attr(features="a,b", version="other")
    path = tmp_path / "other.json"
    booster.save_model(str(path))
    with pytest.raises(ValueError, match="different features"):
        score_anomaly(DEMO, model_path=path)


# --- synthetic data and training -----------------------------------------------


def test_synthetic_data_is_deterministic_and_mixed():
    a, b = make_samples(1000, seed=3), make_samples(1000, seed=3)
    same = lambda x, y: x == y or (math.isnan(x) and math.isnan(y))  # noqa: E731
    assert all(
        sa.label == sb.label and all(map(same, sa.row, sb.row))
        for sa, sb in zip(a, b, strict=True)
    )
    assert any(math.isnan(s.row[FEATURES.index("seconds_since_last_payment")]) for s in a)
    share = sum(s.label for s in a) / len(a)
    assert 0.15 < share < 0.25
    assert all(len(s.row) == len(FEATURES) for s in a)


def test_training_learns_the_pattern_and_round_trips(tmp_path):
    booster, metrics = train(make_samples(3000, seed=11), rounds=40, seed=11)
    assert metrics["auc"] > 0.95
    booster.set_attr(features=",".join(FEATURES), version="test-model")
    path = tmp_path / "model.json"
    booster.save_model(str(path))
    result = score_anomaly(BURST, model_path=path)
    assert result.model_version == "test-model" and result.score > 0.5


def test_evaluate_on_a_known_case():
    metrics = evaluate([0.1, 0.4, 0.6, 0.95], [0, 0, 1, 1])
    assert metrics["auc"] == 1.0
    assert metrics["recall@0.5"] == 1.0 and metrics["false_alarm_rate@0.5"] == 0.0
    assert metrics["recall@0.9"] == 0.5


# --- building features from the database ---------------------------------------

NOW = datetime(2026, 11, 4, 9, 30, tzinfo=UTC)


@pytest.fixture
def signed(monkeypatch):
    settings = Settings(mandate_signing_key=generate_signing_key(), velocity_max_payments=5,
                        velocity_window_seconds=600)
    monkeypatch.setattr(mandates, "get_settings", lambda: settings)
    return settings


async def _payment(s, merchant, status, created_at, amount="10.00", mandate_id="m_demo"):
    p = Payment(mandate_id=mandate_id, merchant_id=merchant, amount=Decimal(amount),
                status=status, created_at=created_at)
    s.add(p)
    await s.flush()
    return p


async def test_features_from_the_database_exclude_the_payment_itself(sessions, signed):
    async with sessions() as s:
        await create_mandate(s, MandateTerms(
            mandate_id="m_demo", intent="headphones", category="electronics",
            max_payment_amount=Decimal("200.00"), total_budget=Decimal("500.00"),
            max_payment_count=5, allowed_merchant_ids=frozenset({"soundhub", "megadeals"}),
            expires_at=NOW + timedelta(days=30), created_at=NOW - timedelta(days=1)))
        earlier = await _payment(s, "soundhub", PaymentStatus.REQUESTED, NOW - timedelta(days=2))
        await check_and_reserve(s, payment_id=earlier.id, mandate_id="m_demo",
                                request=PaymentRequest(Decimal("10.00"), "USD", "electronics",
                                                       "soundhub"), now=NOW - timedelta(days=2))
        earlier.status = PaymentStatus.CAPTURED
        await _payment(s, "soundhub", PaymentStatus.BLOCKED, NOW - timedelta(seconds=30))
        await _payment(s, "megadeals", PaymentStatus.CAPTURED, NOW, mandate_id="m_other")
        current = await _payment(s, "soundhub", PaymentStatus.REQUESTED, NOW, amount="150.00")
        decision = await check_and_reserve(
            s, payment_id=current.id, mandate_id="m_demo",
            request=PaymentRequest(Decimal("150.00"), "USD", "electronics", "soundhub"), now=NOW)
        assert decision.is_allowed
        f = await build_features(s, payment_id=current.id, mandate_id="m_demo",
                                 merchant_id="soundhub", amount=Decimal("150.00"), now=NOW,
                                 window_seconds=600)
        other_shop = await build_features(s, payment_id=current.id, mandate_id="m_demo",
                                          merchant_id="megadeals", amount=Decimal("150.00"),
                                          now=NOW, window_seconds=600)

    assert f == AnomalyFeatures(
        amount=Decimal("150.00"),
        amount_to_max_ratio=0.75,
        budget_used_ratio=0.02,  # only the earlier $10, not this payment's own hold
        payment_count=1,
        recent_payment_count=0,  # the BLOCKED one 30s ago does not count
        seconds_since_last_payment=2 * 86_400.0,
        merchant_seen_before=True,
        hour_of_day_utc=9,
    )
    assert other_shop.merchant_seen_before is False  # m_other's megadeals payment is not ours
    assert 0 <= score_anomaly(f).score <= 1


async def test_features_for_an_unknown_mandate_raise(sessions):
    async with sessions() as s:
        with pytest.raises(LookupError):
            await build_features(s, payment_id="p", mandate_id="nope", merchant_id="soundhub",
                                 amount=Decimal("1"), now=NOW, window_seconds=600)


def test_model_file_is_next_to_the_code():
    assert MODEL_PATH.name == "anomaly_model.json" and MODEL_PATH.exists()
