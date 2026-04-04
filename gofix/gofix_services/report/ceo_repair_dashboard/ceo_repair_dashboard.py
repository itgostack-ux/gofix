# Copyright (c) 2025, GoStack and contributors
# CEO Repair Dashboard — Suggested vs Actual pricing, technician damage costs,
# price override analysis, rework tracking, and transfer summary.

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
		{"label": _("Service Order"), "fieldname": "service_order", "fieldtype": "Link",
		 "options": "Sales Order", "width": 140},
		{"label": _("Customer"), "fieldname": "customer", "fieldtype": "Link",
		 "options": "Customer", "width": 140},
		{"label": _("Device"), "fieldname": "device_model", "width": 120},
		{"label": _("Issue"), "fieldname": "issue_category", "fieldtype": "Link",
		 "options": "Issue Category", "width": 100},
		{"label": _("Spare Parts (₹)"), "fieldname": "spare_parts_revenue",
		 "fieldtype": "Currency", "width": 110},
		{"label": _("Suggested Labor (₹)"), "fieldname": "suggested_labor_cost",
		 "fieldtype": "Currency", "width": 120},
		{"label": _("Suggested Total (₹)"), "fieldname": "suggested_total_cost",
		 "fieldtype": "Currency", "width": 120},
		{"label": _("Actual Billed (₹)"), "fieldname": "actual_billed",
		 "fieldtype": "Currency", "width": 110},
		{"label": _("Override (Δ)"), "fieldname": "price_override_amount",
		 "fieldtype": "Currency", "width": 100},
		{"label": _("Override Reason"), "fieldname": "price_override_reason", "width": 140},
		{"label": _("Overridden By"), "fieldname": "price_overridden_by",
		 "fieldtype": "Link", "options": "User", "width": 130},
		{"label": _("Tech Damage (₹)"), "fieldname": "technician_damage_cost",
		 "fieldtype": "Currency", "width": 110},
		{"label": _("Rework Count"), "fieldname": "rework_count",
		 "fieldtype": "Int", "width": 80},
		{"label": _("Cost Bearer"), "fieldname": "cost_bearer", "width": 110},
		{"label": _("Warranty"), "fieldname": "warranty_status", "width": 100},
		{"label": _("Transfer Status"), "fieldname": "transfer_status", "width": 110},
		{"label": _("QC→Delivery (hrs)"), "fieldname": "qc_to_delivery_hours",
		 "fieldtype": "Float", "precision": 1, "width": 110},
	]


def get_data(filters):
	conditions = "so.is_service_order = 1 AND so.docstatus = 1"
	values = {}

	if filters:
		if filters.get("from_date"):
			conditions += " AND so.transaction_date >= %(from_date)s"
			values["from_date"] = filters["from_date"]
		if filters.get("to_date"):
			conditions += " AND so.transaction_date <= %(to_date)s"
			values["to_date"] = filters["to_date"]
		if filters.get("company"):
			conditions += " AND so.company = %(company)s"
			values["company"] = filters["company"]

	data = frappe.db.sql("""
		SELECT
			so.name as service_order,
			so.customer,
			COALESCE(so.device_model, '') as device_model,
			COALESCE(so.issue_category, '') as issue_category,
			COALESCE(so.spare_parts_revenue, 0) as spare_parts_revenue,
			COALESCE(so.suggested_labor_cost, 0) as suggested_labor_cost,
			COALESCE(so.suggested_total_cost, 0) as suggested_total_cost,
			COALESCE(so.grand_total, 0) as actual_billed,
			COALESCE(so.price_override_amount, 0) as price_override_amount,
			COALESCE(so.price_override_reason, '') as price_override_reason,
			COALESCE(so.price_overridden_by, '') as price_overridden_by,
			COALESCE(so.technician_damage_cost, 0) as technician_damage_cost,
			COALESCE(so.rework_count, 0) as rework_count,
			COALESCE(so.cost_bearer, '') as cost_bearer,
			COALESCE(so.warranty_status, '') as warranty_status,
			COALESCE(sr.transfer_status, '') as transfer_status,
			so.qc_pass_datetime,
			so.delivered_datetime
		FROM `tabSales Order` so
		LEFT JOIN `tabService Request` sr ON sr.name = so.service_request
		WHERE {conditions}
		ORDER BY so.transaction_date DESC
	""".format(conditions=conditions), values, as_dict=1)  # noqa: UP032

	# Calculate QC to delivery hours
	for row in data:
		if row.qc_pass_datetime and row.delivered_datetime:
			from frappe.utils import time_diff_in_hours, get_datetime
			row["qc_to_delivery_hours"] = round(
				time_diff_in_hours(
					get_datetime(row.delivered_datetime),
					get_datetime(row.qc_pass_datetime)
				), 1)
		else:
			row["qc_to_delivery_hours"] = None

		# Remove raw datetime fields from output
		row.pop("qc_pass_datetime", None)
		row.pop("delivered_datetime", None)

	return data


def get_chart(data):
	if not data:
		return None

	# Group by: overrides given vs no override
	override_count = sum(1 for d in data if abs(flt(d.get("price_override_amount"))) > 1)
	no_override = len(data) - override_count
	damage_count = sum(1 for d in data if flt(d.get("technician_damage_cost")) > 0)
	rework_count = sum(1 for d in data if (d.get("rework_count") or 0) > 0)

	return {
		"data": {
			"labels": ["No Override", "Price Overridden", "Tech Damage", "Rework"],
			"datasets": [
				{"name": "Count", "values": [no_override, override_count, damage_count, rework_count]}
			]
		},
		"type": "bar",
		"fieldtype": "Int",
	}


def get_summary(data):
	if not data:
		return []

	total_suggested = sum(flt(d.get("suggested_total_cost")) for d in data)
	total_actual = sum(flt(d.get("actual_billed")) for d in data)
	total_override = sum(flt(d.get("price_override_amount")) for d in data)
	total_damage = sum(flt(d.get("technician_damage_cost")) for d in data)
	total_reworks = sum(d.get("rework_count", 0) for d in data)
	override_count = sum(1 for d in data if abs(flt(d.get("price_override_amount"))) > 1)

	return [
		{"value": len(data), "label": _("Total Service Orders"), "datatype": "Int"},
		{"value": total_suggested, "label": _("Total Suggested"), "datatype": "Currency",
		 "indicator": "blue"},
		{"value": total_actual, "label": _("Total Billed"), "datatype": "Currency",
		 "indicator": "green"},
		{"value": total_override, "label": _("Total Override (Δ)"), "datatype": "Currency",
		 "indicator": "orange" if total_override < 0 else "red"},
		{"value": override_count, "label": _("Overrides Given"), "datatype": "Int",
		 "indicator": "orange"},
		{"value": total_damage, "label": _("Tech Damage Cost"), "datatype": "Currency",
		 "indicator": "red"},
		{"value": total_reworks, "label": _("Total Reworks"), "datatype": "Int",
		 "indicator": "orange"},
	]
