"""Service Hub – Backend API for the unified service dashboard."""

import frappe
from frappe import _
from frappe.utils import flt, nowdate, get_first_day, cint, getdate, date_diff

# Import scope-aware filter builder (H7)
try:
    from ch_erp15.ch_erp15.scope import intersect_filters
except ImportError:
    # Fallback if ch_erp15 not available (unrestricted mode)
    def intersect_filters(**kwargs):
        return {
            "company": kwargs.get("company"),
            "store": kwargs.get("store"),
            "allowed_stores": None,
            "allowed_warehouses": None,
        }


def _build_filters(company=None, store=None, from_date=None, to_date=None):
    # SECURITY (H7): Enforce user's company/store scope
    eff = intersect_filters(company=company, store=store)
    company = eff["company"]
    store = eff["store"]
    allowed_stores = eff["allowed_stores"]  # None = unrestricted, [] = blocked, [list] = restricted

    prm = {}
    co = ""
    if company:
        co = " AND sr.company = %(company)s"
        prm["company"] = company
    wh = ""
    if store:
        wh = " AND sr.source_warehouse = %(store)s"
        prm["store"] = store
    elif allowed_stores is not None:
        # User has scope restrictions and no explicit store
        if not allowed_stores:
            # User has no accessible stores
            wh = " AND 1=0"
        else:
            # Restrict to user's allowed stores
            st_in = "(" + ", ".join(frappe.db.escape(s) for s in allowed_stores) + ")"
            wh = f" AND sr.source_warehouse IN {st_in}"

    from_date = str(getdate(from_date)) if from_date else None
    to_date = str(getdate(to_date)) if to_date else None
    if from_date:
        prm["from_date"] = from_date
    if to_date:
        prm["to_date"] = to_date

    def date_col(col):
        if from_date and to_date:
            return f" AND {col} BETWEEN %(from_date)s AND %(to_date)s"
        if from_date:
            return f" AND {col} >= %(from_date)s"
        if to_date:
            return f" AND {col} <= %(to_date)s"
        return ""

    return {"prm": prm, "co": co, "wh": wh, "date_col": date_col}


