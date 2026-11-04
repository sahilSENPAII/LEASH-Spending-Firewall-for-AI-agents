# Anomaly model

The model's code lives in the backend so the orchestrator can import it (D15):

- `backend/app/anomaly.py`: `score_anomaly`, `build_features`, the feature list.
- `backend/app/anomaly_model.json`: the trained XGBoost model (about 180 KB).
- `backend/scripts/train_anomaly.py`: generates **synthetic** payments and trains the model.

Retrain (deterministic: the same seed gives the same file), from `backend/`:
```bash
uv run python -m scripts.train_anomaly
```

The training data is synthetic: no real data exists for hijacked shopping agents. The scores show the
idea, not production accuracy (see `docs/STAGE1_REQUIREMENTS.md`, "ML data").
