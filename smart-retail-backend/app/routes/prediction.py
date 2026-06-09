"""
Prediction API routes: demand forecasting endpoints.

Endpoints:
  POST /api/predictions/train              – train the GBR model
  GET  /api/predictions/model/status       – check model readiness
  POST /api/predictions/forecast           – forecast one or all products
  GET  /api/predictions/forecast/{id}      – single-product forecast (GET)
  GET  /api/predictions/forecast           – all-products forecast (GET)
"""
import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_active_user, require_roles
from app.database.connection import get_db
from app.models.user import User
from app.schemas.schemas import (
    BatchForecastResponse,
    ForecastRequest,
    ModelStatusResponse,
    ProductForecastResponse,
    TrainModelRequest,
    TrainModelResponse,
)
from app.services.forecast_prediction_service import ForecastPredictionService
from ml.config import FORECAST_HORIZON_DAYS
from ml.training.model_trainer import load_training_metadata, train

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/predictions", tags=["Demand Forecasting & Predictions"])


# ─────────────────────────────────────────────────────────────────────────────
# 1. Train model
# ─────────────────────────────────────────────────────────────────────────────

@router.post(
    "/train",
    response_model=TrainModelResponse,
    status_code=status.HTTP_200_OK,
    summary="Train the GradientBoosting demand forecasting model",
)
def train_model(
    request: TrainModelRequest = TrainModelRequest(),
    current_user: User = Depends(require_roles("admin", "analyst")),
) -> TrainModelResponse:
    """
    Trains a GradientBoostingRegressor on the preprocessed `train.csv`.

    Prerequisites:
    - Run `POST /api/ml/pipeline/run` first to build the processed datasets.

    Requires role: `admin` or `analyst`.
    """
    hyperparams = {}
    if request.n_estimators is not None:
        hyperparams["n_estimators"] = request.n_estimators
    if request.learning_rate is not None:
        hyperparams["learning_rate"] = request.learning_rate
    if request.max_depth is not None:
        hyperparams["max_depth"] = request.max_depth

    try:
        result = train(hyperparams=hyperparams or None)
        if not result.success:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=result.message,
            )
        return TrainModelResponse(
            success=result.success,
            message=result.message,
            trained_at=result.trained_at,
            n_train_samples=result.n_train_samples,
            n_val_samples=result.n_val_samples,
            n_features=result.n_features,
            model_path=result.model_path,
            train_metrics=result.train_metrics,
            val_metrics=result.val_metrics,
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Model training failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Training failed: {exc}",
        )


# ─────────────────────────────────────────────────────────────────────────────
# 2. Model status
# ─────────────────────────────────────────────────────────────────────────────

@router.get(
    "/model/status",
    response_model=ModelStatusResponse,
    status_code=status.HTTP_200_OK,
    summary="Check whether the demand forecasting model has been trained",
)
def model_status(
    current_user: User = Depends(get_current_active_user),
) -> ModelStatusResponse:
    """
    Returns training metadata if the model artefact exists,
    otherwise indicates the model needs to be trained.
    """
    metadata = load_training_metadata()
    if metadata is None:
        return ModelStatusResponse(
            model_trained=False,
            message="No trained model found. Run POST /api/predictions/train first.",
        )
    return ModelStatusResponse(
        model_trained=True,
        model_type=metadata.get("model_type"),
        trained_at=metadata.get("trained_at"),
        n_features=metadata.get("n_features"),
        n_train_samples=metadata.get("n_train_samples"),
        val_metrics=metadata.get("val_metrics"),
        message="Model is ready for inference.",
    )


# ─────────────────────────────────────────────────────────────────────────────
# 3. Forecast – POST (body-driven, single or batch)
# ─────────────────────────────────────────────────────────────────────────────

@router.post(
    "/forecast",
    status_code=status.HTTP_200_OK,
    summary="Forecast demand for one product or all products",
)
def forecast_post(
    request: ForecastRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> Dict[str, Any]:
    """
    If `product_id` is provided, returns a single-product forecast.
    If omitted, returns a batch forecast for all products.

    The API works even if no model has been trained — it falls back
    to a weighted moving average baseline automatically.
    """
    svc = ForecastPredictionService(db)
    try:
        if request.product_id is not None:
            result = svc.forecast_product(request.product_id, request.horizon_days)
        else:
            result = svc.forecast_all_products(request.horizon_days)
        return {"status": "success", "data": result}
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except Exception as exc:
        logger.exception("Forecast failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Forecast error: {exc}",
        )


# ─────────────────────────────────────────────────────────────────────────────
# 4. Single-product forecast – GET
# ─────────────────────────────────────────────────────────────────────────────

@router.get(
    "/forecast/{product_id}",
    status_code=status.HTTP_200_OK,
    summary="Forecast demand for a specific product",
)
def forecast_single(
    product_id: int,
    horizon_days: int = Query(default=FORECAST_HORIZON_DAYS, ge=1, le=90),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> Dict[str, Any]:
    """
    Returns a `horizon_days`-step ahead demand forecast for the specified
    product, including daily predictions, confidence intervals, inventory
    status, and stock recommendations.
    """
    svc = ForecastPredictionService(db)
    try:
        result = svc.forecast_product(product_id, horizon_days)
        return {"status": "success", "data": result}
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except Exception as exc:
        logger.exception("Forecast failed for product %d: %s", product_id, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Forecast error: {exc}",
        )


# ─────────────────────────────────────────────────────────────────────────────
# 5. All-products forecast – GET
# ─────────────────────────────────────────────────────────────────────────────

@router.get(
    "/forecast",
    status_code=status.HTTP_200_OK,
    summary="Forecast demand for all products",
)
def forecast_all(
    horizon_days: int = Query(default=FORECAST_HORIZON_DAYS, ge=1, le=90),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> Dict[str, Any]:
    """
    Returns demand forecasts for every product in the catalogue.
    Suitable for populating a dashboard overview / Tableau data source.
    """
    svc = ForecastPredictionService(db)
    try:
        result = svc.forecast_all_products(horizon_days)
        return {"status": "success", "data": result}
    except Exception as exc:
        logger.exception("Batch forecast failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Batch forecast error: {exc}",
        )
