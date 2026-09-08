"""Repair costing, on the Service Request.

Why
---
The cost and margin of a repair were computed almost entirely from the request
-- parts from Spare Parts Usage, labour from Job Assignment hours -- and then
written onto the Sales Order, which contributed only the revenue figure. Two
management reports then read them from the order.

Under the single-document model no order is raised, so those fields would never
be written and the CEO Repair Dashboard and Repair Profitability reports would
quietly empty out as new repairs came through. Nothing would error; the numbers
would simply stop arriving, which is the worst way for a report to fail.

The fields move to the request. Revenue is the better figure for it too: the
order carried the quote, whereas the request knows both the approved estimate
and, once billed, what the customer was actually charged.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


SERVICE_REQUEST_COSTING_FIELDS = {
	"Service Request": [
		{
			"fieldname": "repair_costing_section",
			"fieldtype": "Section Break",
			"label": "Repair Costing",
			"insert_after": "rework_count",
			"collapsible": 1,
		},
		{"fieldname": "spare_parts_cost", "fieldtype": "Currency", "label": "Spare Parts Cost",
		 "insert_after": "repair_costing_section", "read_only": 1},
		{"fieldname": "spare_parts_revenue", "fieldtype": "Currency", "label": "Spare Parts Revenue",
		 "insert_after": "spare_parts_cost", "read_only": 1},
		{"fieldname": "labor_cost", "fieldtype": "Currency", "label": "Labour Cost",
		 "insert_after": "spare_parts_revenue"},
		{"fieldname": "suggested_labor_cost", "fieldtype": "Currency",
		 "label": "Suggested Labour Cost", "insert_after": "labor_cost", "read_only": 1,
		 "description": "Job Assignment hours x the technician's hourly cost to company."},
		{"fieldname": "total_repair_cost", "fieldtype": "Currency", "label": "Total Repair Cost",
		 "insert_after": "suggested_labor_cost", "read_only": 1},
		{"fieldname": "technician_damage_cost", "fieldtype": "Currency",
		 "label": "Technician Damage Cost", "insert_after": "total_repair_cost", "read_only": 1,
		 "description": "Parts written off to installation damage."},

		{"fieldname": "repair_costing_column", "fieldtype": "Column Break",
		 "insert_after": "technician_damage_cost"},

		{"fieldname": "actual_billed", "fieldtype": "Currency", "label": "Actual Billed",
		 "insert_after": "repair_costing_column", "read_only": 1,
		 "description": "Invoiced total, or the approved estimate until the repair is billed."},
		{"fieldname": "suggested_total_cost", "fieldtype": "Currency",
		 "label": "Suggested Total", "insert_after": "actual_billed", "read_only": 1},
		{"fieldname": "price_override_amount", "fieldtype": "Currency",
		 "label": "Price Override Amount", "insert_after": "suggested_total_cost", "read_only": 1},
		{"fieldname": "price_override_reason", "fieldtype": "Small Text",
		 "label": "Price Override Reason", "insert_after": "price_override_amount"},
		{"fieldname": "price_overridden_by", "fieldtype": "Link", "options": "User",
		 "label": "Price Overridden By", "insert_after": "price_override_reason", "read_only": 1},
		{"fieldname": "repair_margin", "fieldtype": "Currency", "label": "Repair Margin",
		 "insert_after": "price_overridden_by", "read_only": 1},
		{"fieldname": "repair_margin_pct", "fieldtype": "Percent", "label": "Repair Margin %",
		 "insert_after": "repair_margin", "read_only": 1},
		{"fieldname": "cost_bearer", "fieldtype": "Select", "label": "Cost Bearer",
		 "options": "\nCustomer\nCompany\nOEM\nInsurance\nTechnician",
		 "insert_after": "repair_margin_pct"},
		{"fieldname": "repair_outcome", "fieldtype": "Select", "label": "Repair Outcome",
		 "options": "\nRepaired\nNot Repairable\nPartially Repaired\nReturned Unrepaired",
		 "insert_after": "cost_bearer"},
		{"fieldname": "qc_pass_datetime", "fieldtype": "Datetime", "label": "QC Pass Date & Time",
		 "insert_after": "repair_outcome", "read_only": 1},
	]
}


def create_service_request_costing_fields():
	# Every one is written after submit -- costing is computed at QC pass and
	# again when the repair is billed.
	fields = {
		"Service Request": [
			{**f, "allow_on_submit": 1}
			if f.get("fieldtype") not in ("Section Break", "Column Break") else f
			for f in SERVICE_REQUEST_COSTING_FIELDS["Service Request"]
		]
	}
	create_custom_fields(fields, update=True)
	frappe.clear_cache(doctype="Service Request")


def backfill_costing_from_service_orders():
	"""Carry existing costing across, once, so history does not go blank.

	Only fills where the request is still empty, so a figure recomputed after
	the migration is never overwritten by the older order-side one.
	"""
	if not frappe.db.has_column("Service Request", "total_repair_cost"):
		return 0

	columns = ["spare_parts_cost", "spare_parts_revenue", "labor_cost",
	           "suggested_labor_cost", "total_repair_cost", "technician_damage_cost",
	           "suggested_total_cost", "price_override_amount", "price_override_reason",
	           "price_overridden_by", "repair_margin", "repair_margin_pct",
	           "cost_bearer", "repair_outcome", "qc_pass_datetime"]
	available = [c for c in columns if frappe.db.has_column("Sales Order", c)]
	if not available:
		return 0

	selected = ", ".join(f"so.`{c}`" for c in available)
	rows = frappe.db.sql(f"""
		SELECT so.service_request AS sr, so.grand_total, {selected}
		FROM `tabSales Order` so
		WHERE so.is_service_order = 1 AND COALESCE(so.service_request, '') != ''
	""", as_dict=True)

	moved = 0
	for row in rows:
		updates = {}
		for field in available:
			value = row.get(field)
			if value in (None, "", 0):
				continue
			if frappe.db.get_value("Service Request", row.sr, field):
				continue
			updates[field] = value
		if row.get("grand_total") and not frappe.db.get_value(
				"Service Request", row.sr, "actual_billed"):
			updates["actual_billed"] = row["grand_total"]
		if updates:
			frappe.db.set_value("Service Request", row.sr, updates, update_modified=False)
			moved += 1
	return moved
