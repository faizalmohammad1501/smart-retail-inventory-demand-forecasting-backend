# Smart Retail Platform — ML Forecasting Pipeline

## Overview

The ML module implements a complete demand forecasting pipeline:
**Synthetic Data → Preprocessing → Feature Engineering → Model Training → Autoregressive Forecasting**

All steps are API-triggerable, making the pipeline fully observable and reproducible.

---

## Pipeline Stages

### Stage 1: Synthetic Data Generation

**Endpoint:** `POST /api/ml/pipeline/generate`  
**File:** `ml/datasets/dataset_generator.py`

Generates 7,300 rows (20 products × 365 days) of realistic retail demand data:

```python
Features generated:
  - product_id          (1–20)
  - date                (365 daily records per product)
  - demand              (base + seasonal + noise)
  - unit_price          (product-specific with minor variance)
  - supplier_rating     (correlated with delivery reliability)
  - is_weekend          (boolean)
  - month               (1–12)
  - day_of_week         (0–6)

Demand formula:
  base_demand    = product_mean_demand (10–100 units)
  seasonal_mult  = 1 + 0.3 * sin(2π * day_of_year / 365)
  weekend_mult   = 1.15 if is_weekend else 1.0
  promo_spike    = 1.5 on random 5% of days
  noise          = N(0, base_demand * 0.1)
  demand         = base_demand * seasonal_mult * weekend_mult * promo_spike + noise
```

Output: `ml/datasets/synthetic_retail_demand.csv`

---

### Stage 2: Data Validation

**File:** `ml/preprocessing/data_validator.py`

Checks performed:
- Required columns present (`product_id`, `date`, `demand`)
- `demand` values non-negative
- `date` column parseable as datetime
- No completely empty rows
- Duplicate (product_id, date) pairs flagged

---

### Stage 3: Data Cleaning

**File:** `ml/preprocessing/data_cleaner.py`

Operations:
- **Outlier capping:** IQR method — values below Q1 - 1.5×IQR or above Q3 + 1.5×IQR are clipped
- **Missing value imputation:** Forward-fill within each product group, then zero-fill
- **Date sorting:** Chronological order per product

---

### Stage 4: Feature Engineering

**File:** `ml/preprocessing/feature_engineer.py`

```
Input: cleaned demand time series (product_id, date, demand, ...)

Lag Features (capture autocorrelation):
  demand_lag_1   = demand from 1 day ago
  demand_lag_3   = demand from 3 days ago
  demand_lag_7   = demand from 7 days ago
  demand_lag_14  = demand from 14 days ago
  demand_lag_21  = demand from 21 days ago
  demand_lag_28  = demand from 28 days ago

Rolling Window Features (capture trends):
  demand_rolling_mean_7   = 7-day moving average
  demand_rolling_std_7    = 7-day rolling std deviation
  demand_rolling_mean_14  = 14-day moving average
  demand_rolling_std_14   = 14-day rolling std deviation
  demand_rolling_mean_30  = 30-day moving average
  demand_rolling_std_30   = 30-day rolling std deviation

Calendar Features (capture seasonality):
  day_of_week   (0=Monday, 6=Sunday)
  month         (1–12)
  quarter       (1–4)
  is_weekend    (0 or 1)
  day_of_year   (1–365)

Business Features:
  unit_price      (product pricing)
  supplier_rating (correlated with supply reliability)
  reorder_level   (demand context)

Total features: ~30
Target variable: demand (next day)
```

---

### Stage 5: Scaling

**File:** `ml/preprocessing/scaler_service.py`

- `StandardScaler` fitted **only on training set** to prevent data leakage
- Applied to train, validation, and test sets
- Scaler object persisted to `ml/saved_models/scaler.pkl`
- Inverse transform applied when returning forecasts in original units

---

### Stage 6: Dataset Splitting

**File:** `ml/preprocessing/dataset_builder.py`

```
Total records:  7,300 (after dropping lag warm-up rows ~6,700 usable)
Train split:    70%  → ~4,690 rows  → ml/datasets/train.csv
Validation split: 15% → ~1,005 rows  → ml/datasets/val.csv
Test split:     15%  → ~1,005 rows  → ml/datasets/test.csv

Split strategy: chronological (not random) to prevent future data leakage
  - All records up to day 256 → train
  - Days 256–311 → validation
  - Days 311–365 → test
```

---

### Stage 7: Model Training

