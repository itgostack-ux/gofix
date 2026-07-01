# Copyright (c) 2025, GoStack and contributors
# Service Request Summary — Script Report

import frappe
from frappe import _
from frappe.utils import flt

from ch_erp15.ch_erp15.report_scope import scope_where_clause


def execute(filters=None):
	columns = get_columns()
	data = get_data(filters)
	chart = get_chart(data)
	return columns, data, None, chart


def get_columns():
	return [
		{"label": _("Status"), "fieldname": "status", "fieldtype": "Data", "width": 160},
		{"label": _("Count"), "fieldname": "count", "fieldtype": "Int", "width": 100},
		{"label": _("Avg Days"), "fieldname": "avg_days", "fieldtype": "Float", "width": 120, "precision": 1},
		{"label": _("Under Warranty"), "fieldname": "under_warranty", "fieldtype": "Int", "width": 130},
		{"label": _("Out of Warranty"), "fieldname": "out_of_warranty", "fieldtype": "Int", "width": 130},
		{"label": _("Repeat Complaints"), "fieldname": "repeat_complaints", "fieldtype": "Int", "width": 140},
		{"label": _("Total Estimated (₹)"), "fieldname": "total_estimated", "fieldtype": "Currency", "width": 160},
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
	scope = scope_where_clause(
		warehouse_field="sr.source_warehouse",
		extra_warehouse_fields=("sr.transferred_to_store",),
	)
	if scope is not None:
		conditions += f" AND {scope}"

	query = f"""
		SELECT
			COALESCE(sr.decision, sr.status, 'Unknown') as status,
			COUNT(*) as count,
			AVG(DATEDIFF(COALESCE(sr.modified, NOW()), sr.service_date)) as avg_days,
			SUM(CASE WHEN sr.warranty_status = 'Under Warranty' THEN 1 ELSE 0 END) as under_warranty,
			SUM(CASE WHEN sr.warranty_status IN ('Out of Warranty', 'No Warranty') THEN 1 ELSE 0 END) as out_of_warranty,
			SUM(CASE WHEN sr.is_repeat_complaint = 1 THEN 1 ELSE 0 END) as repeat_complaints,
			SUM(COALESCE(sr.estimated_cost, 0)) as total_estimated
		FROM `tabService Request` sr
		WHERE sr.docstatus < 2
		{conditions}
		GROUP BY COALESCE(sr.decision, sr.status, 'Unknown')
		ORDER BY count DESC
	"""

	return frappe.db.sql(query, params, as_dict=True)


def get_chart(data):
	labels = [d.status for d in data]
	values = [d.count for d in data]
	return {
		"data": {"labels": labels, "datasets": [{"name": _("Service Requests"), "values": values}]},
		"type": "bar",
		"colors": ["#5e64ff"],
	}
