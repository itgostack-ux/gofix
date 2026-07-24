# Copyright (c) 2025, GoFix and contributors
# For license information, please see license.txt

"""
After Install Hook - Create custom fields for multi-store setup
"""

import frappe

from gofix.setup.workflow import ensure_service_order_workflow
from gofix.setup.item_custom_fields import create_item_custom_fields


def after_install():
	"""Create custom fields after app installation"""
	create_custom_fields()
	create_item_custom_fields()
	ensure_service_order_workflow()
	from gofix.setup.permissions import ensure_default_permissions
	ensure_default_permissions()


def create_custom_fields():
	"""Create custom fields for Company and Warehouse"""
	
	custom_fields = {
		"Company": [
			{
				"fieldname": "master_hub_warehouse",
				"label": "Master Hub Warehouse",
				"fieldtype": "Link",
				"options": "Warehouse",
				"insert_after": "default_warehouse",
				"description": "Main repair center where complex repairs are handled",
				"get_query": "function() { return { filters: { company: cur_frm.doc.name, is_group: 0 } }; }"
			},
			{
				"fieldname": "supplier_return_warehouse",
				"label": "Supplier Return Warehouse",
				"fieldtype": "Link",
				"options": "Warehouse",
				"insert_after": "master_hub_warehouse",
				"description": "Warehouse for faulty spares awaiting supplier return / credit note",
				"get_query": "function() { return { filters: { company: cur_frm.doc.name, is_group: 0 } }; }"
			},
			{
				"fieldname": "damaged_stock_warehouse",
				"label": "Damaged Stock Warehouse",
				"fieldtype": "Link",
				"options": "Warehouse",
				"insert_after": "supplier_return_warehouse",
				"description": "Warehouse for spares damaged by technicians (write-off / disposal)",
				"get_query": "function() { return { filters: { company: cur_frm.doc.name, is_group: 0 } }; }"
			},
		],
		"Warehouse": [
			{
				"fieldname": "address",
				"label": "Address",
				"fieldtype": "Link",
				"options": "Address",
				"insert_after": "parent_warehouse",
				"description": "Store/warehouse physical address for GST and E-Way Bill"
			}
		]
	}
	
	for doctype, fields in custom_fields.items():
		for field in fields:
			create_custom_field(doctype, field)


def create_custom_field(doctype, field_dict):
	"""Create a custom field if it doesn't exist"""
	
	fieldname = field_dict.get("fieldname")
	
	# Check if custom field already exists
	if frappe.db.exists("Custom Field", {"dt": doctype, "fieldname": fieldname}):
		print(f"Custom field {doctype}.{fieldname} already exists")
		return
	
	# Create custom field
	custom_field = frappe.get_doc({
		"doctype": "Custom Field",
		"dt": doctype,
		**field_dict
	})
	
	custom_field.insert(ignore_permissions=True)
	print(f"Created custom field: {doctype}.{fieldname}")