@frappe.whitelist()
def get_service_hub_data(company=None, store=None, from_date=None, to_date=None):
    """Service lifecycle dashboard: Intake → Accepted → In Service → Completed → Delivered → Invoiced."""
    f = _build_filters(company, store, from_date, to_date)
    prm = f["prm"]
    co = f["co"]
    wh = f["wh"]
    dc = f["date_col"]

    today = nowdate()
    first_day = get_first_day(today)
    prm["today"] = today
    prm["first_day"] = str(first_day)

    # ── Pipeline counts by decision ──
    status_counts = frappe.db.sql(
        f"""SELECT sr.decision, COUNT(*) AS cnt
            FROM `tabService Request` sr
            WHERE sr.docstatus < 2 {co} {wh} {dc('sr.creation')}
            GROUP BY sr.decision""", prm, as_dict=True
    )
    sc = {r.decision: cint(r.cnt) for r in status_counts}

    # Count Not Repairable / BER separately — use modified date (when NR was set)
    nr_count_r = frappe.db.sql(
        f"""SELECT COUNT(*) FROM `tabService Request` sr
            WHERE sr.docstatus < 2 AND sr.decision = 'Rejected'
            AND sr.repairability_status IN ('Not Repairable','BER')
            {co} {wh} {dc('sr.modified')}""", prm
    )
    nr_count = cint(nr_count_r[0][0]) if nr_count_r else 0
    other_rejected = sc.get("Rejected", 0) - nr_count

    pipeline = [
        {"key": "draft",       "label": "New / Draft",     "count": sc.get("Draft", 0),
         "icon": "inbox",       "color": "#94a3b8",  "sub": "Awaiting review"},
        {"key": "accepted",    "label": "Accepted",        "count": sc.get("Accepted", 0),
         "icon": "check",       "color": "#3b82f6",  "sub": "Ready to service"},
        {"key": "in_service",  "label": "In Service",      "count": sc.get("In Service", 0),
         "icon": "cogs",        "color": "#7c3aed",  "sub": "Under repair"},
        {"key": "completed",   "label": "Completed",       "count": sc.get("Completed", 0),
         "icon": "check-circle","color": "#059669",  "sub": "QC passed"},
        {"key": "not_repairable", "label": "Not Repairable", "count": nr_count,
         "icon": "ban",         "color": "#dc2626",  "sub": "NR / BER"},
        {"key": "delivered",   "label": "Delivered",        "count": sc.get("Delivered", 0),
         "icon": "truck",       "color": "#0ea5e9",  "sub": "Handed over"},
        {"key": "invoiced",    "label": "Invoiced",         "count": sc.get("Invoiced", 0),
         "icon": "file-text",   "color": "#10b981",  "sub": "Billed"},
    ]

    # ── KPIs ──
    total_active = sc.get("Draft", 0) + sc.get("Accepted", 0) + sc.get("In Service", 0)
    total_completed = sc.get("Completed", 0) + sc.get("Delivered", 0) + sc.get("Invoiced", 0)
    total_all = sum(sc.values())

    today_intake = frappe.db.sql(
        f"""SELECT COUNT(*) FROM `tabService Request` sr
            WHERE DATE(sr.creation) = %(today)s {co} {wh}""", prm
    )[0][0]

    overdue_count = frappe.db.sql(
        f"""SELECT COUNT(*) FROM `tabService Request` sr
            WHERE sr.expected_completion_date < %(today)s
            AND sr.decision IN ('Draft','Accepted','In Service')
            {co} {wh}""", prm
    )[0][0]

    avg_tat_r = frappe.db.sql(
        f"""SELECT AVG(DATEDIFF(sr.modified, sr.creation)) AS avg_tat
            FROM `tabService Request` sr
            WHERE sr.decision IN ('Completed','Delivered','Invoiced')
            {co} {wh} {dc('sr.creation')}""", prm
    )
    avg_tat = round(flt(avg_tat_r[0][0]), 1) if avg_tat_r and avg_tat_r[0][0] else 0

    # Estimated service revenue (from estimated_cost on completed SRs)
    est_revenue = frappe.db.sql(
        f"""SELECT COALESCE(SUM(sr.estimated_cost), 0) FROM `tabService Request` sr
            WHERE sr.decision IN ('Completed','Delivered','Invoiced')
            AND sr.creation BETWEEN %(first_day)s AND %(today)s
            {co} {wh}""", prm
    )[0][0]

    kpis = [
        {"key": "intake_today", "label": "Intake Today",       "value": cint(today_intake),   "color": "#6366f1", "fmt": "number"},
        {"key": "active",       "label": "Active Jobs",        "value": total_active,          "color": "#7c3aed", "fmt": "number"},
        {"key": "completed",    "label": "Completed (period)", "value": total_completed,       "color": "#059669", "fmt": "number"},
        {"key": "overdue",      "label": "Overdue",            "value": cint(overdue_count),   "color": "#ef4444", "fmt": "number"},
        {"key": "avg_tat",      "label": "Avg TAT (days)",     "value": avg_tat,               "color": "#f59e0b", "fmt": "number"},
        {"key": "est_rev",      "label": "Est. Revenue MTD",   "value": flt(est_revenue),      "color": "#10b981", "fmt": "currency"},
        {"key": "total",        "label": "Total SRs",          "value": total_all,             "color": "#0ea5e9", "fmt": "number"},
        {"key": "not_repairable", "label": "Not Repairable/BER", "value": nr_count,            "color": "#dc2626", "fmt": "number"},
        {"key": "cancelled",    "label": "Cancelled/Other",    "value": sc.get("Cancelled", 0) + other_rejected, "color": "#9ca3af", "fmt": "number"},
    ]

    # ── Detail tables ──
    pending_intake = frappe.db.sql(
        f"""SELECT sr.name, sr.customer_name, sr.customer, sr.device_item,
                   sr.device_item_name,
                   sr.issue_category, sr.priority, sr.creation, sr.decision, sr.status
            FROM `tabService Request` sr
            WHERE sr.decision = 'Draft' {co} {wh} {dc('sr.creation')}
            ORDER BY sr.creation DESC LIMIT 50""", prm, as_dict=True
    )

    in_service = frappe.db.sql(
        f"""SELECT sr.name, sr.customer_name, sr.customer, sr.device_item,
                   sr.device_item_name,
                   sr.brand, sr.decision, sr.status,
                   DATEDIFF(%(today)s, sr.creation) AS days_in_service,
                   (SELECT ja.service_engineer FROM `tabJob Assignment` ja
                    WHERE ja.service_request = sr.name ORDER BY ja.creation DESC LIMIT 1) AS technician
            FROM `tabService Request` sr
            WHERE sr.decision = 'In Service' {co} {wh}
            ORDER BY sr.creation ASC LIMIT 50""", prm, as_dict=True
    )

    ready_delivery = frappe.db.sql(
        f"""SELECT sr.name, sr.customer_name, sr.customer, sr.device_item,
                   sr.device_item_name,
                   sr.modified AS completed_on, sr.estimated_cost
            FROM `tabService Request` sr
            WHERE sr.decision = 'Completed' {co} {wh}
            ORDER BY sr.modified DESC LIMIT 50""", prm, as_dict=True
    )

    overdue = frappe.db.sql(
        f"""SELECT sr.name, sr.customer_name, sr.customer, sr.device_item,
                   sr.device_item_name,
                   sr.expected_completion_date, sr.decision, sr.status,
                   DATEDIFF(%(today)s, sr.expected_completion_date) AS days_overdue
            FROM `tabService Request` sr
            WHERE sr.expected_completion_date < %(today)s
            AND sr.decision IN ('Draft','Accepted','In Service')
            {co} {wh}
            ORDER BY sr.expected_completion_date ASC LIMIT 50""", prm, as_dict=True
    )

    # Technician workload
    technician_load = frappe.db.sql(
        f"""SELECT
                ja.service_engineer AS technician,
                SUM(CASE WHEN sr.decision IN ('Accepted','In Service') THEN 1 ELSE 0 END) AS active_jobs,
                SUM(CASE WHEN sr.decision IN ('Completed','Delivered','Invoiced') THEN 1 ELSE 0 END) AS completed,
                ROUND(AVG(CASE WHEN sr.decision IN ('Completed','Delivered','Invoiced')
                    THEN DATEDIFF(sr.modified, sr.creation) END), 1) AS avg_tat
            FROM `tabJob Assignment` ja
            JOIN `tabService Request` sr ON sr.name = ja.service_request
            WHERE ja.docstatus < 2 {co} {wh} {dc('sr.creation')}
            GROUP BY ja.service_engineer
            ORDER BY active_jobs DESC LIMIT 20""", prm, as_dict=True
    )

    # Not Repairable detail table
    not_repairable = frappe.db.sql(
        f"""SELECT sr.name, sr.customer_name, sr.customer, sr.device_item,
                   sr.device_item_name,
                   sr.repairability_status, sr.rejection_reason,
                   sr.modified AS rejected_on,
                   (SELECT COUNT(*) FROM `tabSpare Parts Usage` spu
                    WHERE spu.service_request = sr.name AND spu.part_status = 'Consumed'
                    AND spu.deleted = 0 AND spu.status = 'Active') AS pending_spares
            FROM `tabService Request` sr
            WHERE sr.decision = 'Rejected'
            AND sr.repairability_status IN ('Not Repairable','BER')
            {co} {wh} {dc('sr.modified')}
            ORDER BY sr.modified DESC LIMIT 50""", prm, as_dict=True
    )

    # Issue category breakdown
    issue_breakdown = frappe.db.sql(
        f"""SELECT sr.issue_category,
                   COUNT(*) AS total,
                   SUM(CASE WHEN sr.decision IN ('Draft','Accepted','In Service') THEN 1 ELSE 0 END) AS active,
                   SUM(CASE WHEN sr.decision IN ('Completed','Delivered','Invoiced') THEN 1 ELSE 0 END) AS completed
            FROM `tabService Request` sr
            WHERE sr.issue_category IS NOT NULL AND sr.issue_category != ''
            {co} {wh} {dc('sr.creation')}
            GROUP BY sr.issue_category
            ORDER BY total DESC LIMIT 20""", prm, as_dict=True
    )

    # ── AI Insights ──
    ai_insights = []
    if cint(overdue_count) > 3:
        ai_insights.append({
            "severity": "High", "title": f"{cint(overdue_count)} Overdue Service Requests",
            "detail": "Multiple SRs past expected completion date. Customer satisfaction at risk.",
            "action": "Prioritize overdue jobs and communicate updated timelines to customers."
        })
    if total_active > 20:
        ai_insights.append({
            "severity": "Medium", "title": f"High Active Backlog ({total_active} jobs)",
            "detail": "Consider redistributing workload or adding temporary capacity.",
            "action": "Review technician load distribution in the Technician Load tab."
        })
    if avg_tat > 7:
        ai_insights.append({
            "severity": "Medium", "title": f"High Avg TAT ({avg_tat} days)",
            "detail": "Average turnaround time exceeds 7 days. Investigate bottlenecks.",
            "action": "Check if specific issue categories or technicians are causing delays."
        })
    if not ai_insights:
        ai_insights.append({
            "severity": "Low", "title": "Service Operations on Track",
            "detail": "No significant anomalies detected. Keep up the pace!",
        })

    completion_rate = f"{total_completed*100//max(total_all,1)}%" if total_all else "0%"

    financial_control = {
        "total_active": total_active,
        "completion_rate": completion_rate,
        "avg_tat": avg_tat or "N/A",
        "revenue_mtd": flt(est_revenue),
    }

    # AI insight for Not Repairable
    if nr_count >= 3:
        nr_rate = round(nr_count * 100 / max(total_all, 1), 1)
        ai_insights.append({
            "severity": "Medium",
            "title": f"{nr_count} Not Repairable Devices ({nr_rate}%)",
            "detail": "Review NR/BER reasons for patterns — common causes may indicate intake screening gaps.",
            "action": "Check the Not Repairable tab for pending spare recoveries.",
        })

    return {
        "pipeline": pipeline,
        "kpis": kpis,
        "pending_intake": pending_intake,
        "in_service": in_service,
        "ready_delivery": ready_delivery,
        "not_repairable": not_repairable,
        "overdue": overdue,
        "technician_load": technician_load,
        "issue_breakdown": issue_breakdown,
        "ai_insights": ai_insights,
        "financial_control": financial_control,
    }
