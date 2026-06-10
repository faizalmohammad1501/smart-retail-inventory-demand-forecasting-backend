"""
InventoryRecommendationService: combines live inventory levels, order history,
supplier data, and demand forecasting to generate actionable restocking
recommendations for every product.

Core algorithms:
- avg_daily_demand    : rolling 30-day sales average from orders
- lead_time_days      : derived from avg_total_time in orders (fallback 7 d)
- safety_stock        : z * σ_demand * √lead_time  (95 % service level)
- reorder_point       : avg_demand * lead_time + safety_stock
- reorder_quantity    : covers lead_time + review_period, minus on-hand
- stockout_risk_score : 0-100 continuous risk index
- priority            : CRITICAL / HIGH / MEDIUM / LOW
- action              : REORDER_NOW / REORDER_SOON / MONITOR /
                        NO_ACTION / REDUCE_STOCK
"""
import logging
import math
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.inventory import Inventory
from app.models.product import Product
from app.models.sales import Order
from app.models.supplier import Supplier

logger = logging.getLogger(__name__)

# ── Tunable constants ─────────────────────────────────────────────────────────
SERVICE_LEVEL_Z: float = 1.65          # 95 % service level
DEFAULT_LEAD_TIME_DAYS: float = 7.0    # used when no order history exists
REVIEW_PERIOD_DAYS: int = 14           # replenishment review cycle
DEMAND_WINDOW_DAYS: int = 30           # rolling window for avg daily demand
OVERSTOCK_MULTIPLIER: float = 5.0      # stock > reorder_level × this → overstock


