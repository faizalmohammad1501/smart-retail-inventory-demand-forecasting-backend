"""
PDF Export Service
===================
Generates PDF reports using fpdf2 (pure-Python, no system dependencies).
Falls back gracefully if fpdf2 is not installed.

Install:  pip install fpdf2
"""

from datetime import datetime, timezone
from io import BytesIO
from typing import Any, Dict, List, Optional


def _now_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


# ─────────────────────────────────────────────────────────────────────────────
#  Base PDF builder
# ─────────────────────────────────────────────────────────────────────────────

class _ReportPDF:
    """Thin wrapper around FPDF2 with branded header/footer."""

    def __init__(self, title: str):
        try:
            from fpdf import FPDF
        except ImportError as exc:
            raise RuntimeError(
                "fpdf2 is required for PDF exports. "
                "Install it with: pip install fpdf2"
            ) from exc

        self.pdf = FPDF(orientation="L", unit="mm", format="A4")
        self.pdf.set_auto_page_break(auto=True, margin=15)
        self.pdf.set_margins(15, 15, 15)
        self.title = title
        self._add_cover()

    # ── Formatting helpers ──────────────────────────────────────────────────

    def _header(self):
        self.pdf.set_fill_color(30, 64, 175)  # brand blue
        self.pdf.rect(0, 0, 297, 18, "F")
        self.pdf.set_font("Helvetica", "B", 11)
        self.pdf.set_text_color(255, 255, 255)
        self.pdf.set_xy(15, 4)
        self.pdf.cell(0, 10, f"Smart Retail Platform  |  {self.title}", ln=0)
        self.pdf.set_xy(220, 4)
        self.pdf.cell(0, 10, _now_str(), ln=0, align="R")
        self.pdf.set_text_color(0, 0, 0)

    def _footer(self):
        self.pdf.set_y(-12)
        self.pdf.set_font("Helvetica", "I", 8)
        self.pdf.set_text_color(120, 120, 120)
        self.pdf.cell(0, 10, f"Page {self.pdf.page_no()}  |  Confidential", align="C")
        self.pdf.set_text_color(0, 0, 0)

    def _new_page(self):
        self.pdf.add_page()
        self._header()
        self.pdf.ln(12)

    def _add_cover(self):
        self.pdf.add_page()
        self._header()
        self.pdf.ln(30)
        self.pdf.set_font("Helvetica", "B", 28)
        self.pdf.set_text_color(30, 64, 175)
        self.pdf.cell(0, 14, "Smart Retail Platform", ln=True, align="C")
        self.pdf.set_font("Helvetica", "B", 18)
        self.pdf.set_text_color(55, 65, 81)
        self.pdf.cell(0, 10, self.title, ln=True, align="C")
        self.pdf.ln(4)
        self.pdf.set_font("Helvetica", "", 11)
        self.pdf.set_text_color(107, 114, 128)
        self.pdf.cell(0, 8, f"Generated: {_now_str()}", ln=True, align="C")
        self._footer()

    # ── Section heading ─────────────────────────────────────────────────────

    def section(self, heading: str):
        self.pdf.ln(4)
        self.pdf.set_fill_color(239, 246, 255)
        self.pdf.set_font("Helvetica", "B", 12)
        self.pdf.set_text_color(30, 64, 175)
        self.pdf.cell(0, 8, f"  {heading}", ln=True, fill=True)
        self.pdf.set_text_color(0, 0, 0)
        self.pdf.ln(2)

    # ── KPI cards row ───────────────────────────────────────────────────────

    def kpi_row(self, kpis: List[Dict[str, Any]]):
        """Render up to 5 KPI boxes in a single row."""
        n = min(len(kpis), 5)
        col_w = 250 / n
        self.pdf.set_font("Helvetica", "B", 9)
        for i, kpi in enumerate(kpis[:n]):
            x = 15 + i * col_w
            self.pdf.set_xy(x, self.pdf.get_y())
            self.pdf.set_fill_color(249, 250, 251)
            self.pdf.rect(x, self.pdf.get_y(), col_w - 3, 18, "FD")
            self.pdf.set_xy(x + 2, self.pdf.get_y() + 2)
            self.pdf.set_text_color(107, 114, 128)
            self.pdf.set_font("Helvetica", "", 7)
            self.pdf.cell(col_w - 5, 4, str(kpi.get("label", "")), ln=True)
            self.pdf.set_xy(x + 2, self.pdf.get_y())
            self.pdf.set_font("Helvetica", "B", 11)
            self.pdf.set_text_color(17, 24, 39)
            self.pdf.cell(col_w - 5, 7, str(kpi.get("value", "")), ln=True)
            if kpi.get("change") is not None:
                self.pdf.set_xy(x + 2, self.pdf.get_y())
                chg = kpi["change"]
                self.pdf.set_text_color(34, 197, 94) if chg >= 0 else self.pdf.set_text_color(239, 68, 68)
                self.pdf.set_font("Helvetica", "", 7)
                arrow = "▲" if chg >= 0 else "▼"
                self.pdf.cell(col_w - 5, 4, f"{arrow} {abs(chg):.1f}% vs prior", ln=True)
        self.pdf.ln(22)
        self.pdf.set_text_color(0, 0, 0)

    # ── Table ───────────────────────────────────────────────────────────────

    def table(
        self,
        headers: List[str],
        rows: List[List[Any]],
        col_widths: Optional[List[float]] = None,
        max_rows: int = 50,
    ):
        """Render a data table with alternating row shading."""
        if not col_widths:
            total_w = 257.0
            col_widths = [total_w / len(headers)] * len(headers)

        # Header row
        self.pdf.set_fill_color(30, 64, 175)
        self.pdf.set_text_color(255, 255, 255)
        self.pdf.set_font("Helvetica", "B", 8)
        for i, h in enumerate(headers):
            self.pdf.cell(col_widths[i], 7, str(h), border=0, fill=True, align="C")
        self.pdf.ln()

        # Data rows
        self.pdf.set_font("Helvetica", "", 8)
        for idx, row in enumerate(rows[:max_rows]):
            if self.pdf.get_y() > 180:
                self._footer()
                self._new_page()
            # Alternating shade
            if idx % 2 == 0:
                self.pdf.set_fill_color(249, 250, 251)
            else:
                self.pdf.set_fill_color(255, 255, 255)
            self.pdf.set_text_color(31, 41, 55)
            for i, cell in enumerate(row):
                val = "" if cell is None else str(cell)
                self.pdf.cell(col_widths[i], 6, val, border=0, fill=True, align="C")
            self.pdf.ln()

        self.pdf.set_text_color(0, 0, 0)
        self.pdf.ln(3)

    # ── Output ──────────────────────────────────────────────────────────────

    def output_bytes(self) -> bytes:
        self._footer()
        buf = BytesIO()
        buf.write(self.pdf.output())
        buf.seek(0)
        return buf.read()


