# Copyright (c) 2026, GoFix and contributors
# Custom fields on Service Request to track Ops Hub workflow progress

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def create_service_request_ops_fields():
	"""Add analysis_confirmed, customer_confirmed, confirmation_sent_at to Service Request."""
	if not frappe.db.exists("DocType", "Service Request"):
		frappe.logger("gofix").info(
			"Skipping Service Request ops fields: DocType not yet available"
		)
		return

	custom_fields = {
		"Service Request": [
			{
				"fieldname": "ops_hub_section",
				"label": "Ops Hub Workflow",
				"fieldtype": "Section Break",
				"insert_after": "estimated_cost",
				"collapsible": 1,
			},
			{
				"fieldname": "analysis_confirmed",
				"label": "Analysis Confirmed",
				"fieldtype": "Check",
				"insert_after": "ops_hub_section",
				"read_only": 1,
				"description": "Set by GoFix Ops Hub when technician confirms issue analysis",
			},
			{
				"fieldname": "ops_col_break",
				"fieldtype": "Column Break",
				"insert_after": "analysis_confirmed",
			},
			{
				"fieldname": "customer_confirmed",
				"label": "Customer Confirmed",
				"fieldtype": "Check",
				"insert_after": "ops_col_break",
				"read_only": 1,
				"description": "Set by GoFix Ops Hub when customer approves the estimate",
			},
			{
				"fieldname": "confirmation_sent_at",
				"label": "Confirmation Sent At",
				"fieldtype": "Datetime",
				"insert_after": "customer_confirmed",
				"read_only": 1,
				"description": "Timestamp when WhatsApp confirmation was sent to customer",
			},
		]
	}

	create_custom_fields(custom_fields, update=True)
	frappe.logger("gofix").info("GoFix Ops Hub: Service Request custom fields created/updated.")