class InventoryRecommendationService:
    """All recommendation logic for a single request lifecycle."""

    def __init__(self, db: Session) -> None:
        self.db = db
        self._cutoff = datetime.utcnow() - timedelta(days=DEMAND_WINDOW_DAYS)

    # ─────────────────────────────────────────────────────────────────────────
    # Public API
    # ─────────────────────────────────────────────────────────────────────────

    def get_all_recommendations(self) -> Dict[str, Any]:
        """
        Generate recommendations for every product in the catalogue.
        Returns a summary dict plus the full list of per-product recommendations.
        """
        products = self.db.query(Product).all()
        if not products:
            return {"total_products": 0, "recommendations": []}

        recommendations = [self._build_recommendation(p) for p in products]
        recommendations.sort(key=lambda r: r["stockout_risk_score"], reverse=True)

        return {
            "generated_at": datetime.utcnow().isoformat(),
            "total_products": len(recommendations),
            "summary": _aggregate_summary(recommendations),
            "recommendations": recommendations,
        }

    def get_product_recommendation(self, product_id: int) -> Dict[str, Any]:
        """Recommendation for a single product (raises ValueError if not found)."""
        product = self.db.query(Product).filter(Product.id == product_id).first()
        if not product:
            raise ValueError(f"Product with id={product_id} not found.")
        return self._build_recommendation(product)

    def get_inventory_health(self) -> Dict[str, Any]:
        """
        Dashboard-ready inventory health overview.
        Returns KPI counts and category-level health breakdown.
        """
        recs = self.get_all_recommendations()["recommendations"]
        if not recs:
            return {"status": "no_products", "kpis": {}, "by_category": [], "by_priority": []}

        kpis = _aggregate_summary(recs)

        # Category breakdown
        cat_map: Dict[str, Dict] = {}
        for r in recs:
            cat = r.get("category") or "Unknown"
            if cat not in cat_map:
                cat_map[cat] = {"category": cat, "total": 0, "critical": 0,
                                "high": 0, "medium": 0, "low": 0,
                                "avg_risk_score": 0.0, "risk_total": 0.0}
            cat_map[cat]["total"] += 1
            cat_map[cat][r["priority"].lower()] += 1
            cat_map[cat]["risk_total"] += r["stockout_risk_score"]

        by_category = []
        for cat, data in cat_map.items():
            data["avg_risk_score"] = round(data["risk_total"] / data["total"], 1)
            data.pop("risk_total")
            by_category.append(data)
        by_category.sort(key=lambda x: x["avg_risk_score"], reverse=True)

        # Priority distribution
        from collections import Counter
        priority_counts = Counter(r["priority"] for r in recs)
        by_priority = [
            {"priority": p, "count": priority_counts.get(p, 0)}
            for p in ("CRITICAL", "HIGH", "MEDIUM", "LOW")
        ]

        return {
            "generated_at": datetime.utcnow().isoformat(),
            "kpis": kpis,
            "by_category": by_category,
            "by_priority": by_priority,
        }

    def get_critical_alerts(self) -> Dict[str, Any]:
        """Return only CRITICAL and HIGH priority items, sorted by risk score."""
        recs = self.get_all_recommendations()["recommendations"]
        alerts = [r for r in recs if r["priority"] in ("CRITICAL", "HIGH")]
        return {
            "generated_at": datetime.utcnow().isoformat(),
            "total_alerts": len(alerts),
            "alerts": alerts,
        }

    def get_replenishment_list(self) -> Dict[str, Any]:
        """
        Items where action is REORDER_NOW or REORDER_SOON, enriched with
        supplier contact details for purchase-order generation.
        """
        recs = self.get_all_recommendations()["recommendations"]
        to_reorder = [
            r for r in recs
            if r["action"] in ("REORDER_NOW", "REORDER_SOON")
        ]

        # Enrich with supplier data
        supplier_ids = {r["supplier_id"] for r in to_reorder if r.get("supplier_id")}
        suppliers = {
            s.id: s
            for s in self.db.query(Supplier).filter(Supplier.id.in_(supplier_ids)).all()
        } if supplier_ids else {}

        for r in to_reorder:
            sid = r.get("supplier_id")
            sup = suppliers.get(sid)
            r["supplier_info"] = (
                {
                    "supplier_name": sup.supplier_name,
                    "contact_person": sup.contact_person,
                    "email": sup.email,
                    "phone": sup.phone,
                    "rating": sup.rating,
                }
                if sup else None
            )

        return {
            "generated_at": datetime.utcnow().isoformat(),
            "total_items_to_reorder": len(to_reorder),
            "estimated_total_reorder_value": round(
                sum(r["reorder_quantity"] * r["unit_price"] for r in to_reorder), 2
            ),
            "replenishment_list": to_reorder,
        }

    # ─────────────────────────────────────────────────────────────────────────
    # Core per-product logic
    # ─────────────────────────────────────────────────────────────────────────

    def _build_recommendation(self, product: Product) -> Dict[str, Any]:
        """Full recommendation dict for one product."""
        # ── Inventory snapshot ────────────────────────────────────────────────
        inv = (
            self.db.query(Inventory)
            .filter(Inventory.product_id == product.id)
            .order_by(Inventory.last_restocked.desc())
            .first()
        )
        qty_available = int(inv.quantity_available) if inv else 0
        qty_reserved = int(inv.quantity_reserved) if inv else 0
        available_stock = max(0, qty_available - qty_reserved)
        last_restocked = inv.last_restocked.isoformat() if (inv and inv.last_restocked) else None

        # ── Demand analysis (last 30 days) ────────────────────────────────────
        demand_stats = self._compute_demand_stats(product.id)
        avg_daily_demand = demand_stats["avg_daily_demand"]
        std_daily_demand = demand_stats["std_daily_demand"]
        total_orders_30d = demand_stats["total_orders"]
        total_qty_30d = demand_stats["total_quantity"]

        # ── Lead time (from order history) ────────────────────────────────────
        lead_time_days = self._estimate_lead_time(product.id)

        # ── Reorder calculations ──────────────────────────────────────────────
        safety_stock = round(
            SERVICE_LEVEL_Z * std_daily_demand * math.sqrt(max(lead_time_days, 1)), 2
        )
        reorder_point = round(avg_daily_demand * lead_time_days + safety_stock, 2)
        demand_during_replenishment = avg_daily_demand * (lead_time_days + REVIEW_PERIOD_DAYS)
        reorder_quantity = max(
            0,
            round(demand_during_replenishment + safety_stock - available_stock, 0),
        )
        # Minimum order: at least cover the reorder level if stock is very low
        reorder_level = int(product.reorder_level or 10)
        if available_stock <= reorder_level and reorder_quantity < reorder_level:
            reorder_quantity = float(reorder_level)

        # ── Days of stock remaining ───────────────────────────────────────────
        days_of_stock = (
            round(available_stock / avg_daily_demand, 1)
            if avg_daily_demand > 0
            else 999.0
        )

        # ── Stockout risk score 0-100 ─────────────────────────────────────────
        risk_score = _compute_risk_score(
            days_of_stock=days_of_stock,
            lead_time_days=lead_time_days,
            reorder_point=reorder_point,
            available_stock=available_stock,
            reorder_level=reorder_level,
        )

        # ── Priority & action ─────────────────────────────────────────────────
        priority = _classify_priority(risk_score, available_stock, reorder_level, reorder_point)
        action = _recommend_action(
            priority=priority,
            available_stock=available_stock,
            reorder_level=reorder_level,
        )

        # ── Stockout date estimate ────────────────────────────────────────────
        stockout_date = None
        if avg_daily_demand > 0 and available_stock > 0:
            days_left = available_stock / avg_daily_demand
            stockout_date = (
                datetime.utcnow() + timedelta(days=days_left)
            ).date().isoformat()

        return {
            # Identity
            "product_id": product.id,
            "product_name": product.product_name,
            "sku": product.sku,
            "category": product.category,
            "unit_price": float(product.unit_price),
            "supplier_id": product.supplier_id,
            # Inventory
            "current_stock": qty_available,
            "reserved_stock": qty_reserved,
            "available_stock": available_stock,
            "reorder_level": reorder_level,
            "last_restocked": last_restocked,
            # Demand
            "avg_daily_demand": round(avg_daily_demand, 2),
            "std_daily_demand": round(std_daily_demand, 2),
            "total_orders_last_30d": total_orders_30d,
            "total_qty_sold_last_30d": total_qty_30d,
            # Calculations
            "lead_time_days": round(lead_time_days, 1),
            "safety_stock": safety_stock,
            "reorder_point": reorder_point,
            "reorder_quantity": int(reorder_quantity),
            "estimated_reorder_value": round(reorder_quantity * float(product.unit_price), 2),
            # Risk & status
            "days_of_stock_remaining": days_of_stock,
            "stockout_risk_score": round(risk_score, 1),
            "estimated_stockout_date": stockout_date,
            "priority": priority,
            "action": action,
        }

    def _compute_demand_stats(self, product_id: int) -> Dict[str, Any]:
        """Compute avg / std daily demand from orders in the last 30 days."""
        import numpy as np

        rows = (
            self.db.query(
                func.date(Order.order_placed_at).label("day"),
                func.sum(Order.quantity).label("qty"),
            )
            .filter(
                Order.product_id == product_id,
                Order.order_placed_at >= self._cutoff,
            )
            .group_by(func.date(Order.order_placed_at))
            .all()
        )

        if not rows:
            return {
                "avg_daily_demand": 0.0,
                "std_daily_demand": 0.0,
                "total_orders": 0,
                "total_quantity": 0,
            }

        daily_quantities = np.array([float(r.qty) for r in rows], dtype=float)

        # Pad to 30 days to account for days with zero demand
        full_series = np.zeros(DEMAND_WINDOW_DAYS)
        full_series[: len(daily_quantities)] = daily_quantities

        return {
            "avg_daily_demand": float(np.mean(full_series)),
            "std_daily_demand": float(np.std(full_series)),
            "total_orders": len(rows),
            "total_quantity": int(np.sum(daily_quantities)),
        }

    def _estimate_lead_time(self, product_id: int) -> float:
        """
        Estimate lead time as avg total_time (hours) → days from recent orders.
        Falls back to DEFAULT_LEAD_TIME_DAYS if no data.
        """
        result = (
            self.db.query(func.avg(Order.total_time))
            .filter(
                Order.product_id == product_id,
                Order.total_time.isnot(None),
                Order.order_placed_at >= self._cutoff,
            )
            .scalar()
        )
        if result:
            return max(1.0, round(float(result) / 24, 1))
        return DEFAULT_LEAD_TIME_DAYS