# ─────────────────────────────────────────────────────────────────────────────
#  Public PDF builders
# ─────────────────────────────────────────────────────────────────────────────

def build_sales_pdf(data: Dict[str, Any]) -> bytes:
    """Generate a Sales Summary PDF report."""
    doc = _ReportPDF("Sales Report")
    doc._new_page()

    s = data.get("sales_summary", {})
    doc.section("Executive Sales Summary")
    doc.kpi_row([
        {"label": "Total Revenue", "value": f"${s.get('total_revenue', 0):,.2f}"},
        {"label": "Total Orders", "value": str(s.get("total_orders", 0))},
        {"label": "Avg Order Value", "value": f"${s.get('avg_order_value', 0):,.2f}"},
        {"label": "Fulfillment Rate", "value": f"{s.get('fulfillment_rate', 0):.1f}%"},
        {"label": "Cancellation Rate", "value": f"{s.get('cancellation_rate', 0):.1f}%"},
    ])

    # Revenue trends table
    trends = data.get("revenue_trends", {}).get("data", [])
    if trends:
        doc.section("Revenue Trends (Monthly)")
        doc.table(
            headers=["Period", "Orders", "Revenue", "Units Sold", "Avg Order Value", "Fulfillment %"],
            rows=[
                [
                    t["period"],
                    t["orders"],
                    f"${t['revenue']:,.2f}",
                    t.get("units_sold", 0),
                    f"${t.get('avg_order_value', 0):,.2f}",
                    f"{t.get('fulfillment_rate', 0):.1f}%",
                ]
                for t in trends
            ],
            col_widths=[40, 28, 45, 32, 48, 40],
        )

    # Top products table
    products = data.get("top_products", {}).get("products", [])
    if products:
        doc.section("Top 10 Products by Revenue")
        doc.table(
            headers=["Product", "SKU", "Category", "Orders", "Units", "Revenue", "Share %"],
            rows=[
                [
                    p["product_name"][:25],
                    p["sku"],
                    p.get("category") or "-",
                    p["total_orders"],
                    p["total_units_sold"],
                    f"${p['total_revenue']:,.2f}",
                    f"{p['revenue_share_pct']:.1f}%",
                ]
                for p in products
            ],
            col_widths=[52, 30, 30, 22, 22, 42, 28],
        )

    # Category breakdown
    cats = data.get("category_revenue", {}).get("categories", [])
    if cats:
        doc.section("Revenue by Category")
        doc.table(
            headers=["Category", "Orders", "Units", "Revenue", "Avg Order", "Share %"],
            rows=[
                [
                    c["category"],
                    c["total_orders"],
                    c["total_units_sold"],
                    f"${c['total_revenue']:,.2f}",
                    f"${c['avg_order_value']:,.2f}",
                    f"{c['revenue_share_pct']:.1f}%",
                ]
                for c in cats
            ],
            col_widths=[45, 28, 28, 48, 48, 30],
        )

    return doc.output_bytes()


