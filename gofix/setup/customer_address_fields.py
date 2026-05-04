# Copyright (c) 2026, GoFix and contributors
# Custom fields on Customer to manage multiple billing/shipping addresses
# with single-active enforcement per address type.

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def create_customer_address_fields():
	"""Add billing_addresses child table to Customer doctype."""
	if not frappe.db.exists("DocType", "CH Customer Address"):
		frappe.logger("gofix").info(
			"Skipping Customer address fields: CH Customer Address DocType not yet available"
		)
		return

	custom_fields = {
		"Customer": [
			{
				"fieldname": "billing_addresses_section",
				"label": "Billing & Shipping Addresses",
				"fieldtype": "Section Break",
				"insert_after": "customer_primary_contact",
				"collapsible": 0,
			},
			{
				"fieldname": "billing_addresses",
				"label": "Addresses",
				"fieldtype": "Table",
				"options": "CH Customer Address",
				"insert_after": "billing_addresses_section",
				"description": (
					"Manage multiple billing and shipping addresses. "
					"Mark exactly one Billing and one Shipping address as Active — "
					"this address is pulled into Service Requests and Invoices."
				),
			},
		]
	}

	create_custom_fields(custom_fields, update=True)
	frappe.logger("gofix").info("GoFix: Customer billing_addresses custom fields created/updated.")