# ─────────────────────────────────────────────────────────────────────────────
# Pure helper functions (stateless)
# ─────────────────────────────────────────────────────────────────────────────

def _compute_risk_score(
    days_of_stock: float,
    lead_time_days: float,
    reorder_point: float,
    available_stock: int,
    reorder_level: int,
) -> float:
    """
    Continuous stockout risk score in [0, 100].

    Zones:
    100  – already out of stock
    80-99 – will stock out before replenishment arrives
    50-79 – stock at or below reorder point
    20-49 – stock below 1.5× reorder level (watch zone)
    0-19  – healthy stock level
    """
    if available_stock <= 0:
        return 100.0

    if days_of_stock <= lead_time_days:
        # Will run out before new stock arrives
        fraction = days_of_stock / max(lead_time_days, 0.01)
        return round(99.0 - fraction * 19.0, 1)   # 80-99

    if available_stock <= reorder_point:
        overshoot = available_stock / max(reorder_point, 0.01)
        return round(79.0 - overshoot * 29.0, 1)   # 50-79

    if available_stock <= reorder_level * 1.5:
        ratio = (available_stock - reorder_level) / max(reorder_level * 0.5, 0.01)
        return round(49.0 - ratio * 29.0, 1)        # 20-49

    # Healthy: risk scales down toward 0 as stock grows
    surplus_ratio = available_stock / max(reorder_level * 1.5, 1.0)
    return round(max(0.0, 19.0 - (surplus_ratio - 1.0) * 5.0), 1)


