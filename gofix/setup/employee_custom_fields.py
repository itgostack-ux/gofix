# Copyright (c) 2025, GoFix and contributors
# Custom field for Employee to support technician grade mapping

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def create_employee_custom_fields():
	"""Create technician_grade custom field in Employee"""
	if not frappe.db.exists("DocType", "Technician Grade"):
		frappe.logger("gofix").info(
			"Skipping Employee custom fields: Technician Grade DocType not yet available"
		)
		return

	custom_fields = {
		"Employee": [
			{
				"fieldname": "technician_grade",
				"label": "Technician Grade",
				"fieldtype": "Link",
				"options": "Technician Grade",
				"insert_after": "designation",
				"description": "Technician skill grade for GoFix service assignment"
			},
		]
	}

	create_custom_fields(custom_fields, update=True)
