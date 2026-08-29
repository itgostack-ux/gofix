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
	if not frappe.db.exists("DocType", "GoFix Spare Compatible Model") or not frappe.db.exists(
		"DocType", "GoFix Item Repair Solution"
	):
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
				"description": "Exact device models this spare fits (most specific tier — overrides brand/category matching). Same spare may list many models.",
			},
			{
				"fieldname": "gofix_repair_solutions",
				"label": "Serves Repair Solutions",
				"fieldtype": "Table MultiSelect",
				"options": "GoFix Item Repair Solution",
				"insert_after": "gofix_compatible_models",
				"description": (
					"Which GoFix repairs consume this spare. The Item is the master: "
					"these rows are mirrored into Solution Spare Mapping, so a spare "
					"added here shows up in the repair flow immediately."
				),
			},
			{
				"fieldname": "gofix_spare_grade",
				"label": "Spare Grade",
				"fieldtype": "Select",
				"options": "\nOEM\nOEM Equivalent\nAftermarket\nRefurbished",
				"insert_after": "gofix_repair_solutions",
				"description": (
					"Part tier. The same repair is normally offered at 2-3 price points "
					"by stocking one Item per grade -- an OEM screen and an aftermarket "
					"screen are separate SKUs, both mapped to the same repair."
				),
			},
			{
				"fieldname": "gofix_part_warranty_days",
				"label": "Part Warranty (Days)",
				"fieldtype": "Int",
				"insert_after": "gofix_spare_grade",
				"description": (
					"Warranty this part carries. The repair's warranty is the SHORTEST "
					"of the parts fitted and the workmanship warranty on the repair, "
					"which is how an aftermarket part shortens the customer's cover."
				),
			},
			{
				"fieldname": "gofix_universal_spare",
				"label": "Universal Spare / Consumable",
				"fieldtype": "Check",
				"insert_after": "gofix_compatible_models",
				"description": "Tick for consumables (thermal paste, adhesive, screws) usable on any device — bypasses brand/category/model matching.",
			},
		],
		# The spare's own catalogue category declares which DEVICE category it
		# serves (Laptop Spares → Laptops). Drives the category tier of spare
		# compatibility so a laptop keyboard never shows for a mobile repair.
		"CH Category": [
			{
				"fieldname": "gofix_spares_for_category",
				"label": "Spares For (Device Category)",
				"fieldtype": "Link",
				"options": "CH Category",
				"insert_after": "item_group",
				"description": "If this is a spares category, the device category its parts serve (e.g. Laptop Spares → Laptops).",
			},
		],
	}

	create_custom_fields(custom_fields, update=True)
	frappe.logger("gofix").info("GoFix: Item spare-compatibility custom fields created/updated.")

	# The spares→device category mapping depends on the CH Category custom
	# field created just above — seed it here (idempotent) because the patch
	# in patches.txt runs BEFORE after_migrate creates the column.
	try:
		from gofix.patches.seed_gofix_service_masters import _seed_spare_category_mapping

		_seed_spare_category_mapping()
	except Exception:
		frappe.log_error(frappe.get_traceback(), "spare category mapping seed failed")