def _classify_priority(
    risk_score: float,
    available_stock: int,
    reorder_level: int,
    reorder_point: float,
) -> str:
    if risk_score >= 80 or available_stock <= 0:
        return "CRITICAL"
    if risk_score >= 50 or available_stock <= reorder_level:
        return "HIGH"
    if risk_score >= 20 or available_stock <= reorder_level * 1.5:
        return "MEDIUM"
    return "LOW"


def _recommend_action(
    priority: str,
    available_stock: int,
    reorder_level: int,
) -> str:
    if priority == "CRITICAL":
        return "REORDER_NOW"
    if priority == "HIGH":
        return "REORDER_SOON"
    if priority == "MEDIUM":
        return "MONITOR"
    if available_stock > reorder_level * int(OVERSTOCK_MULTIPLIER):
        return "REDUCE_STOCK"
    return "NO_ACTION"


def _aggregate_summary(recommendations: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Compute KPI summary counts from a list of recommendation dicts."""
    total = len(recommendations)
    critical = sum(1 for r in recommendations if r["priority"] == "CRITICAL")
    high = sum(1 for r in recommendations if r["priority"] == "HIGH")
    medium = sum(1 for r in recommendations if r["priority"] == "MEDIUM")
    low = sum(1 for r in recommendations if r["priority"] == "LOW")
    out_of_stock = sum(1 for r in recommendations if r["available_stock"] <= 0)
    reorder_now = sum(1 for r in recommendations if r["action"] == "REORDER_NOW")
    reorder_soon = sum(1 for r in recommendations if r["action"] == "REORDER_SOON")
    avg_risk = (
        round(sum(r["stockout_risk_score"] for r in recommendations) / total, 1)
        if total > 0 else 0.0
    )
    total_reorder_value = round(
        sum(r["estimated_reorder_value"] for r in recommendations
            if r["action"] in ("REORDER_NOW", "REORDER_SOON")), 2
    )

    return {
        "total_products": total,
        "out_of_stock": out_of_stock,
        "critical": critical,
        "high": high,
        "medium": medium,
        "low": low,
        "require_immediate_reorder": reorder_now,
        "require_reorder_soon": reorder_soon,
        "avg_stockout_risk_score": avg_risk,
        "estimated_total_reorder_value": total_reorder_value,
    }
