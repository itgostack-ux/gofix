"""
Walk-in Conversion Report
------------------------
Store / Zone / City manager view — automatically scoped to the logged-in
user's assigned CH Store(s).  System Manager sees all data.

Metrics per store per day
  Walk-ins          : all Service Requests
  Withdrawn         : walkin_status = 'Withdrawn'
  Withdrawal %      : Withdrawn / Walk-ins
  CS Conversions    : previously-withdrawn customers re-engaged and invoiced
                      (proxy: SR with is_repeat_complaint=1 AND decision in Invoiced/Delivered)
  Invoice Amount    : grand_total sum of linked Sales Invoices for conversions
"""

import frappe
from frappe import _
from frappe.utils import flt, today


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------

def execute(filters=None):
    filters = filters or {}
    _apply_defaults(filters)
    scope_sql = _get_scope_sql()
    columns = _get_columns()
    data = _get_data(filters, scope_sql)
    chart = _make_chart(data)
    summary = _make_summary(data)
    return columns, data, None, chart, summary


# ---------------------------------------------------------------------------
# defaults
# ---------------------------------------------------------------------------

def _apply_defaults(filters):
    if not filters.get("from_date"):
        filters["from_date"] = frappe.utils.get_first_day(today())
    if not filters.get("to_date"):
        filters["to_date"] = today()


# ---------------------------------------------------------------------------
# permission scope — based on CH Store user assignment
# ---------------------------------------------------------------------------

def _get_scope_sql():
    """Return raw SQL fragment like ' AND sr.source_warehouse IN (...)'
    or empty string for unrestricted access."""
    user = frappe.session.user
    if "System Manager" in frappe.get_roles(user):
        return ""

    try:
        rows = frappe.db.sql(
            """
            SELECT DISTINCT cs.warehouse
            FROM `tabCH Store` cs
            INNER JOIN `tabCH Store User` su ON su.parent = cs.name
            WHERE su.user = %s
              AND cs.disabled = 0
              AND cs.warehouse IS NOT NULL
              AND cs.warehouse != ''
            """,
            user,
            as_dict=True,
        )
        if rows:
            wh_list = ", ".join(frappe.db.escape(r.warehouse) for r in rows)
            return f" AND sr.source_warehouse IN ({wh_list})"
    except Exception:
        pass

    return ""


# ---------------------------------------------------------------------------
# columns
# ---------------------------------------------------------------------------

def _get_columns():
    return [
        {"fieldname": "service_date", "label": _("Date"),
         "fieldtype": "Date", "width": 105},
        {"fieldname": "store_name", "label": _("Store"),
         "fieldtype": "Data", "width": 165},
        {"fieldname": "zone", "label": _("Zone"),
         "fieldtype": "Link", "options": "CH Store Zone", "width": 135},
        {"fieldname": "city", "label": _("City"),
         "fieldtype": "Link", "options": "CH City", "width": 120},
        {"fieldname": "total_walkins", "label": _("Walk-ins"),
         "fieldtype": "Int", "width": 85},
        {"fieldname": "withdrawn", "label": _("Withdrawn"),
         "fieldtype": "Int", "width": 95},
        {"fieldname": "withdrawn_pct", "label": _("Withdrawn %"),
         "fieldtype": "Percent", "width": 108},
        {"fieldname": "conversions", "label": _("CS Conversions"),
         "fieldtype": "Int", "width": 120},
        {"fieldname": "invoice_amount", "label": _("Invoice Amount (₹)"),
         "fieldtype": "Currency", "width": 155},
    ]


# ---------------------------------------------------------------------------
# data query
# ---------------------------------------------------------------------------

