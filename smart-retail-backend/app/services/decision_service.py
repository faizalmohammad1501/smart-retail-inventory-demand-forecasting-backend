"""
Decision Support Service
=========================
Translates raw analytics signals from insights_service.py into prioritised,
actionable recommendations for procurement, inventory optimisation, and
supplier management.

Each recommendation has:
  - id              (deterministic hash of product/supplier/category + type)
  - type            (procurement | inventory | supplier | pricing | category)
  - priority        (CRITICAL | HIGH | MEDIUM | LOW)
  - priority_score  (0–100 for sorting)
  - title           (concise headline)
  - description     (full narrative)
  - action          (machine-readable verb)
  - context         (dict with supporting numbers)
  - estimated_value (monetary impact estimate, may be 0)
  - expires_in_days (how long this rec stays relevant before data is stale)

Recommendations are deduplicated by (type, resource_id) so the same product
cannot appear twice under the same recommendation type in a single run.
"""

import hashlib
import statistics
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import func, case
from sqlalchemy.orm import Session

from app.models.inventory import Inventory
from app.models.product import Product
from app.models.sales import Order
from app.models.supplier import Supplier
from app.services.insights_service import (
    score_inventory_risks,
    classify_product_velocity,
    analyze_supplier_performance,
    identify_revenue_opportunities,
    detect_dead_stock,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _r(v, n: int = 2) -> float:
    return round(float(v), n) if v is not None else 0.0


def _rec_id(rec_type: str, resource: str) -> str:
    """Deterministic short ID for deduplication."""
    raw = f"{rec_type}:{resource}"
    return hashlib.md5(raw.encode()).hexdigest()[:12]


_PRIORITY_SCORE = {"CRITICAL": 100, "HIGH": 75, "MEDIUM": 40, "LOW": 10}


def _score(priority: str) -> int:
    return _PRIORITY_SCORE.get(priority, 10)


# ── Main entry point ──────────────────────────────────────────────────────────

def generate_recommendations(
    db: Session,
    lookback_days: int = 30,
    max_per_type: int = 20,
) -> Dict[str, Any]:
    """
    Run all insight engines and compile a unified, deduplicated recommendation
    list ranked by priority_score descending.

    Parameters
    ----------
    lookback_days
        Rolling window for demand / sales analytics (default 30 days).
    max_per_type
        Maximum number of recommendations per type to prevent noise.

    Returns
    -------
    {
        generated_at:     ISO timestamp
        lookback_days:    int
        total:            int
        by_type:          {type: count}
        by_priority:      {priority: count}
        recommendations:  [...]
    }
    """
    seen: set = set()  # dedup keys
    recs: List[Dict] = []

    def _add(rec: Dict) -> None:
        key = (rec["type"], rec.get("resource_id", ""))
        if key not in seen:
            seen.add(key)
            recs.append(rec)

    # ── 1. Procurement / reorder recommendations (from risk scores) ───────────
    risk_data = score_inventory_risks(db, lookback_days=lookback_days)
    proc_count = 0
    for p in risk_data["products"]:
        if proc_count >= max_per_type:
            break
        tier = p["risk_tier"]
        if tier not in ("CRITICAL", "HIGH"):
            continue

        dom = p["dominant_risk"]
        if dom == "stockout":
            action = "REORDER_NOW" if tier == "CRITICAL" else "REORDER_SOON"
            title  = f"Reorder {p['product_name']} — stockout risk {tier}"
            desc   = (
                f"{p['product_name']} (SKU: {p['sku']}) has {p['net_quantity']:.0f} units on hand "
                f"against a reorder level of {p['reorder_level']:.0f}. "
                f"At current demand of {p['avg_daily_demand']:.1f} units/day, "
                f"stock lasts only {p['days_on_hand']:.0f} more days."
            )
            expires_in = max(1, int(p["days_on_hand"]))
        elif dom == "overstock":
            action = "REDUCE_PURCHASING"
            title  = f"Reduce purchasing for {p['product_name']} — overstock risk"
            desc   = (
                f"{p['product_name']} has {p['net_quantity']:.0f} units ({p['days_on_hand']:.0f} days "
                f"of supply) against avg daily demand of {p['avg_daily_demand']:.1f} units/day. "
                f"Pause procurement to reduce carrying costs."
            )
            expires_in = 14
        else:
            continue

        _add({
            "id":              _rec_id("procurement", str(p["product_id"])),
            "type":            "procurement",
            "priority":        tier,
            "priority_score":  _score(tier) + int(p["risk_score"]) // 10,
            "title":           title,
            "description":     desc,
            "action":          action,
            "resource_id":     str(p["product_id"]),
            "resource_type":   "product",
            "resource_name":   p["product_name"],
            "sku":             p["sku"],
            "category":        p["category"],
            "context": {
                "risk_score":       p["risk_score"],
                "net_quantity":     p["net_quantity"],
                "reorder_level":    p["reorder_level"],
                "days_on_hand":     p["days_on_hand"],
                "avg_daily_demand": p["avg_daily_demand"],
            },
            "estimated_value": 0,
            "expires_in_days": expires_in,
        })
        proc_count += 1

    # ── 2. Inventory / dead-stock recommendations ─────────────────────────────
    dead = detect_dead_stock(db, days_threshold=90)
    inv_count = 0
    for item in dead["items"]:
        if inv_count >= max_per_type:
            break
        priority = "HIGH" if item["book_value"] > 5000 else "MEDIUM"
        _add({
            "id":              _rec_id("inventory", str(item["product_id"])),
            "type":            "inventory",
            "priority":        priority,
            "priority_score":  _score(priority) + min(25, int(item["book_value"] / 1000)),
            "title":           f"Dead stock alert: {item['product_name']}",
            "description":     (
                f"{item['product_name']} (SKU: {item['sku']}) has {item['quantity_on_hand']:.0f} units "
                f"(book value ${item['book_value']:,.0f}) with no demand in {item['days_idle']} days. "
                f"Monthly carrying cost: ${item['carrying_cost_monthly']:,.0f}. "
                f"Action: {item['recommendation']}."
            ),
            "action":          "LIQUIDATE" if item["days_idle"] > 180 else "MARKDOWN",
            "resource_id":     str(item["product_id"]),
            "resource_type":   "product",
            "resource_name":   item["product_name"],
            "sku":             item["sku"],
            "category":        item["category"],
            "context": {
                "quantity_on_hand":       item["quantity_on_hand"],
                "book_value":             item["book_value"],
                "days_idle":              item["days_idle"],
                "carrying_cost_monthly":  item["carrying_cost_monthly"],
            },
            "estimated_value": item["carrying_cost_monthly"] * 3,  # 3-month saving
            "expires_in_days": 30,
        })
        inv_count += 1

    # ── 3. Supplier recommendations ───────────────────────────────────────────
    sup_data = analyze_supplier_performance(db, lookback_days=max(lookback_days, 60))
    sup_count = 0
    for s in sup_data["suppliers"]:
        if sup_count >= max_per_type:
            break
        score = s["performance_score"]
        if score >= 70:
            continue   # healthy — no recommendation needed
        priority = "HIGH" if score < 50 else "MEDIUM"
        _add({
            "id":              _rec_id("supplier", str(s["supplier_id"])),
            "type":            "supplier",
            "priority":        priority,
            "priority_score":  _score(priority) + int((70 - score) / 2),
            "title":           f"Supplier performance alert: {s['supplier_name']}",
            "description":     (
                f"{s['supplier_name']} has a performance score of {score:.0f}/100. "
                f"On-time delivery rate: {s['on_time_rate_pct']:.1f}%, "
                f"SLA breach rate: {s['sla_breach_rate_pct']:.1f}%, "
                f"avg procurement time: {s['avg_procurement_hours']:.1f}h. "
                f"Trend: {s['trend']}. "
                + ("Consider alternative suppliers." if score < 50 else
                   "Schedule a performance review.")
            ),
            "action":          "EVALUATE_SUPPLIER" if score < 50 else "REVIEW_SUPPLIER",
            "resource_id":     str(s["supplier_id"]),
            "resource_type":   "supplier",
            "resource_name":   s["supplier_name"],
            "sku":             None,
            "category":        None,
            "context": {
                "performance_score":      score,
                "on_time_rate_pct":       s["on_time_rate_pct"],
                "sla_breach_rate_pct":    s["sla_breach_rate_pct"],
                "avg_procurement_hours":  s["avg_procurement_hours"],
                "trend":                  s["trend"],
                "order_volume":           s["order_volume"],
            },
            "estimated_value": 0,
            "expires_in_days": 14,
        })
        sup_count += 1

    # ── 4. Pricing / markdown recommendations ────────────────────────────────
    opp_data = identify_revenue_opportunities(db, lookback_days=lookback_days)
    price_count = 0
    for opp in opp_data["opportunities"]:
        if price_count >= max_per_type:
            break
        if opp["type"] not in ("markdown_opportunity", "stockout_loss", "demand_supply_gap"):
            continue
        priority = opp["priority"]
        _add({
            "id":              _rec_id("pricing", str(opp.get("product_id") or opp.get("category", ""))),
            "type":            "pricing",
            "priority":        priority,
            "priority_score":  _score(priority) + min(20, int(opp["opportunity_value"] / 1000)),
            "title":           _opp_title(opp),
            "description":     opp["description"],
            "action":          opp["action"],
            "resource_id":     str(opp.get("product_id") or ""),
            "resource_type":   "product" if opp.get("product_id") else "category",
            "resource_name":   opp.get("product_name") or opp.get("category", ""),
            "sku":             opp.get("sku"),
            "category":        opp.get("category"),
            "context": {
                "opportunity_type":  opp["type"],
                "opportunity_value": opp["opportunity_value"],
            },
            "estimated_value": opp["opportunity_value"],
            "expires_in_days": 7,
        })
        price_count += 1

    # ── 5. Category / strategic recommendations ───────────────────────────────
    cat_count = 0
    for opp in opp_data["opportunities"]:
        if cat_count >= 5:
            break
        if opp["type"] != "category_decline":
            continue
        _add({
            "id":              _rec_id("category", opp.get("category", "")),
            "type":            "category",
            "priority":        "HIGH",
            "priority_score":  _score("HIGH"),
            "title":           f"Category decline: {opp['category']}",
            "description":     opp["description"],
            "action":          "INVESTIGATE_CATEGORY",
            "resource_id":     opp.get("category", ""),
            "resource_type":   "category",
            "resource_name":   opp.get("category", ""),
            "sku":             None,
            "category":        opp.get("category"),
            "context":         {"opportunity_value": opp["opportunity_value"]},
            "estimated_value": opp["opportunity_value"],
            "expires_in_days": 14,
        })
        cat_count += 1

    # ── 6. Velocity-based smart buying recommendations ─────────────────────────
    vel_data = classify_product_velocity(db, lookback_days=lookback_days)
    vel_count = 0
    for p in vel_data["products"]:
        if vel_count >= max_per_type:
            break
        if p["velocity"] not in ("fast_erratic", "slow_erratic"):
            continue
        priority = "MEDIUM"
        action = "SMOOTH_DEMAND" if p["xyz_class"] == "Z" else "REVIEW_FORECAST"
        _add({
            "id":              _rec_id("inventory_strategy", str(p["product_id"])),
            "type":            "inventory_strategy",
            "priority":        priority,
            "priority_score":  _score(priority) + int(p["cv"] * 10),
            "title":           f"Erratic demand pattern: {p['product_name']}",
            "description":     (
                f"{p['product_name']} shows highly variable demand (CV={p['cv']:.2f}, "
                f"segment={p['segment']}). Consider safety-stock buffers or "
                f"demand-smoothing promotions to reduce forecast error."
            ),
            "action":          action,
            "resource_id":     str(p["product_id"]),
            "resource_type":   "product",
            "resource_name":   p["product_name"],
            "sku":             p["sku"],
            "category":        p["category"],
            "context": {
                "abc_class":      p["abc_class"],
                "xyz_class":      p["xyz_class"],
                "cv":             p["cv"],
                "avg_weekly_qty": p["avg_weekly_qty"],
            },
            "estimated_value": 0,
            "expires_in_days": 30,
        })
        vel_count += 1

    # ── Sort and summarise ────────────────────────────────────────────────────
    recs.sort(key=lambda r: r["priority_score"], reverse=True)

    by_type: Dict[str, int]     = defaultdict(int)
    by_priority: Dict[str, int] = defaultdict(int)
    for r in recs:
        by_type[r["type"]] += 1
        by_priority[r["priority"]] += 1

    return {
        "generated_at":    _now().isoformat(),
        "lookback_days":   lookback_days,
        "total":           len(recs),
        "by_type":         dict(by_type),
        "by_priority":     dict(by_priority),
        "recommendations": recs,
    }


# ── Reorder urgency ranking ───────────────────────────────────────────────────

def rank_reorder_urgency(db: Session, lookback_days: int = 30) -> Dict[str, Any]:
    """
    Rank all products by how urgently they need to be reordered.

    Urgency score combines:
      - days_on_hand relative to a 14-day target lead time
      - risk_score from the inventory risk model
      - demand trend (is demand growing?)

    Returns a ranked list with urgency_score (0–100) and
    recommended_order_qty (safety stock formula).
    """
    risk_data  = score_inventory_risks(db, lookback_days=lookback_days)
    vel_data   = classify_product_velocity(db, lookback_days=lookback_days)

    vel_map = {p["product_id"]: p for p in vel_data["products"]}
    TARGET_LEAD_TIME = 14  # days

    ranked = []
    for p in risk_data["products"]:
        pid  = p["product_id"]
        vel  = vel_map.get(pid, {})
        doh  = p["days_on_hand"]
        risk = p["risk_score"]

        # Urgency: 100 when doh=0, falls off toward 0 at doh=TARGET*2
        doh_urgency = max(0.0, 100 * (1 - doh / (TARGET_LEAD_TIME * 2)))
        urgency = _r(doh_urgency * 0.60 + risk * 0.40)

        # Safety stock = z_factor × std_dev × sqrt(lead_time)
        # Conservative: 1.65 z (95 % service level)
        daily_demand = p["avg_daily_demand"]
        cv           = float(vel.get("cv", 0.3))
        demand_std   = daily_demand * cv
        safety_stock = _r(1.65 * demand_std * (TARGET_LEAD_TIME ** 0.5))
        reorder_qty  = _r(max(p["reorder_level"], daily_demand * TARGET_LEAD_TIME + safety_stock))

        ranked.append({
            "product_id":         pid,
            "product_name":       p["product_name"],
            "sku":                p["sku"],
            "category":           p["category"],
            "urgency_score":      urgency,
            "urgency_tier":       (
                "CRITICAL" if urgency >= 75 else
                "HIGH"      if urgency >= 50 else
                "MEDIUM"    if urgency >= 25 else "LOW"
            ),
            "days_on_hand":         p["days_on_hand"],
            "avg_daily_demand":     p["avg_daily_demand"],
            "risk_score":           risk,
            "safety_stock":         safety_stock,
            "recommended_order_qty": reorder_qty,
            "net_quantity":         p["net_quantity"],
        })

    ranked.sort(key=lambda r: r["urgency_score"], reverse=True)

    return {
        "lookback_days":  lookback_days,
        "total_products": len(ranked),
        "critical_count": sum(1 for r in ranked if r["urgency_tier"] == "CRITICAL"),
        "high_count":     sum(1 for r in ranked if r["urgency_tier"] == "HIGH"),
        "products":       ranked,
    }


# ── Internal helper ───────────────────────────────────────────────────────────

def _opp_title(opp: Dict) -> str:
    type_labels = {
        "stockout_loss":      "Revenue at risk — out of stock",
        "demand_supply_gap":  "Demand exceeds supply",
        "markdown_opportunity": "Markdown to unlock stalled stock",
        "category_decline":   "Category revenue decline",
    }
    label = type_labels.get(opp["type"], opp["type"].replace("_", " ").title())
    name  = opp.get("product_name") or opp.get("category") or ""
    return f"{label}: {name}" if name else label
