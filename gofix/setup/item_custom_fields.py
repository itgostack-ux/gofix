# Copyright (c) 2026, GoFix and contributors
# Custom fields on Item to declare which device models a spare part is compatible with.
# Used by the GoFix repair flow to only offer spares that fit the device being repaired.

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def create_item_custom_fields():
	"""Add the 'Spare Compatibility' child table to Item.

	Lets users list the device models a spare fits while creating/editing the
	spare Item. An empty list means the spare is treated as universal (fits any
	device); a non-empty list restricts the spare to the listed device models.
	"""
	if not frappe.db.exists("DocType", "GoFix Spare Compatible Model"):
		frappe.logger("gofix").info(
			"Skipping Item spare-compatibility fields: child DocType not yet available"
		)
		return

	custom_fields = {
		"Item": [
			{
				"fieldname": "gofix_spare_compatibility_section",
				"label": "Spare Compatibility (GoFix)",
				"fieldtype": "Section Break",
				"insert_after": "description",
				"collapsible": 1,
				"depends_on": "eval:doc.is_stock_item",
				"description": "Restrict this spare to specific device models for GoFix repairs",
			},
			{
				"fieldname": "gofix_compatible_models",
				"label": "Compatible Device Models",
				"fieldtype": "Table",
				"options": "GoFix Spare Compatible Model",
				"insert_after": "gofix_spare_compatibility_section",
				"description": "Device models this spare part fits. Leave empty if the spare fits any device.",
			},
		]
	}

	create_custom_fields(custom_fields, update=True)
	frappe.logger("gofix").info("GoFix: Item spare-compatibility custom fields created/updated.")
