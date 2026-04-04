# Copyright (c) 2025, GoStack and contributors
# Store-Wise Service Status — Script Report

import frappe
from frappe import _
from frappe.utils import flt, now_datetime, time_diff_in_hours


def execute(filters=None):
	columns = get_columns()
	data = get_data(filters)
	chart = get_chart(data)
	return columns, data, None, chart


def get_columns():
	return [
		{"label": _("Store / Warehouse"), "fieldname": "warehouse", "fieldtype": "Link", "options": "Warehouse", "width": 200},
		{"label": _("Total Requests"), "fieldname": "total", "fieldtype": "Int", "width": 120},
		{"label": _("In Progress"), "fieldname": "in_progress", "fieldtype": "Int", "width": 110},
		{"label": _("Completed"), "fieldname": "completed", "fieldtype": "Int", "width": 100},
		{"label": _("Delivered"), "fieldname": "delivered", "fieldtype": "Int", "width": 100},
		{"label": _("Pending Delivery"), "fieldname": "pending_delivery", "fieldtype": "Int", "width": 130},
		{"label": _("Unclaimed"), "fieldname": "unclaimed", "fieldtype": "Int", "width": 100},
		{"label": _("Avg Turnaround (Days)"), "fieldname": "avg_tat", "fieldtype": "Float", "width": 160, "precision": 1},
		{"label": _("SLA Breached"), "fieldname": "sla_breached", "fieldtype": "Int", "width": 110},
		{"label": _("Total Revenue (₹)"), "fieldname": "revenue", "fieldtype": "Currency", "width": 140},
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

	query = f"""
		SELECT
			COALESCE(sr.source_warehouse, 'No Warehouse') as warehouse,
			COUNT(*) as total,
			SUM(CASE WHEN sr.decision IN ('Accepted', 'In Service') THEN 1 ELSE 0 END) as in_progress,
			SUM(CASE WHEN sr.decision IN ('Completed', 'Invoiced') THEN 1 ELSE 0 END) as completed,
			SUM(CASE WHEN sr.decision = 'Delivered' THEN 1 ELSE 0 END) as delivered,
			SUM(CASE WHEN sr.decision IN ('Completed', 'Invoiced') AND sr.unclaimed_flag = 0 THEN 1 ELSE 0 END) as pending_delivery,
			SUM(CASE WHEN sr.unclaimed_flag = 1 THEN 1 ELSE 0 END) as unclaimed,
			AVG(CASE WHEN sr.decision IN ('Completed', 'Delivered', 'Invoiced')
				THEN DATEDIFF(sr.modified, sr.service_date) END) as avg_tat,
			SUM(COALESCE(sr.estimated_cost, 0)) as revenue
		FROM `tabService Request` sr
		WHERE sr.docstatus < 2
		{conditions}
		GROUP BY COALESCE(sr.source_warehouse, 'No Warehouse')
		ORDER BY total DESC
	"""

	data = frappe.db.sql(query, params, as_dict=True)

	# Count SLA breaches per warehouse
	from gofix.gofix_services.doctype.gofix_sla_rule.gofix_sla_rule import get_sla_rule

	warehouses = [row.warehouse for row in data]
	if warehouses:
		open_srs = frappe.get_all("Service Request",
			filters={
				"decision": ["in", ["Accepted", "In Service"]],
				"docstatus": ["<", 2],
				"source_warehouse": ["in", warehouses],
			},
			fields=["source_warehouse", "issue_category", "priority",
					"received_datetime", "warranty_status", "warranty_plan", "company"])

		breach_map = {}
		now = now_datetime()
		for sr in open_srs:
			wh = sr.source_warehouse or "No Warehouse"
			if not sr.received_datetime:
				continue
			rule = get_sla_rule(
				sr.issue_category, sr.priority,
				company=sr.company,
				warranty_plan=sr.warranty_plan,
				warranty_status=sr.warranty_status,
			)
			if rule and time_diff_in_hours(now, sr.received_datetime) > (rule.target_hours or 0):
				breach_map[wh] = breach_map.get(wh, 0) + 1

		for row in data:
			row.sla_breached = breach_map.get(row.warehouse, 0)
	else:
		for row in data:
			row.sla_breached = 0

	return data


def get_chart(data):
	labels = [d.warehouse for d in data[:10]]
	in_progress = [d.in_progress for d in data[:10]]
	completed = [d.completed for d in data[:10]]
	delivered = [d.delivered for d in data[:10]]
	return {
		"data": {
			"labels": labels,
			"datasets": [
				{"name": _("In Progress"), "values": in_progress},
				{"name": _("Completed"), "values": completed},
				{"name": _("Delivered"), "values": delivered},
			]
		},
		"type": "bar",
		"colors": ["#ff9f43", "#36b37e", "#5e64ff"],
		"barOptions": {"stacked": 1},
	}
