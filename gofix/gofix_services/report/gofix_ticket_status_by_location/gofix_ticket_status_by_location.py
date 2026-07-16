# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt

"""GoFix Ticket Status by Location.

One row per Service Request with its full lifecycle position — SR status,
device transfer/location, technician, QC verdict, billing — grouped by the
store the ticket was raised at. Answers "what is the complete status of every
ticket, location wise" in a single screen.
"""

import frappe
from frappe import _
from frappe.utils import date_diff, flt, today


def execute(filters=None):
	filters = frappe._dict(filters or {})
	columns = get_columns()
	data = get_data(filters)
	summary = get_summary(data)
	chart = get_chart(data)
	return columns, data, None, chart, summary


def get_columns():
	return [
		{"label": _("Location"), "fieldname": "location", "fieldtype": "Link", "options": "Warehouse", "width": 200},
		{"label": _("Ticket"), "fieldname": "name", "fieldtype": "Link", "options": "Service Request", "width": 150},
		{"label": _("Date"), "fieldname": "service_date", "fieldtype": "Date", "width": 95},
		{"label": _("Customer"), "fieldname": "customer_name", "fieldtype": "Data", "width": 140},
		{"label": _("Device"), "fieldname": "device", "fieldtype": "Data", "width": 170},
		{"label": _("Issue"), "fieldname": "issue_category", "fieldtype": "Link", "options": "Issue Category", "width": 120},
		{"label": _("Priority"), "fieldname": "priority", "fieldtype": "Data", "width": 75},
		{"label": _("Status"), "fieldname": "status", "fieldtype": "Data", "width": 105},
		{"label": _("Device At"), "fieldname": "device_at", "fieldtype": "Data", "width": 170},
		{"label": _("Transfer"), "fieldname": "transfer_status", "fieldtype": "Data", "width": 130},
		{"label": _("Technician"), "fieldname": "technician", "fieldtype": "Data", "width": 130},
		{"label": _("QC"), "fieldname": "qc_status", "fieldtype": "Data", "width": 70},
		{"label": _("Estimate"), "fieldname": "estimated_cost", "fieldtype": "Currency", "width": 100},
		{"label": _("Invoice"), "fieldname": "service_invoice", "fieldtype": "Link", "options": "Sales Invoice", "width": 140},
		{"label": _("Billed"), "fieldname": "billed_amount", "fieldtype": "Currency", "width": 100},
		{"label": _("Completed"), "fieldname": "actual_completion_date", "fieldtype": "Date", "width": 95},
		{"label": _("Days Open"), "fieldname": "days_open", "fieldtype": "Int", "width": 85},
		{"label": _("Timeline"), "fieldname": "timeline_btn", "fieldtype": "Data", "width": 90},
	]


def get_data(filters):
	conditions = ["sr.docstatus < 2"]
	values = {}

	if filters.get("company"):
		conditions.append("sr.company = %(company)s")
		values["company"] = filters.company
	if filters.get("location"):
		conditions.append("sr.source_warehouse = %(location)s")
		values["location"] = filters.location
	if filters.get("from_date"):
		conditions.append("sr.service_date >= %(from_date)s")
		values["from_date"] = filters.from_date
	if filters.get("to_date"):
		conditions.append("sr.service_date <= %(to_date)s")
		values["to_date"] = filters.to_date
	if filters.get("status"):
		conditions.append("sr.status = %(status)s")
		values["status"] = filters.status
	if not filters.get("include_closed"):
		conditions.append("sr.status NOT IN ('Cancelled', 'Rejected')")

	rows = frappe.db.sql(
		f"""
		SELECT
			sr.name, sr.source_warehouse AS location, sr.service_date,
			sr.customer_name, sr.customer,
			COALESCE(NULLIF(sr.device_item_name, ''), sr.device_item) AS device,
			sr.issue_category, sr.priority, sr.status, sr.decision,
			sr.transfer_status, sr.current_location, sr.transferred_to_store,
			sr.estimated_cost, sr.service_invoice, sr.service_order,
			sr.actual_completion_date,
			so.qc_status,
			si.grand_total AS billed_amount, si.status AS invoice_status,
			(
				SELECT emp.employee_name
				FROM `tabJob Assignment` ja
				LEFT JOIN `tabEmployee` emp ON emp.name = ja.service_engineer
				WHERE ja.service_request = sr.name AND ja.docstatus < 2
				ORDER BY ja.creation DESC LIMIT 1
			) AS technician
		FROM `tabService Request` sr
		LEFT JOIN `tabSales Order` so ON so.name = sr.service_order
		LEFT JOIN `tabSales Invoice` si ON si.name = sr.service_invoice
		WHERE {" AND ".join(conditions)}
		ORDER BY sr.source_warehouse, sr.service_date DESC, sr.name
		""",
		values,
		as_dict=True,
	)

	for r in rows:
		# Where the device physically is right now
		if r.transfer_status == "In Transit":
			r.device_at = f"In transit → {r.transferred_to_store or ''}"
		elif r.transfer_status == "Return In Transit":
			r.device_at = f"In transit → {r.location}"
		else:
			r.device_at = r.current_location or r.location
		if r.invoice_status:
			r.status = f"{r.status} ({r.invoice_status})" if r.service_invoice else r.status
		end = r.actual_completion_date or today()
		r.days_open = max(date_diff(end, r.service_date), 0) if r.service_date else None
		r.timeline_btn = "View"

	return rows


def get_summary(data):
	total = len(data)
	completed = sum(1 for r in data if (r.status or "").startswith(("Completed", "Invoiced", "Delivered")))
	in_progress = total - completed
	billed = sum(flt(r.billed_amount) for r in data)
	locations = len({r.location for r in data})
	return [
		{"label": _("Locations"), "value": locations, "datatype": "Int"},
		{"label": _("Tickets"), "value": total, "datatype": "Int"},
		{"label": _("In Progress"), "value": in_progress, "datatype": "Int", "indicator": "Orange"},
		{"label": _("Completed"), "value": completed, "datatype": "Int", "indicator": "Green"},
		{"label": _("Billed Amount"), "value": billed, "datatype": "Currency"},
	]


def get_chart(data):
	buckets = {}
	for r in data:
		loc = r.location or "—"
		b = buckets.setdefault(loc, {"open": 0, "done": 0})
		if (r.status or "").startswith(("Completed", "Invoiced", "Delivered")):
			b["done"] += 1
		else:
			b["open"] += 1
	labels = sorted(buckets)
	return {
		"data": {
			"labels": labels,
			"datasets": [
				{"name": _("In Progress"), "values": [buckets[l]["open"] for l in labels]},
				{"name": _("Completed"), "values": [buckets[l]["done"] for l in labels]},
			],
		},
		"type": "bar",
		"barOptions": {"stacked": 1},
	}
