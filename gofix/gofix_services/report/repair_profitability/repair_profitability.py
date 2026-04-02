# Copyright (c) 2025, GoStack and contributors
# Repair Profitability — Script Report

import frappe
from frappe import _
from frappe.utils import flt


def execute(filters=None):
	columns = get_columns()
	data = get_data(filters)
	chart = get_chart(data)
	summary = get_summary(data)
	return columns, data, None, chart, summary


def get_columns():
	return [
		{"label": _("Service Order"), "fieldname": "name", "fieldtype": "Link", "options": "Sales Order", "width": 160},
		{"label": _("Customer"), "fieldname": "customer_name", "fieldtype": "Data", "width": 160},
		{"label": _("Device"), "fieldname": "device_model", "fieldtype": "Data", "width": 140},
		{"label": _("Issue"), "fieldname": "issue_category", "fieldtype": "Data", "width": 120},
		{"label": _("Revenue (₹)"), "fieldname": "revenue", "fieldtype": "Currency", "width": 120},
		{"label": _("Parts Cost (₹)"), "fieldname": "spare_parts_cost", "fieldtype": "Currency", "width": 120},
		{"label": _("Labor Cost (₹)"), "fieldname": "labor_cost", "fieldtype": "Currency", "width": 110},
		{"label": _("Total Cost (₹)"), "fieldname": "total_repair_cost", "fieldtype": "Currency", "width": 110},
		{"label": _("Margin (₹)"), "fieldname": "repair_margin", "fieldtype": "Currency", "width": 100},
		{"label": _("Margin %"), "fieldname": "repair_margin_pct", "fieldtype": "Percent", "width": 100},
		{"label": _("Cost Bearer"), "fieldname": "cost_bearer", "fieldtype": "Data", "width": 130},
		{"label": _("Warranty"), "fieldname": "warranty_status", "fieldtype": "Data", "width": 110},
	]


def get_data(filters):
	conditions = "WHERE so.is_service_order = 1 AND so.docstatus = 1"
	params = {}

	if filters and filters.get("company"):
		conditions += " AND so.company = %(company)s"
		params["company"] = filters["company"]
	if filters and filters.get("from_date"):
		conditions += " AND so.transaction_date >= %(from_date)s"
		params["from_date"] = filters["from_date"]
	if filters and filters.get("to_date"):
		conditions += " AND so.transaction_date <= %(to_date)s"
		params["to_date"] = filters["to_date"]
	if filters and filters.get("warehouse"):
		conditions += " AND so.set_warehouse = %(warehouse)s"
		params["warehouse"] = filters["warehouse"]

	query = f"""
		SELECT
			so.name,
			so.customer_name,
			so.device_model,
			so.issue_category,
			COALESCE(so.grand_total, so.total, 0) as revenue,
			COALESCE(so.spare_parts_cost, 0) as spare_parts_cost,
			COALESCE(so.labor_cost, 0) as labor_cost,
			COALESCE(so.total_repair_cost, 0) as total_repair_cost,
			COALESCE(so.repair_margin, 0) as repair_margin,
			COALESCE(so.repair_margin_pct, 0) as repair_margin_pct,
			COALESCE(so.cost_bearer, '') as cost_bearer,
			COALESCE(so.warranty_status, '') as warranty_status
		FROM `tabSales Order` so
		{conditions}
		ORDER BY so.transaction_date DESC
	"""

	return frappe.db.sql(query, params, as_dict=True)


def get_chart(data):
	if not data:
		return None

	# Aggregate by issue category
	cat_data = {}
	for d in data:
		cat = d.issue_category or "Other"
		if cat not in cat_data:
			cat_data[cat] = {"revenue": 0, "cost": 0, "count": 0}
		cat_data[cat]["revenue"] += flt(d.revenue)
		cat_data[cat]["cost"] += flt(d.total_repair_cost)
		cat_data[cat]["count"] += 1

	labels = list(cat_data.keys())[:10]
	revenue = [cat_data[l]["revenue"] for l in labels]
	cost = [cat_data[l]["cost"] for l in labels]

	return {
		"data": {
			"labels": labels,
			"datasets": [
				{"name": _("Revenue"), "values": revenue},
				{"name": _("Cost"), "values": cost},
			]
		},
		"type": "bar",
		"colors": ["#36b37e", "#ff5630"],
	}


def get_summary(data):
	if not data:
		return []

	total_revenue = sum(flt(d.revenue) for d in data)
	total_cost = sum(flt(d.total_repair_cost) for d in data)
	total_margin = total_revenue - total_cost
	avg_margin = (total_margin / total_revenue * 100) if total_revenue else 0

	return [
		{"label": _("Total Revenue"), "value": total_revenue, "datatype": "Currency", "indicator": "green"},
		{"label": _("Total Cost"), "value": total_cost, "datatype": "Currency", "indicator": "red"},
		{"label": _("Total Margin"), "value": total_margin, "datatype": "Currency",
		 "indicator": "green" if total_margin > 0 else "red"},
		{"label": _("Avg Margin %"), "value": f"{avg_margin:.1f}%", "indicator": "blue"},
		{"label": _("Total Jobs"), "value": len(data), "indicator": "blue"},
	]
