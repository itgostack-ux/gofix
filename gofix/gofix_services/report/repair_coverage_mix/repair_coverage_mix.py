# Copyright (c) 2026, GoStack and contributors
# Repair Coverage Mix — Script Report
"""Who paid for the repairs we did, and what each pocket cost us.

Every service management suite carries a report of this shape — SAP CS splits
warranty-borne cost from customer-billed, Oracle Service reports contract
coverage against entitlement — because the three pockets are answerable to
three different people:

  * **In-Warranty**  — we carry it. This is cost, not revenue, and it is the
    number a service head is asked to bring down: it is rework and it is
    honouring our own promise.
  * **VAS Claim**    — a protection plan carries it. The repair is quoted
    normally and recovered through the claims flow, so an unrecovered claim is
    money sitting on the floor. This column is what tells finance how much.
  * **Non-Warranty** — the customer pays. Ordinary revenue.

``coverage_category`` is derived on every Service Request by
``_classify_coverage`` and, until this report existed, was written and never
read outside tests: correct, and invisible. Nothing here re-derives it — a
report that computed its own answer from the IMEI would sooner or later
disagree with the ticket, and then two screens tell a manager different things
about the same repair.

Blank is shown as its own row rather than folded into Non-Warranty. A ticket
with no category is one the classifier never ran on, which is a data-quality
signal worth seeing, not a paid repair.
"""

import frappe
from frappe import _
from frappe.utils import flt

from ch_erp15.ch_erp15.report_scope import scope_where_clause

#: Kept in the business's order of precedence, not alphabetical, so the rows
#: read down the way the rule is applied.
_ORDER = ("In-Warranty", "VAS Claim", "Non-Warranty", "Unclassified")


def execute(filters=None):
    columns = get_columns()
    data = get_data(filters)
    return columns, data, None, get_chart(data), get_summary(data)


def get_columns():
    return [
        {"label": _("Who Pays"), "fieldname": "coverage", "fieldtype": "Data", "width": 150},
        {"label": _("Repairs"), "fieldname": "jobs", "fieldtype": "Int", "width": 90},
        {"label": _("Share %"), "fieldname": "share_pct", "fieldtype": "Percent", "width": 90},
        {"label": _("Billed (₹)"), "fieldname": "revenue", "fieldtype": "Currency", "width": 130},
        {"label": _("Parts Cost (₹)"), "fieldname": "parts_cost", "fieldtype": "Currency", "width": 130},
        {"label": _("Labour Cost (₹)"), "fieldname": "labour_cost", "fieldtype": "Currency", "width": 130},
        {"label": _("Total Cost (₹)"), "fieldname": "total_cost", "fieldtype": "Currency", "width": 130},
        {"label": _("Margin (₹)"), "fieldname": "margin", "fieldtype": "Currency", "width": 120},
        {"label": _("Avg Cost / Repair (₹)"), "fieldname": "avg_cost", "fieldtype": "Currency", "width": 150},
        {"label": _("Repeat Visits"), "fieldname": "repeats", "fieldtype": "Int", "width": 110},
    ]


