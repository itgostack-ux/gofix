# Copyright (c) 2026, GoFix and contributors

"""Every job the workshop turned away, and why.

A rejection is the one outcome the Ops Hub used to hide: the ticket dropped out
of every queue view, so nobody could see how often devices were refused, at
which counter, or for what reason. That is exactly the number a service business
has to watch — a rising rejection rate is either a training problem, a parts
availability problem, or a pricing problem, and all three are invisible until
somebody counts them.

Rows are one per rejected ticket. The summary above the table carries the
rejection RATE, because a count on its own says nothing without the base it came
from.
"""

import frappe
from frappe import _
from frappe.utils import flt

# Decisions that mean "we did not do the work". Withdrawn is the customer's
# choice and is reported alongside, but counted separately from a rejection,
# which is ours.
REFUSED_DECISIONS = ("Rejected", "Cancelled", "Expired")
CUSTOMER_BACKED_OUT = ("Withdrawn",)


def execute(filters=None):
	filters = frappe._dict(filters or {})
	columns = get_columns()
	data = get_data(filters)
	return columns, data, None, get_chart(data), get_summary(filters, data)


def get_columns():
	return [
		{"label": _("Ticket"), "fieldname": "name", "fieldtype": "Link",
			"options": "Service Request", "width": 150},
		{"label": _("Date"), "fieldname": "service_date", "fieldtype": "Date", "width": 95},
		{"label": _("Outcome"), "fieldname": "decision", "fieldtype": "Data", "width": 95},
		{"label": _("Store"), "fieldname": "source_warehouse", "fieldtype": "Link",
			"options": "Warehouse", "width": 160},
		{"label": _("Customer"), "fieldname": "customer_name", "fieldtype": "Data", "width": 150},
		{"label": _("Device"), "fieldname": "device_item_name", "fieldtype": "Data", "width": 190},
		{"label": _("Brand"), "fieldname": "brand", "fieldtype": "Data", "width": 90},
		{"label": _("Issue Category"), "fieldname": "issue_category", "fieldtype": "Link",
			"options": "Issue Category", "width": 140},
		{"label": _("Reason"), "fieldname": "rejection_reason", "fieldtype": "Small Text",
			"width": 300},
		{"label": _("Days Held"), "fieldname": "days_held", "fieldtype": "Int", "width": 90},
		{"label": _("Est. Value Lost"), "fieldname": "estimated_cost", "fieldtype": "Currency",
			"width": 130},
	]


def _conditions(filters, alias="sr"):
	where = [f"{alias}.docstatus < 2"]
	values = {}
	if filters.get("from_date"):
		where.append(f"{alias}.service_date >= %(from_date)s")
		values["from_date"] = filters.from_date
	if filters.get("to_date"):
		where.append(f"{alias}.service_date <= %(to_date)s")
		values["to_date"] = filters.to_date
	if filters.get("company"):
		where.append(f"{alias}.company = %(company)s")
		values["company"] = filters.company
	if filters.get("source_warehouse"):
		where.append(f"{alias}.source_warehouse = %(source_warehouse)s")
		values["source_warehouse"] = filters.source_warehouse
	return where, values


def get_data(filters):
	where, values = _conditions(filters)

	decisions = list(REFUSED_DECISIONS)
	if filters.get("include_withdrawn"):
		decisions += list(CUSTOMER_BACKED_OUT)
	if filters.get("decision"):
		decisions = [filters.decision]
	where.append("sr.decision IN %(decisions)s")
	values["decisions"] = tuple(decisions)

	rows = frappe.db.sql(
		f"""
		SELECT sr.name, sr.service_date, sr.decision, sr.source_warehouse,
		       sr.customer_name, sr.device_item_name, sr.brand, sr.issue_category,
		       sr.rejection_reason, sr.estimated_cost,
		       DATEDIFF(IFNULL(sr.modified, NOW()), sr.service_date) AS days_held
		FROM `tabService Request` sr
		WHERE {" AND ".join(where)}
		ORDER BY sr.service_date DESC, sr.name DESC
		""",
		values,
		as_dict=True,
	)
	for r in rows:
		r["rejection_reason"] = (r.get("rejection_reason") or "").strip() or _("— not recorded —")
		r["days_held"] = max(0, int(r.get("days_held") or 0))
	return rows


def get_chart(data):
	"""Rejections grouped by reason — the actionable cut."""
	buckets = {}
	for row in data:
		key = (row.get("rejection_reason") or "").strip()[:60] or _("Unspecified")
		buckets[key] = buckets.get(key, 0) + 1
	if not buckets:
		return None
	top = sorted(buckets.items(), key=lambda kv: kv[1], reverse=True)[:10]
	return {
		"data": {
			"labels": [k for k, _v in top],
			"datasets": [{"name": _("Tickets"), "values": [v for _k, v in top]}],
		},
		"type": "bar",
		"colors": ["#e24c4c"],
	}


def get_summary(filters, data):
	"""A count means nothing without the base it came from — show the rate."""
	where, values = _conditions(filters)
	total = frappe.db.sql(
		f"SELECT COUNT(*) FROM `tabService Request` sr WHERE {' AND '.join(where)}",
		values,
	)[0][0] or 0

	rejected = sum(1 for r in data if r.get("decision") == "Rejected")
	refused = sum(1 for r in data if r.get("decision") in REFUSED_DECISIONS)
	no_reason = sum(1 for r in data if r.get("rejection_reason") == _("— not recorded —"))
	value_lost = sum(flt(r.get("estimated_cost")) for r in data)
	rate = (refused / total * 100.0) if total else 0.0

	return [
		{"label": _("Tickets in Period"), "value": total, "indicator": "Blue",
			"datatype": "Int"},
		{"label": _("Rejected"), "value": rejected, "indicator": "Red", "datatype": "Int"},
		{"label": _("Not Serviced (all reasons)"), "value": refused, "indicator": "Orange",
			"datatype": "Int"},
		{"label": _("Rejection Rate"), "value": f"{rate:.1f}%",
			"indicator": "Red" if rate >= 10 else "Green", "datatype": "Data"},
		{"label": _("No Reason Recorded"), "value": no_reason,
			"indicator": "Red" if no_reason else "Green", "datatype": "Int"},
		{"label": _("Est. Value Lost"), "value": value_lost, "indicator": "Orange",
			"datatype": "Currency"},
	]
