"""What a repair cost us, and what it earned.

The calculation is unchanged from the Sales Order version -- parts at purchase
cost, labour at the technician's hourly cost to company, margin against what the
customer was charged. What changed is where it reads from and where it lands.

Every input was already the request's: Spare Parts Usage is keyed on the service
request, and Job Assignment carries the request as well as the order. The order
supplied only the revenue figure, and it supplied the weaker one -- the quote.
The request knows the approved estimate before billing and the invoiced total
after, so the margin is provisional while the job is open and becomes real the
moment the customer is billed.

Recomputed at two moments: QC pass (the work is finished and costed) and every
invoice registration (what was actually charged is now known).
"""

import frappe
from frappe.utils import flt

from gofix.config import get_int_setting


def _as_doc(sr):
    return frappe.get_doc("Service Request", sr) if isinstance(sr, str) else sr


def actual_revenue(sr) -> float:
    """What the customer was charged, or is expected to be.

    Sums every submitted invoice raised against the repair, so additional work
    billed after a reopen counts towards the margin instead of being invisible.
    Falls back to the approved estimate while the repair is still unbilled.
    """
    sr = _as_doc(sr)
    names = [r.invoice for r in (sr.get("service_invoices") or []) if r.invoice]
    if sr.get("service_invoice") and sr.service_invoice not in names:
        names.append(sr.service_invoice)

    if names:
        billed = frappe.db.sql("""
            SELECT COALESCE(SUM(grand_total), 0) FROM `tabSales Invoice`
            WHERE name IN %(names)s AND docstatus = 1
        """, {"names": tuple(names)})[0][0]
        if flt(billed):
            return flt(billed)

    return flt(sr.get("estimated_cost"))


def _labour_cost(sr_name) -> float:
    """Finished Job Assignment hours priced at each technician's hourly CTC."""
    sheets = frappe.get_all(
        "Job Assignment",
        filters={"service_request": sr_name,
                 "assignment_status": ["in", ["Completed", "Closed"]]},
        fields=["actual_hours", "service_engineer"])

    engineers = tuple({s.service_engineer for s in sheets if s.service_engineer})
    if not engineers:
        return 0.0

    annual_hours = get_int_setting("annual_working_hours", 2080)
    fields = ["name", "ctc"]
    has_rate = frappe.db.has_column("Employee", "custom_hourly_rate")
    if has_rate:
        fields.append("custom_hourly_rate")

    rates = {}
    for emp in frappe.get_all("Employee", filters={"name": ("in", engineers)},
                              fields=fields, limit_page_length=len(engineers)):
        rate = flt(emp.get("custom_hourly_rate")) if has_rate else 0
        if not rate and flt(emp.ctc):
            rate = flt(emp.ctc) / annual_hours
        rates[emp.name] = rate

    return sum(flt(s.actual_hours) * rates.get(s.service_engineer, 0) for s in sheets)


def update_service_costing(sr) -> dict:
    """Recompute and store the repair's cost, revenue and margin."""
    sr = _as_doc(sr)
    if not sr.meta.get_field("total_repair_cost"):
        return {}

    parts = frappe.db.sql("""
        SELECT COALESCE(SUM(purchase_cost * qty_used), 0) AS cost,
               COALESCE(SUM(sales_price   * qty_used), 0) AS revenue
        FROM `tabSpare Parts Usage`
        WHERE service_request = %s
          AND status = 'Active'
          AND part_status IN ('Consumed', 'Issued')
    """, (sr.name,), as_dict=True)[0]

    damage = frappe.db.sql("""
        SELECT COALESCE(SUM(purchase_cost * qty_used), 0) AS cost
        FROM `tabSpare Parts Usage`
        WHERE service_request = %s
          AND is_defective = 1
          AND defect_type = 'Installation Damage'
    """, (sr.name,), as_dict=True)[0]

    suggested_labour = _labour_cost(sr.name)
    labour = flt(sr.get("labor_cost")) or suggested_labour
    total_cost = flt(parts.cost) + labour
    revenue = actual_revenue(sr)
    margin = revenue - total_cost
    suggested_total = flt(parts.revenue) + suggested_labour

    updates = {
        "spare_parts_cost": flt(parts.cost),
        "spare_parts_revenue": flt(parts.revenue),
        "suggested_labor_cost": suggested_labour,
        "total_repair_cost": total_cost,
        "technician_damage_cost": flt(damage.cost),
        "actual_billed": revenue,
        "suggested_total_cost": suggested_total,
        "price_override_amount": (revenue - suggested_total) if suggested_total else 0,
        "repair_margin": margin,
        "repair_margin_pct": (margin / revenue * 100) if revenue else 0,
    }
    if not flt(sr.get("labor_cost")) and suggested_labour:
        updates["labor_cost"] = suggested_labour

    # Who took the price away from the suggestion. Recorded once, on the first
    # material deviation, so the trail names the person who made the call.
    if abs(updates["price_override_amount"]) > 1 and not sr.get("price_overridden_by"):
        updates["price_overridden_by"] = frappe.session.user

    sr.flags.ignore_billing_lock = True
    sr.db_set(updates, update_modified=False)
    _mirror(sr, updates)
    return updates


def _mirror(sr, updates) -> None:
    """Keep a legacy Sales Order's costing in step. Never fails the caller."""
    order = sr.get("service_order")
    if not order:
        return
    meta = frappe.get_meta("Sales Order")
    payload = {k: v for k, v in updates.items()
               if meta.get_field(k) and k != "actual_billed"}
    if not payload:
        return
    try:
        frappe.db.set_value("Sales Order", order, payload, update_modified=False)
    except Exception:
        frappe.log_error(frappe.get_traceback(), f"costing: could not mirror onto {order}")
