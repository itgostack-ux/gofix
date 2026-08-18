# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt

"""GoFix Daily Tokens — store-level daily token report.

One row per (business_date, store), aggregating token counts per status,
average waiting time to completion, and cancellation ratio. Ops uses this
to spot understaffed stores and unusually high walk-away rates.

Filters
-------
company                → Link Company (optional, else all gofix_enabled)
store                  → Link Warehouse (optional single-store view)
from_date / to_date    → business_date range (default: last 30 days)
"""

from __future__ import annotations

from typing import Any

import frappe
from ch_erp15.ch_erp15.report_scope import scope_where_clause
from frappe import _
from frappe.utils import add_days, cint, flt, get_datetime, nowdate


_STATUS_BUCKETS = {
	"Waiting":          "waiting",
	"Called":           "in_progress",
	"Attending":        "in_progress",
	"Job Card Created": "in_progress",
	"Completed":        "completed",
	"Cancelled":        "cancelled",
	"Customer Left":    "dropped",
}


def execute(filters: dict | None = None):
	filters = frappe._dict(filters or {})
	columns = _columns()
	data = _fetch(filters)
	return columns, data


def _columns() -> list[dict[str, Any]]:
	return [
		{"label": _("Business Date"), "fieldname": "business_date", "fieldtype": "Date", "width": 110},
		{"label": _("Store"),         "fieldname": "store",         "fieldtype": "Link", "options": "Warehouse", "width": 200},
		{"label": _("Store Code"),    "fieldname": "store_code",    "fieldtype": "Data", "width": 110},
		{"label": _("Company"),       "fieldname": "company",       "fieldtype": "Link", "options": "Company", "width": 160},
		{"label": _("Total"),         "fieldname": "total",         "fieldtype": "Int",  "width": 80},
		{"label": _("Waiting"),       "fieldname": "waiting",       "fieldtype": "Int",  "width": 80},
		{"label": _("In Progress"),   "fieldname": "in_progress",   "fieldtype": "Int",  "width": 100},
		{"label": _("Completed"),     "fieldname": "completed",     "fieldtype": "Int",  "width": 90},
		{"label": _("Cancelled"),     "fieldname": "cancelled",     "fieldtype": "Int",  "width": 90},
		{"label": _("Customer Left"), "fieldname": "dropped",       "fieldtype": "Int",  "width": 110},
		{"label": _("Avg Wait (min)"),"fieldname": "avg_wait_min",  "fieldtype": "Float","width": 110, "precision": 1},
		{"label": _("Completion %"),  "fieldname": "completion_pct","fieldtype": "Percent","width": 110},
		{"label": _("Walkaway %"),    "fieldname": "walkaway_pct",  "fieldtype": "Percent","width": 110},
	]


def _fetch(filters: dict) -> list[dict]:
	to_date = filters.get("to_date") or nowdate()
	from_date = filters.get("from_date") or add_days(to_date, -29)

	conds: list[str] = ["business_date BETWEEN %(from_date)s AND %(to_date)s"]
	params: dict[str, Any] = {"from_date": from_date, "to_date": to_date}

	if filters.get("company"):
		conds.append("company = %(company)s")
		params["company"] = filters["company"]
	else:
		# Restrict to gofix-enabled companies only when the column exists.
		if frappe.db.has_column("Company", "gofix_enabled"):
			companies = frappe.get_all(
				"Company", filters={"gofix_enabled": 1}, pluck="name"
			)
			if not companies:
				return []
			conds.append("company IN %(companies)s")
			params["companies"] = tuple(companies)

	if filters.get("store"):
		conds.append("store = %(store)s")
		params["store"] = filters["store"]

	# Row-level scope: only tokens for stores the user is scoped to.
	scope = scope_where_clause(warehouse_field="store")
	if scope:
		conds.append(scope)

	where = " AND ".join(conds)
	rows = frappe.db.sql(
		f"""
		SELECT
			business_date, store, store_code, store_name, company,
			status, creation, completed_at
		FROM `tabGoFix Token`
		WHERE {where}
		""",
		params,
		as_dict=True,
	)

	agg: dict[tuple, dict] = {}
	for r in rows:
		key = (str(r["business_date"]), r["store"])
		bucket = agg.setdefault(
			key,
			{
				"business_date": r["business_date"],
				"store": r["store"],
				"store_code": r.get("store_code") or "",
				"company": r.get("company") or "",
				"total": 0, "waiting": 0, "in_progress": 0,
				"completed": 0, "cancelled": 0, "dropped": 0,
				"_wait_sum": 0, "_wait_count": 0,
			},
		)
		bucket["total"] += 1
		b = _STATUS_BUCKETS.get(r["status"], "waiting")
		bucket[b] = bucket.get(b, 0) + 1
		if b == "completed" and r.get("completed_at"):
			mins = int(
				(get_datetime(r["completed_at"]) - get_datetime(r["creation"]))
				.total_seconds() / 60
			)
			bucket["_wait_sum"] += mins
			bucket["_wait_count"] += 1

	out: list[dict] = []
	for bucket in sorted(agg.values(), key=lambda b: (b["business_date"], b["store_code"])):
		wait_count = bucket.pop("_wait_count")
		wait_sum = bucket.pop("_wait_sum")
		bucket["avg_wait_min"] = flt(wait_sum / wait_count, 1) if wait_count else 0
		serviceable = bucket["completed"] + bucket["waiting"] + bucket["in_progress"]
		bucket["completion_pct"] = round(
			(bucket["completed"] / serviceable) * 100, 1
		) if serviceable else 0
		bucket["walkaway_pct"] = round(
			(bucket["dropped"] / bucket["total"]) * 100, 1
		) if bucket["total"] else 0
		out.append(bucket)
	return out
