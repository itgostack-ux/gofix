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
			{
				"fieldname": "gofix_billing_hourly_rate",
				"label": "Billable Rate / Hour (GoFix)",
				"fieldtype": "Currency",
				"insert_after": "technician_grade",
				"description": (
					"What the CUSTOMER is charged per hour of this technician's time. "
					"Distinct from custom_hourly_rate, which is the cost the company "
					"bears (CTC-derived) — billing at cost would make every repair "
					"zero-margin. Blank falls back to the Technician Grade's rate."
				),
			},
			{
				"fieldname": "gofix_service_warehouse",
				"label": "Service Center / Store",
				"fieldtype": "Link",
				"options": "Warehouse",
				"insert_after": "technician_grade",
				"description": "Warehouse (store or hub) where this technician is based — technician pickers show staff of the ticket's repair location first",
				# This is a DESCRIPTIVE attribute ("where is this technician
				# based"), never an authorisation control — access is decided by
				# CH User Scope and the role matrix. Without this flag Frappe
				# treats it as one, and with
				# System Settings -> Apply Strict User Permissions ON it emits
				#     `gofix_service_warehouse in (<allowed warehouses>)`
				# with NO `ifnull(field,'') = ''` escape. The field is empty on
				# almost every employee, so every user holding a Warehouse User
				# Permission — which ch_erp15 creates from each CH User Scope
				# store row — saw ZERO employees in every desk list, silently and
				# with no error. That broke Frappe HR, ch_hrms, and this app's own
				# technician picker (technician_intelligence.recommend_technicians
				# reads Employee through frappe.get_list). System Manager did not
				# help: that role bypasses ROLE permissions, not USER permissions.
				"ignore_user_permissions": 1,
			},
		]
	}

	create_custom_fields(custom_fields, update=True)
