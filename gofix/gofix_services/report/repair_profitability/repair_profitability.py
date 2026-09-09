# Copyright (c) 2025, GoStack and contributors
# Repair Profitability — Script Report

import frappe
from frappe import _
from frappe.utils import flt

from ch_erp15.ch_erp15.report_scope import scope_where_clause


def execute(filters=None):
	columns = get_columns()
	data = get_data(filters)
	chart = get_chart(data)
	summary = get_summary(data)
	return columns, data, None, chart, summary


def get_columns():
	return [
		{"label": _("Repair"), "fieldname": "name", "fieldtype": "Link", "options": "Service Request", "width": 175},
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
	# Anchored on the repair itself. Anchoring on the Sales Order meant this
	# report only ever saw repairs that happened to raise one, so it would have
	# gone quiet as the single-document flow took over.
	conditions = "WHERE sr.docstatus = 1"
	params = {}

	if filters and filters.get("company"):
		conditions += " AND sr.company = %(company)s"
		params["company"] = filters["company"]
	if filters and filters.get("from_date"):
		conditions += " AND DATE(sr.creation) >= %(from_date)s"
		params["from_date"] = filters["from_date"]
	if filters and filters.get("to_date"):
		conditions += " AND DATE(sr.creation) <= %(to_date)s"
		params["to_date"] = filters["to_date"]
	if filters and filters.get("warehouse"):
		conditions += " AND sr.source_warehouse = %(warehouse)s"
		params["warehouse"] = filters["warehouse"]

	# Tier 4: fail-closed scope on the repair's own endpoints.
	scope = scope_where_clause(
		warehouse_field="sr.source_warehouse",
		extra_warehouse_fields=("sr.transferred_to_store", "so.set_warehouse"),
	)
	if scope is not None:
		conditions += f" AND {scope}"

	# The geography the user asked for, alongside the scope they are
	# entitled to. Both apply: asking for a zone you cannot see returns
	# nothing, never everything.
	from gofix.report_filters import geo_conditions

	conditions += geo_conditions(filters, company_field=None,
	                             warehouse_field='sr.source_warehouse')

	query = f"""
		SELECT
			sr.name,
			sr.customer_name,
			COALESCE(sr.device_model, so.device_model, '') as device_model,
			COALESCE(sr.issue_category, so.issue_category, '') as issue_category,
			COALESCE(NULLIF(sr.actual_billed, 0), so.grand_total, so.total, 0) as revenue,
			COALESCE(NULLIF(sr.spare_parts_cost, 0), so.spare_parts_cost, 0)
				as spare_parts_cost,
			COALESCE(NULLIF(sr.labor_cost, 0), so.labor_cost, 0) as labor_cost,
			COALESCE(NULLIF(sr.total_repair_cost, 0), so.total_repair_cost, 0)
				as total_repair_cost,
			COALESCE(NULLIF(sr.repair_margin, 0), so.repair_margin, 0) as repair_margin,
			COALESCE(NULLIF(sr.repair_margin_pct, 0), so.repair_margin_pct, 0)
				as repair_margin_pct,
			COALESCE(NULLIF(sr.cost_bearer, ''), so.cost_bearer, '') as cost_bearer,
			COALESCE(NULLIF(sr.warranty_status, ''), so.warranty_status, '')
				as warranty_status
		FROM `tabService Request` sr
		LEFT JOIN `tabSales Order` so
			ON so.name = sr.service_order AND so.is_service_order = 1
		{conditions}
		ORDER BY sr.creation DESC
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