def _get_data(filters, scope_sql):
    params = {
        "from_date": filters.get("from_date"),
        "to_date": filters.get("to_date"),
    }
    extra = scope_sql  # already safe — built from DB values, not user input

    if filters.get("company"):
        extra += " AND sr.company = %(company)s"
        params["company"] = filters["company"]
    if filters.get("store"):
        extra += " AND sr.source_warehouse = %(store)s"
        params["store"] = filters["store"]
    if filters.get("zone"):
        extra += " AND w.ch_zone = %(zone)s"
        params["zone"] = filters["zone"]
    if filters.get("city"):
        extra += " AND w.ch_city = %(city)s"
        params["city"] = filters["city"]

    query = """
        SELECT
            sr.service_date,
            w.warehouse_name                          AS store_name,
            IFNULL(w.ch_zone,  '')                    AS zone,
            IFNULL(w.ch_city,  '')                    AS city,
            COUNT(*)                                   AS total_walkins,

            SUM(CASE WHEN sr.walkin_status = 'Withdrawn'
                     THEN 1 ELSE 0 END)               AS withdrawn,

            ROUND(
                100.0 * SUM(CASE WHEN sr.walkin_status = 'Withdrawn'
                                 THEN 1 ELSE 0 END)
                / NULLIF(COUNT(*), 0), 1
            )                                          AS withdrawn_pct,

            /* CS Conversions = repeat-complaint walk-ins that eventually got invoiced */
            SUM(CASE WHEN sr.is_repeat_complaint = 1
                      AND sr.decision IN ('Invoiced', 'Delivered')
                     THEN 1 ELSE 0 END)               AS conversions,

            COALESCE(SUM(
                CASE WHEN sr.is_repeat_complaint = 1
                      AND sr.decision IN ('Invoiced', 'Delivered')
                     THEN IFNULL(si.grand_total, 0)
                     ELSE 0 END
            ), 0)                                      AS invoice_amount

        FROM `tabService Request` sr
        INNER JOIN `tabWarehouse`    w  ON w.name  = sr.source_warehouse
        LEFT  JOIN `tabSales Invoice` si ON si.name = sr.service_invoice
                                        AND si.docstatus = 1
        WHERE sr.docstatus < 2
          AND sr.service_date BETWEEN %(from_date)s AND %(to_date)s
    """ + extra + """
        GROUP BY sr.service_date, sr.source_warehouse
        ORDER BY sr.service_date DESC, w.warehouse_name
    """

    data = frappe.db.sql(query, params, as_dict=True)
    for row in data:
        row["withdrawn_pct"] = flt(row.get("withdrawn_pct"), 1)
    return data


# ---------------------------------------------------------------------------
# chart
# ---------------------------------------------------------------------------

def _make_chart(data):
    if not data:
        return None

    # Aggregate by date for chart (multiple stores collapse into date totals)
    date_map = {}
    for row in data:
        d = str(row.get("service_date") or "")
        if d not in date_map:
            date_map[d] = {"walkins": 0, "withdrawn": 0, "conversions": 0}
        date_map[d]["walkins"]     += (row.get("total_walkins") or 0)
        date_map[d]["withdrawn"]   += (row.get("withdrawn") or 0)
        date_map[d]["conversions"] += (row.get("conversions") or 0)

    labels = sorted(date_map.keys())
    return {
        "data": {
            "labels": labels,
            "datasets": [
                {"name": _("Walk-ins"),    "values": [date_map[d]["walkins"]     for d in labels]},
                {"name": _("Withdrawn"),   "values": [date_map[d]["withdrawn"]   for d in labels]},
                {"name": _("Conversions"), "values": [date_map[d]["conversions"] for d in labels]},
            ],
        },
        "type": "bar",
        "colors": ["#5e64ff", "#ff5858", "#28a745"],
        "barOptions": {"stacked": False},
    }


# ---------------------------------------------------------------------------
# summary strip
# ---------------------------------------------------------------------------

def _make_summary(data):
    if not data:
        return []

    total_walkins   = sum(r.get("total_walkins") or 0 for r in data)
    total_withdrawn = sum(r.get("withdrawn") or 0 for r in data)
    total_conv      = sum(r.get("conversions") or 0 for r in data)
    total_invoice   = sum(r.get("invoice_amount") or 0 for r in data)
    wdraw_pct       = flt(100 * total_withdrawn / total_walkins, 1) if total_walkins else 0

    return [
        {"value": total_walkins,   "label": _("Total Walk-ins"),    "datatype": "Int",      "color": "blue"},
        {"value": total_withdrawn, "label": _("Withdrawn"),          "datatype": "Int",      "color": "orange"},
        {"value": wdraw_pct,       "label": _("Withdrawal %"),       "datatype": "Percent",  "color": "orange"},
        {"value": total_conv,      "label": _("CS Conversions"),     "datatype": "Int",      "color": "green"},
        {"value": total_invoice,   "label": _("Conversion Amount"),  "datatype": "Currency", "color": "green"},
    ]