def get_data(filters):
    conditions = ""
    params = {}

    if filters and filters.get("company"):
        conditions += " AND sr.company = %(company)s"
        params["company"] = filters["company"]
    if filters and filters.get("from_date"):
        conditions += " AND sr.service_date >= %(from_date)s"
        params["from_date"] = filters["from_date"]
    if filters and filters.get("to_date"):
        conditions += " AND sr.service_date <= %(to_date)s"
        params["to_date"] = filters["to_date"]
    if filters and filters.get("source_warehouse"):
        conditions += " AND sr.source_warehouse = %(source_warehouse)s"
        params["source_warehouse"] = filters["source_warehouse"]

    # Tier 4: fail-closed scope on either Service Request warehouse endpoint.
    # Same pair as Service Request Summary — a device in transit to another
    # store is still that store's job.
    scope = scope_where_clause(
        warehouse_field="sr.source_warehouse",
        extra_warehouse_fields=("sr.transferred_to_store",),
    )
    if scope is not None:
        conditions += f" AND {scope}"

    # The geography asked for, on top of the scope the caller holds. Asking for
    # a zone you cannot see returns nothing, never everything.
    from gofix.report_filters import geo_conditions

    conditions += geo_conditions(filters, company_field=None,
                                 warehouse_field="sr.source_warehouse")

    # A site that has not yet migrated has no column to group on; answer empty
    # rather than throwing 1054 at whoever opened the report.
    if not frappe.db.has_column("Service Request", "coverage_category"):
        return []

    rows = frappe.db.sql(
        f"""
        SELECT
            COALESCE(NULLIF(sr.coverage_category, ''), 'Unclassified') AS coverage,
            COUNT(*) AS jobs,
            -- Same definitions Repair Profitability uses, deliberately: two
            -- reports that disagree about what a repair earned are worse than
            -- one report. actual_billed is what was invoiced; estimated_cost
            -- only stands in where nothing has been billed yet.
            SUM(COALESCE(NULLIF(sr.actual_billed, 0), sr.estimated_cost, 0)) AS revenue,
            SUM(COALESCE(sr.spare_parts_cost, 0)) AS parts_cost,
            SUM(COALESCE(sr.labor_cost, 0)) AS labour_cost,
            SUM(COALESCE(NULLIF(sr.total_repair_cost, 0),
                         COALESCE(sr.spare_parts_cost, 0) + COALESCE(sr.labor_cost, 0)
                )) AS total_cost,
            SUM(COALESCE(sr.is_repeat_complaint, 0)) AS repeats
        FROM `tabService Request` sr
        WHERE sr.docstatus = 1
        {conditions}
        GROUP BY COALESCE(NULLIF(sr.coverage_category, ''), 'Unclassified')
        """,
        params,
        as_dict=True,
    )

    total_jobs = sum(r.jobs for r in rows) or 0
    by_key = {r.coverage: r for r in rows}

    data = []
    for key in _ORDER:
        r = by_key.pop(key, None)
        if not r:
            continue
        data.append(_shape(r, total_jobs))
    # Anything the Select has gained since this list was written still shows.
    for r in by_key.values():
        data.append(_shape(r, total_jobs))
    return data


def _shape(r, total_jobs):
    jobs = int(r.jobs or 0)
    parts = flt(r.parts_cost)
    labour = flt(r.labour_cost)
    # total_repair_cost is the controller's own figure and is what Repair
    # Profitability reports; parts+labour is only the fallback for rows where
    # it was never stamped, so the two reports still add up to the same money.
    total_cost = flt(r.total_cost)
    revenue = flt(r.revenue)
    return {
        "coverage": r.coverage,
        "jobs": jobs,
        "share_pct": (100.0 * jobs / total_jobs) if total_jobs else 0.0,
        "revenue": revenue,
        "parts_cost": parts,
        "labour_cost": labour,
        "total_cost": total_cost,
        "margin": revenue - total_cost,
        "avg_cost": (total_cost / jobs) if jobs else 0.0,
        "repeats": int(r.repeats or 0),
    }


def get_chart(data):
    if not data:
        return None
    return {
        "data": {
            "labels": [r["coverage"] for r in data],
            "datasets": [
                {"name": _("Billed"), "values": [r["revenue"] for r in data]},
                {"name": _("Cost"), "values": [r["total_cost"] for r in data]},
            ],
        },
        "type": "bar",
        "colors": ["#16a34a", "#dc2626"],
    }


def get_summary(data):
    if not data:
        return []
    jobs = sum(r["jobs"] for r in data)
    borne = sum(r["total_cost"] for r in data if r["coverage"] == "In-Warranty")
    claimable = sum(r["total_cost"] for r in data if r["coverage"] == "VAS Claim")
    unclassified = sum(r["jobs"] for r in data if r["coverage"] == "Unclassified")
    return [
        {"label": _("Repairs"), "value": jobs, "datatype": "Int", "indicator": "blue"},
        # The two numbers a service head is actually asked for.
        {"label": _("Cost We Carry"), "value": borne, "datatype": "Currency",
         "indicator": "red"},
        {"label": _("Recoverable From Plans"), "value": claimable,
         "datatype": "Currency", "indicator": "orange"},
        {"label": _("Unclassified"), "value": unclassified, "datatype": "Int",
         "indicator": "red" if unclassified else "green"},
    ]