def build_inventory_pdf(data: Dict[str, Any]) -> bytes:
    """Generate an Inventory Valuation PDF report."""
    doc = _ReportPDF("Inventory Report")
    doc._new_page()

    inv = data.get("inventory_valuation", {})
    doc.section("Inventory Valuation Summary")
    doc.kpi_row([
        {"label": "Total SKUs", "value": str(inv.get("total_sku_count", 0))},
        {"label": "Available Value", "value": f"${inv.get('total_available_value', 0):,.2f}"},
        {"label": "Reserved Value", "value": f"${inv.get('total_reserved_value', 0):,.2f}"},
        {"label": "Grand Total Value", "value": f"${inv.get('grand_total_value', 0):,.2f}"},
    ])

    # By-category
    by_cat = inv.get("by_category", [])
    if by_cat:
        doc.section("Valuation by Category")
        doc.table(
            headers=["Category", "Total Value", "Share %"],
            rows=[
                [c["category"], f"${c['total_value']:,.2f}", f"{c['value_share_pct']:.1f}%"]
                for c in by_cat
            ],
            col_widths=[100, 80, 60],
        )

    # SKU detail
    items = inv.get("items", [])
    if items:
        doc.section("SKU-Level Detail (Top 50 by Value)")
        sorted_items = sorted(items, key=lambda x: x["total_value"], reverse=True)
        doc.table(
            headers=["Product", "SKU", "Category", "Unit Price", "Available", "Reserved", "Total Value"],
            rows=[
                [
                    i["product_name"][:25],
                    i["sku"],
                    i.get("category") or "-",
                    f"${i['unit_price']:,.2f}",
                    i["quantity_available"],
                    i["quantity_reserved"],
                    f"${i['total_value']:,.2f}",
                ]
                for i in sorted_items[:50]
            ],
            col_widths=[52, 28, 28, 32, 28, 28, 45],
        )

    return doc.output_bytes()


def build_supplier_pdf(data: Dict[str, Any]) -> bytes:
    """Generate a Supplier Performance PDF report."""
    doc = _ReportPDF("Supplier Performance Report")
    doc._new_page()

    sup = data.get("supplier_performance", {})
    suppliers = sup.get("suppliers", [])

    doc.section("Supplier Performance Overview")
    if suppliers:
        avg_score = sum(s.get("performance_score", 0) for s in suppliers) / len(suppliers)
        avg_sla = sum(s.get("sla_compliance_rate", 0) for s in suppliers) / len(suppliers)
        doc.kpi_row([
            {"label": "Total Suppliers", "value": str(len(suppliers))},
            {"label": "Avg Performance Score", "value": f"{avg_score:.1f}/100"},
            {"label": "Avg SLA Compliance", "value": f"{avg_sla:.1f}%"},
        ])

        doc.section("Supplier Scorecard Table")
        doc.table(
            headers=[
                "Supplier", "Orders", "Delivered", "Revenue",
                "Avg Lead Time (h)", "SLA Compliance", "On-Time %", "Score",
            ],
            rows=[
                [
                    s["supplier_name"][:22],
                    s["total_orders"],
                    s["delivered_orders"],
                    f"${s['total_revenue']:,.2f}",
                    f"{s['avg_lead_time_hours']:.1f}",
                    f"{s['sla_compliance_rate']:.1f}%",
                    f"{s['on_time_delivery_rate']:.1f}%",
                    f"{s['performance_score']:.1f}",
                ]
                for s in suppliers
            ],
            col_widths=[42, 22, 25, 42, 36, 36, 26, 22],
        )

    return doc.output_bytes()