**Endpoint:** `POST /api/predictions/train`  
**File:** `ml/training/model_trainer.py`

```python
from sklearn.ensemble import GradientBoostingRegressor

model = GradientBoostingRegressor(
    n_estimators=200,      # number of boosting stages
    learning_rate=0.05,    # shrinkage to prevent overfitting
    max_depth=4,           # tree depth (bias-variance trade-off)
    subsample=0.8,         # stochastic gradient boosting
    min_samples_split=5,
    random_state=42
)

model.fit(X_train, y_train)

# Evaluation on validation set
mae  = mean_absolute_error(y_val, model.predict(X_val))
rmse = sqrt(mean_squared_error(y_val, model.predict(X_val)))
r2   = r2_score(y_val, model.predict(X_val))
```

Persisted to:
- `ml/saved_models/model.pkl`
- `ml/saved_models/training_metadata.json` — contains MAE, RMSE, R², trained_at, feature_names

**Why GradientBoosting over LSTM?**
- Retail demand data is tabular (not long sequences)
- GBR achieves comparable accuracy with 10× faster training
- Feature importance is interpretable
- No GPU required
- Handles missing lag values gracefully

---

### Stage 8: Autoregressive Forecasting

**Endpoint:** `POST /api/predictions/forecast { product_id, days }`  
**File:** `ml/prediction/forecast_engine.py`

```
Algorithm: Autoregressive Multi-Step Forecasting

1. Load last 28 days of actual demand for product_id (seed window)
2. For each future day t = 1 to N:
   a. Build feature vector:
      - Lag features from current window (last 1,3,7,14,21,28 days)
      - Rolling stats from current window
      - Calendar features for target date
      - Static product features (price, rating, reorder_level)
   b. Scale feature vector using loaded scaler
   c. demand_t = model.predict(feature_vector)
   d. Append demand_t to window (slide window forward by 1)
   e. confidence_low  = demand_t × 0.85   (±15% band)
      confidence_high = demand_t × 1.15

3. Return:
   [{ date, predicted_demand, confidence_low, confidence_high }]

Fallback (if model not trained):
   Return 30-day rolling average of available historical demand
```

---

## Performance Benchmarks

| Metric | Typical Value |
|--------|--------------|
| MAE (units/day) | 3–8 |
| RMSE | 5–12 |
| R² | 0.75–0.92 |
| Training time | 5–15 seconds |
| Forecast generation (30 days) | < 1 second |
| Feature count | ~30 |

---

## API Workflow

```bash
# 1. Generate synthetic data
curl -X POST http://localhost:8000/api/ml/pipeline/generate \
  -H "Authorization: Bearer <token>"

# 2. Run preprocessing (outputs train/val/test CSVs + scaler)
curl -X POST http://localhost:8000/api/ml/pipeline/run \
  -H "Authorization: Bearer <token>"

# 3. Inspect engineered features
curl http://localhost:8000/api/ml/pipeline/features \
  -H "Authorization: Bearer <token>"

# 4. Train the model
curl -X POST http://localhost:8000/api/predictions/train \
  -H "Authorization: Bearer <token>"

# 5. Check model metrics
curl http://localhost:8000/api/predictions/model/status \
  -H "Authorization: Bearer <token>"

# 6. Generate 30-day forecast for product 1
curl -X POST http://localhost:8000/api/predictions/forecast \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"product_id": 1, "days": 30}'

# 7. View forecast accuracy report
curl http://localhost:8000/api/reports/forecast/accuracy \
  -H "Authorization: Bearer <token>"
```

---

## Configuration

All ML hyperparameters in `ml/config.py`:

```python
FORECAST_HORIZON_DAYS = 30
LOOKBACK_DAYS = 60
SYNTHETIC_PRODUCTS = 20
SYNTHETIC_DAYS = 365
LAG_DAYS = [1, 3, 7, 14, 21, 28]
ROLLING_WINDOWS = [7, 14, 30]
```

---

## Extending the Pipeline

To use real data instead of synthetic:
1. Prepare a CSV with columns: `product_id, date, demand, unit_price`
2. Place at `ml/datasets/synthetic_retail_demand.csv`
3. Skip `POST /api/ml/pipeline/generate`
4. Run `POST /api/ml/pipeline/run` → train as normal

To switch models (e.g. RandomForest, XGBoost):
1. Edit `ml/training/model_trainer.py`
2. Replace `GradientBoostingRegressor` with your model
3. Retrain via `POST /api/predictions/train`
