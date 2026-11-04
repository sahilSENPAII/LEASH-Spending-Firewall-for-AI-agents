"""Generate SYNTHETIC payments and train the anomaly model into app/anomaly_model.json.

No real data exists for hijacked shopping agents, so the data is generated from simple, written-down
assumptions (below) and the README says so. Deterministic: the same seed gives the same model.

cd backend && uv run python -m scripts.train_anomaly
"""

import argparse
import hashlib
import math
import random
from dataclasses import dataclass

from app.anomaly import FEATURES, MODEL_PATH

LIMITS = (50, 100, 200, 500)  # typical per-payment limits on a mandate


@dataclass(frozen=True)
class Sample:
    row: list[float]
    label: int  # 1 = looks like a hijacked agent


NIGHT_UTC = (18, 19, 20, 21, 22, 23, 0, 1)  # 23:30-07:30 IST


def _hour(rng: random.Random, usual: tuple[int, ...], share: float) -> int:
    # Time of day is only a weak hint: people also shop at night, and attacks also run by day.
    return rng.choice(usual) if rng.random() < share else rng.randint(0, 23)


def _normal(rng: random.Random) -> list[float]:
    """A user's own agent: few, spaced-out payments, any amount up to the limit, mostly daytime.

    Buying close to the limit is normal (people set the limit just above the price they expect),
    and so is a first purchase at a new shop or a late-night purchase.
    """
    limit = rng.choice(LIMITS)
    ratio = min(rng.betavariate(2, 3), 1.0) if rng.random() < 0.5 else rng.uniform(0.55, 1.0)
    count = rng.choice((0, 0, 1, 1, 2, 3))
    seconds = math.nan if count == 0 else rng.uniform(3_600, 14 * 86_400)
    return [
        round(ratio * limit, 2),
        ratio,
        rng.uniform(0, 0.7) if count else 0.0,
        count,
        0 if rng.random() < 0.9 else 1,
        seconds,
        1.0 if count and rng.random() < 0.5 else 0.0,
        _hour(rng, tuple(range(2, 18)), 0.75),  # mostly 07:30-23:30 IST
    ]


def _hijacked(rng: random.Random) -> list[float]:
    """A tricked agent: bursts of payments seconds apart, new shops, budget nearly gone.

    These are the real signals. Amount near the limit and night time only lean this way.
    """
    limit = rng.choice(LIMITS)
    ratio = rng.uniform(0.75, 1.0)
    count = rng.randint(1, 6)
    return [
        round(ratio * limit, 2),
        ratio,
        rng.uniform(0.5, 0.98),
        count,
        rng.randint(1, 6),
        rng.uniform(2, 180) if rng.random() < 0.8 else rng.uniform(180, 900),
        1.0 if rng.random() < 0.15 else 0.0,
        _hour(rng, NIGHT_UTC, 0.5),
    ]


def _blend(a: list[float], b: list[float], rng: random.Random, k: int) -> list[float]:
    """Copy k random features from b into a, so the two classes overlap like real data would."""
    out = list(a)
    for i in rng.sample(range(len(out)), k):
        out[i] = b[i]
    return out


def make_samples(n: int, seed: int, anomaly_share: float = 0.2) -> list[Sample]:
    rng = random.Random(seed)
    samples = []
    for _ in range(n):
        if rng.random() < anomaly_share:
            row = _hijacked(rng)
            if rng.random() < 0.15:  # some attacks look mostly normal
                row = _blend(_normal(rng), row, rng, 2)
            samples.append(Sample(row, 1))
        else:
            row = _normal(rng)
            if rng.random() < 0.1:  # some normal payments have one odd trait
                row = _blend(row, _hijacked(rng), rng, 1)
            samples.append(Sample(row, 0))
    return samples


def train(samples: list[Sample], rounds: int, seed: int):
    import numpy
    import xgboost

    rng = random.Random(seed)
    order = list(range(len(samples)))
    rng.shuffle(order)
    cut = int(len(order) * 0.8)

    def matrix(idx):
        x = numpy.array([samples[i].row for i in idx], dtype=float)
        y = numpy.array([samples[i].label for i in idx], dtype=float)
        return xgboost.DMatrix(x, label=y, missing=math.nan, feature_names=list(FEATURES))

    dtrain, dtest = matrix(order[:cut]), matrix(order[cut:])
    params = {
        "objective": "binary:logistic",
        "eval_metric": "auc",
        "max_depth": 3,
        "eta": 0.1,
        "subsample": 0.9,
        "seed": seed,
        "nthread": 1,  # same result on every machine
    }
    booster = xgboost.train(params, dtrain, num_boost_round=rounds)
    scores = booster.predict(dtest)
    labels = dtest.get_label()
    return booster, evaluate(scores, labels)


def evaluate(scores, labels) -> dict:
    pairs = sorted(zip(scores, labels, strict=True), key=lambda p: p[0])
    pos = sum(1 for _, y in pairs if y == 1)
    neg = len(pairs) - pos
    rank_sum = sum(i + 1 for i, (_, y) in enumerate(pairs) if y == 1)
    auc = (rank_sum - pos * (pos + 1) / 2) / (pos * neg)
    out = {"auc": round(auc, 4)}
    for t in (0.5, 0.9):
        flagged = [(s >= t, y) for s, y in pairs]
        tp = sum(1 for f, y in flagged if f and y == 1)
        fp = sum(1 for f, y in flagged if f and y == 0)
        out[f"recall@{t}"] = round(tp / pos, 4)
        out[f"false_alarm_rate@{t}"] = round(fp / neg, 4)
    return out


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=int, default=20_000)
    parser.add_argument("--rounds", type=int, default=150)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--out", default=str(MODEL_PATH))
    args = parser.parse_args(argv)

    samples = make_samples(args.samples, args.seed)
    booster, metrics = train(samples, args.rounds, args.seed)
    tag = f"synthetic-v2-seed{args.seed}-n{args.samples}-r{args.rounds}"
    booster.set_attr(features=",".join(FEATURES), version=tag, data="synthetic",
                     **{k: str(v) for k, v in metrics.items()})
    booster.save_model(args.out)
    digest = hashlib.sha256(open(args.out, "rb").read()).hexdigest()[:12]
    print(f"model   {args.out}  ({tag}, sha256 {digest})")
    for k, v in metrics.items():
        print(f"metric  {k} = {v}")


if __name__ == "__main__":
    main()
