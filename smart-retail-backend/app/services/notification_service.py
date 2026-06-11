"""
NotificationService: monitors the platform and auto-generates persisted
alerts for low stock, reorder triggers, demand surges, SLA breaches,
bottlenecks, and supplier delays.

Design:
- Each alert type has a dedicated _generate_* method.
- A dedup_key prevents creating the same alert twice per day.
- run_all_checks() is the single entry point called by the API.
- CRUD helpers (list, mark_read, mark_resolved, delete) are also here.
"""
import logging
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.inventory import Inventory
from app.models.notification import Notification
from app.models.product import Product
from app.models.sales import Order
from app.models.supplier import Supplier
from app.services.inventory_recommendation_service import InventoryRecommendationService

logger = logging.getLogger(__name__)

# ── Thresholds ────────────────────────────────────────────────────────────────
LOW_STOCK_RISK_THRESHOLD = 50         # stockout_risk_score ≥ this → LOW_STOCK alert
CRITICAL_RISK_THRESHOLD = 80          # stockout_risk_score ≥ this → CRITICAL
DEMAND_SURGE_MULTIPLIER = 1.5         # current demand > 1.5× 30-day avg → surge
SLA_BREACH_ALERT_HOURS = 24           # only SLA breaches created within last N hours
SUPPLIER_DELAY_HOURS = 120            # procurement_time > this → supplier delay alert
BOTTLENECK_MIN_COUNT = 3              # bottleneck stage must affect ≥ N orders
TODAY = date.today().isoformat()       # dedup date key