def build_executive_pdf(data: Dict[str, Any]) -> bytes:
    """Full executive summary PDF combining all sections."""
    doc = _ReportPDF("Executive Business Report")
    days = data.get("period_days", 30)

    # ── Page 1: Executive KPIs ───────────────────────────────────────────────
    doc._new_page()
    ops = data.get("operational_kpis", {})
    s_kpis = ops.get("sales_kpis", {})
    inv_kpis = ops.get("inventory_kpis", {})
    sla_kpis = ops.get("sla_kpis", {})

    doc.section(f"Executive Summary — Last {days} Days")
    doc.kpi_row([
        {"label": "Total Revenue", "value": f"${s_kpis.get('total_revenue', 0):,.2f}"},
        {"label": "Total Orders", "value": str(s_kpis.get("total_orders", 0))},
        {"label": "Fulfillment Rate", "value": f"{s_kpis.get('fulfillment_rate', 0):.1f}%"},
        {"label": "Inventory Value", "value": f"${inv_kpis.get('total_inventory_value', 0):,.2f}"},
        {"label": "SLA Compliance", "value": f"{sla_kpis.get('sla_compliance_rate', 0):.1f}%"},
    ])

    doc.kpi_row([
        {"label": "Avg Order Value", "value": f"${s_kpis.get('avg_order_value', 0):,.2f}"},
        {"label": "Units Sold", "value": str(s_kpis.get("total_units_sold", 0))},
        {"label": "Out-of-Stock SKUs", "value": str(inv_kpis.get("out_of_stock_count", 0))},
        {"label": "Total SKUs", "value": str(inv_kpis.get("total_skus", 0))},
        {"label": "SLA Breaches", "value": str(sla_kpis.get("sla_breaches", 0))},
    ])

    # ── Sales section ────────────────────────────────────────────────────────
    sales_data = data.get("sales_summary", {})
    doc.section("Sales Summary")
    doc.table(
        headers=["Metric", "Value"],
        rows=[
            ["Total Revenue", f"${sales_data.get('total_revenue', 0):,.2f}"],
            ["Total Orders", str(sales_data.get("total_orders", 0))],
            ["Delivered Orders", str(sales_data.get("delivered_orders", 0))],
            ["Cancelled Orders", str(sales_data.get("cancelled_orders", 0))],
            ["Avg Order Value", f"${sales_data.get('avg_order_value', 0):,.2f}"],
            ["Fulfillment Rate", f"{sales_data.get('fulfillment_rate', 0):.2f}%"],
            ["Cancellation Rate", f"{sales_data.get('cancellation_rate', 0):.2f}%"],
            ["Total Units Sold", str(sales_data.get("total_units_sold", 0))],
        ],
        col_widths=[100, 150],
        max_rows=20,
    )

    # ── Top products ─────────────────────────────────────────────────────────
    products = data.get("top_products", {}).get("products", [])
    if products:
        doc.section("Top 10 Products by Revenue")
        doc.table(
            headers=["Product", "SKU", "Category", "Revenue", "Units", "Share %"],
            rows=[
                [
                    p["product_name"][:28],
                    p["sku"],
                    p.get("category") or "-",
                    f"${p['total_revenue']:,.2f}",
                    p["total_units_sold"],
                    f"{p['revenue_share_pct']:.1f}%",
                ]
                for p in products
            ],
            col_widths=[58, 30, 32, 52, 30, 30],
        )

    # ── Supplier performance ─────────────────────────────────────────────────
    suppliers = data.get("supplier_performance", {}).get("suppliers", [])
    if suppliers:
        doc.section("Supplier Performance")
        doc.table(
            headers=["Supplier", "Orders", "Revenue", "SLA Compliance", "On-Time %", "Score"],
            rows=[
                [
                    s["supplier_name"][:28],
                    s["total_orders"],
                    f"${s['total_revenue']:,.2f}",
                    f"{s['sla_compliance_rate']:.1f}%",
                    f"{s['on_time_delivery_rate']:.1f}%",
                    f"{s['performance_score']:.1f}",
                ]
                for s in suppliers[:20]
            ],
            col_widths=[58, 25, 50, 42, 38, 28],
        )

    # ── Forecast accuracy ────────────────────────────────────────────────────
    forecast = data.get("forecast_accuracy", [])
    if forecast:
        doc.section("Demand Forecast Accuracy (Top 20 by Accuracy)")
        sorted_fc = sorted(forecast, key=lambda x: x.get("accuracy_pct", 0), reverse=True)
        doc.table(
            headers=["Product", "SKU", "Actual Demand", "Predicted", "MAPE %", "Accuracy %"],
            rows=[
                [
                    f["product_name"][:28],
                    f["sku"],
                    f"{f['actual_demand']:,.0f}",
                    f"{f['predicted_demand']:,.0f}",
                    f"{f['mape_pct']:.1f}%",
                    f"{f['accuracy_pct']:.1f}%",
                ]
                for f in sorted_fc[:20]
            ],
            col_widths=[58, 28, 38, 38, 35, 38],
        )

    return doc.output_bytes()