class NotificationService:
    def __init__(self, db: Session) -> None:
        self.db = db

    # ─────────────────────────────────────────────────────────────────────────
    # Main orchestrator
    # ─────────────────────────────────────────────────────────────────────────

    def run_all_checks(self) -> Dict[str, Any]:
        """
        Run every alert check and persist new notifications.
        Returns a summary of what was created.
        """
        created: List[Dict] = []

        created += self._generate_stock_alerts()
        created += self._generate_sla_breach_alerts()
        created += self._generate_bottleneck_alerts()
        created += self._generate_supplier_delay_alerts()
        created += self._generate_demand_surge_alerts()

        logger.info("Alert run completed: %d new notifications created.", len(created))
        return {
            "run_at": datetime.utcnow().isoformat(),
            "total_created": len(created),
            "notifications": created,
        }

    # ─────────────────────────────────────────────────────────────────────────
    # Alert generators
    # ─────────────────────────────────────────────────────────────────────────

    def _generate_stock_alerts(self) -> List[Dict]:
        """LOW_STOCK and REORDER_REQUIRED alerts from the recommendation engine."""
        recs_data = InventoryRecommendationService(self.db).get_all_recommendations()
        created = []

        for r in recs_data.get("recommendations", []):
            risk = r["stockout_risk_score"]
            if risk < LOW_STOCK_RISK_THRESHOLD:
                continue

            category = "REORDER_REQUIRED" if r["action"] == "REORDER_NOW" else "LOW_STOCK"
            priority = "CRITICAL" if risk >= CRITICAL_RISK_THRESHOLD else "HIGH"

            if r["available_stock"] <= 0:
                title = f"OUT OF STOCK: {r['product_name']}"
                msg = (
                    f"{r['product_name']} (SKU: {r['sku']}) has zero available stock. "
                    f"Immediate reorder of {r['reorder_quantity']} units required."
                )
            else:
                title = f"Low Stock Alert: {r['product_name']}"
                msg = (
                    f"{r['product_name']} (SKU: {r['sku']}) has only "
                    f"{r['available_stock']} units available — "
                    f"{r['days_of_stock_remaining']} days of stock remaining. "
                    f"Reorder {r['reorder_quantity']} units (est. value: "
                    f"${r['estimated_reorder_value']:,.2f})."
                )

            n = self._upsert(
                dedup_key=f"stock:{r['product_id']}:{TODAY}",
                category=category,
                priority=priority,
                title=title,
                message=msg,
                product_id=r["product_id"],
                product_name=r["product_name"],
                supplier_id=r.get("supplier_id"),
                metric_value=risk,
                metric_label="stockout_risk_score",
            )
            if n:
                created.append(_to_dict(n))

        return created

    def _generate_sla_breach_alerts(self) -> List[Dict]:
        """SLA_BREACH alerts for orders that breached SLA in the last 24 h."""
        cutoff = datetime.utcnow() - timedelta(hours=SLA_BREACH_ALERT_HOURS)
        breached = (
            self.db.query(Order)
            .filter(
                Order.sla_breach == True,
                Order.created_at >= cutoff,
            )
            .all()
        )

        created = []
        for order in breached:
            n = self._upsert(
                dedup_key=f"sla:{order.id}",
                category="SLA_BREACH",
                priority="HIGH",
                title=f"SLA Breach: Order #{order.order_number}",
                message=(
                    f"Order {order.order_number} breached SLA at the "
                    f"'{order.breached_stage}' stage. "
                    f"Total fulfilment time: {round(order.total_time or 0, 1)} h "
                    f"(bottleneck: {order.bottleneck_stage or 'unknown'})."
                ),
                order_id=order.id,
                product_id=order.product_id,
                metric_value=order.total_time,
                metric_label="total_time_hours",
            )
            if n:
                created.append(_to_dict(n))

        return created

    def _generate_bottleneck_alerts(self) -> List[Dict]:
        """BOTTLENECK alert when a single stage is the bottleneck for many orders."""
        rows = (
            self.db.query(
                Order.bottleneck_stage,
                func.count(Order.id).label("cnt"),
            )
            .filter(Order.bottleneck_stage.isnot(None))
            .group_by(Order.bottleneck_stage)
            .having(func.count(Order.id) >= BOTTLENECK_MIN_COUNT)
            .all()
        )

        created = []
        for row in rows:
            n = self._upsert(
                dedup_key=f"bottleneck:{row.bottleneck_stage}:{TODAY}",
                category="BOTTLENECK",
                priority="MEDIUM",
                title=f"Bottleneck Detected: {row.bottleneck_stage}",
                message=(
                    f"The '{row.bottleneck_stage}' stage is the bottleneck "
                    f"in {row.cnt} orders. Review capacity and resources "
                    f"assigned to this stage to reduce delays."
                ),
                metric_value=float(row.cnt),
                metric_label="affected_orders",
            )
            if n:
                created.append(_to_dict(n))

        return created

    def _generate_supplier_delay_alerts(self) -> List[Dict]:
        """SUPPLIER_DELAY alert when avg procurement time exceeds threshold."""
        rows = (
            self.db.query(
                Order.supplier_id,
                func.avg(Order.procurement_time).label("avg_proc"),
                func.count(Order.id).label("cnt"),
            )
            .filter(
                Order.supplier_id.isnot(None),
                Order.procurement_time.isnot(None),
            )
            .group_by(Order.supplier_id)
            .having(func.avg(Order.procurement_time) > SUPPLIER_DELAY_HOURS)
            .all()
        )

        # Build supplier name lookup
        supplier_ids = [r.supplier_id for r in rows]
        suppliers = {
            s.id: s
            for s in self.db.query(Supplier).filter(Supplier.id.in_(supplier_ids)).all()
        } if supplier_ids else {}

        created = []
        for row in rows:
            sup = suppliers.get(row.supplier_id)
            sup_name = sup.supplier_name if sup else f"Supplier #{row.supplier_id}"
            avg_hours = round(float(row.avg_proc), 1)

            n = self._upsert(
                dedup_key=f"supplier_delay:{row.supplier_id}:{TODAY}",
                category="SUPPLIER_DELAY",
                priority="HIGH" if avg_hours > SUPPLIER_DELAY_HOURS * 1.5 else "MEDIUM",
                title=f"Supplier Delay: {sup_name}",
                message=(
                    f"{sup_name} has an average procurement time of "
                    f"{avg_hours} hours across {row.cnt} orders "
                    f"(threshold: {SUPPLIER_DELAY_HOURS} h). "
                    f"Consider escalating or switching suppliers."
                ),
                supplier_id=row.supplier_id,
                metric_value=avg_hours,
                metric_label="avg_procurement_time_hours",
            )
            if n:
                created.append(_to_dict(n))

        return created

    def _generate_demand_surge_alerts(self) -> List[Dict]:
        """DEMAND_SURGE alert when recent 7-day demand far exceeds 30-day avg."""
        cutoff_30d = datetime.utcnow() - timedelta(days=30)
        cutoff_7d = datetime.utcnow() - timedelta(days=7)

        # 30-day avg per product
        avg_30 = dict(
            self.db.query(Order.product_id, func.avg(Order.quantity))
            .filter(Order.order_placed_at >= cutoff_30d)
            .group_by(Order.product_id)
            .all()
        )

        # 7-day avg per product
        avg_7 = dict(
            self.db.query(Order.product_id, func.avg(Order.quantity))
            .filter(Order.order_placed_at >= cutoff_7d)
            .group_by(Order.product_id)
            .all()
        )

        # Product name lookup
        pids = set(avg_30) | set(avg_7)
        products = {
            p.id: p
            for p in self.db.query(Product).filter(Product.id.in_(pids)).all()
        } if pids else {}

        created = []
        for pid, recent_avg in avg_7.items():
            base_avg = avg_30.get(pid)
            if not base_avg or base_avg <= 0:
                continue
            if float(recent_avg) >= float(base_avg) * DEMAND_SURGE_MULTIPLIER:
                product = products.get(pid)
                pname = product.product_name if product else f"Product #{pid}"
                surge_pct = round((float(recent_avg) / float(base_avg) - 1) * 100, 1)

                n = self._upsert(
                    dedup_key=f"surge:{pid}:{TODAY}",
                    category="DEMAND_SURGE",
                    priority="HIGH" if surge_pct > 100 else "MEDIUM",
                    title=f"Demand Surge: {pname}",
                    message=(
                        f"Demand for {pname} has surged {surge_pct}% above the "
                        f"30-day average over the last 7 days "
                        f"(7-day avg: {round(float(recent_avg), 1)} units/order vs "
                        f"30-day avg: {round(float(base_avg), 1)} units/order). "
                        f"Review stock levels and consider expediting replenishment."
                    ),
                    product_id=pid,
                    product_name=pname,
                    metric_value=surge_pct,
                    metric_label="demand_surge_pct",
                )
                if n:
                    created.append(_to_dict(n))

        return created

    # ─────────────────────────────────────────────────────────────────────────
    # CRUD helpers
    # ─────────────────────────────────────────────────────────────────────────

    def list_notifications(
        self,
        category: Optional[str] = None,
        priority: Optional[str] = None,
        is_read: Optional[bool] = None,
        is_resolved: Optional[bool] = None,
        skip: int = 0,
        limit: int = 50,
    ) -> Dict[str, Any]:
        q = self.db.query(Notification)
        if category:
            q = q.filter(Notification.category == category.upper())
        if priority:
            q = q.filter(Notification.priority == priority.upper())
        if is_read is not None:
            q = q.filter(Notification.is_read == is_read)
        if is_resolved is not None:
            q = q.filter(Notification.is_resolved == is_resolved)

        total = q.count()
        rows = q.order_by(Notification.created_at.desc()).offset(skip).limit(limit).all()

        return {
            "total": total,
            "skip": skip,
            "limit": limit,
            "notifications": [_to_dict(n) for n in rows],
        }

    def mark_read(self, notification_id: int) -> Optional[Dict]:
        n = self.db.query(Notification).filter(Notification.id == notification_id).first()
        if not n:
            return None
        n.is_read = True
        self.db.commit()
        self.db.refresh(n)
        return _to_dict(n)

    def mark_all_read(self) -> int:
        count = (
            self.db.query(Notification)
            .filter(Notification.is_read == False)
            .update({"is_read": True})
        )
        self.db.commit()
        return count

    def mark_resolved(self, notification_id: int) -> Optional[Dict]:
        n = self.db.query(Notification).filter(Notification.id == notification_id).first()
        if not n:
            return None
        n.is_resolved = True
        n.resolved_at = datetime.utcnow()
        self.db.commit()
        self.db.refresh(n)
        return _to_dict(n)

    def delete_notification(self, notification_id: int) -> bool:
        n = self.db.query(Notification).filter(Notification.id == notification_id).first()
        if not n:
            return False
        self.db.delete(n)
        self.db.commit()
        return True

    def get_summary(self) -> Dict[str, Any]:
        """Unread counts by category and priority — for dashboard badge."""
        rows = (
            self.db.query(
                Notification.category,
                Notification.priority,
                func.count(Notification.id).label("cnt"),
            )
            .filter(Notification.is_resolved == False)
            .group_by(Notification.category, Notification.priority)
            .all()
        )

        total_unread = (
            self.db.query(func.count(Notification.id))
            .filter(Notification.is_read == False, Notification.is_resolved == False)
            .scalar()
            or 0
        )

        by_category: Dict[str, int] = {}
        by_priority: Dict[str, int] = {}
        for r in rows:
            by_category[r.category] = by_category.get(r.category, 0) + r.cnt
            by_priority[r.priority] = by_priority.get(r.priority, 0) + r.cnt

        return {
            "total_active": sum(by_category.values()),
            "total_unread": total_unread,
            "by_category": by_category,
            "by_priority": by_priority,
        }

    # ─────────────────────────────────────────────────────────────────────────
    # Internal upsert (deduplication)
    # ─────────────────────────────────────────────────────────────────────────

    def _upsert(self, dedup_key: str, **kwargs) -> Optional[Notification]:
        """
        Create notification only if no record with this dedup_key exists.
        Returns the new Notification or None if skipped.
        """
        existing = (
            self.db.query(Notification)
            .filter(Notification.dedup_key == dedup_key)
            .first()
        )
        if existing:
            return None

        n = Notification(dedup_key=dedup_key, **kwargs)
        self.db.add(n)
        try:
            self.db.commit()
            self.db.refresh(n)
            logger.debug("Created notification [%s] %s", n.priority, n.title)
            return n
        except Exception as exc:
            self.db.rollback()
            logger.warning("Notification upsert failed for key=%s: %s", dedup_key, exc)
            return None


# ─────────────────────────────────────────────────────────────────────────────
# Serialisation helper
# ─────────────────────────────────────────────────────────────────────────────

def _to_dict(n: Notification) -> Dict[str, Any]:
    return {
        "id": n.id,
        "category": n.category,
        "priority": n.priority,
        "title": n.title,
        "message": n.message,
        "product_id": n.product_id,
        "product_name": n.product_name,
        "supplier_id": n.supplier_id,
        "order_id": n.order_id,
        "metric_value": n.metric_value,
        "metric_label": n.metric_label,
        "is_read": n.is_read,
        "is_resolved": n.is_resolved,
        "resolved_at": n.resolved_at.isoformat() if n.resolved_at else None,
        "created_at": n.created_at.isoformat() if n.created_at else None,
    }
