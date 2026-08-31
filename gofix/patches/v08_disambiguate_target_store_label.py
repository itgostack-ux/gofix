# Copyright (c) 2026, GoFix and contributors

"""Stop two different fields both calling themselves "Target Store".

Purchase Order carries:

  * ``set_warehouse``        — ERPNext's own destination Warehouse, relabelled
                               to "Target Store" by a Property Setter
  * ``custom_target_store``  — the store-facing CH Store picker, also labelled
                               "Target Store"

Two adjacent fields with one label, one holding a Warehouse and one holding a
CH Store. A Warehouse link renders its ``ch_display_name`` ("Palavakkam ·
Customer Device") while a CH Store renders its ``store_name`` ("Palavakkam"),
so the same form showed two different-looking values under the same caption and
there was no way to tell which one drove the receipt.

The warehouse field keeps its meaning but says what it is.

NOTE: the authoritative source for this label is ch_erp15's Customize Form file
``ch_erp15/custom/purchase_order.json``, which re-syncs on every migrate. This
patch alone was silently reverted ~8 minutes later by that sync; the JSON had to
be changed too. If you are chasing a Property Setter that keeps coming back,
look for a ``custom/<doctype>.json`` with ``sync_on_migrate`` before you write
another patch.
"""

import frappe

NEW_LABEL = "Destination Warehouse"
DESCRIPTION = (
	"The bin goods are received into. Pick the store above and this resolves to "
	"its Sellable bin — a Customer Device, Damaged or Buyback bin is custody or "
	"quarantine stock and cannot receive a purchase."
)


def execute():
	for doctype in ("Purchase Order", "Purchase Receipt"):
		if not frappe.db.exists("DocType", doctype):
			continue
		_relabel(doctype, "set_warehouse")
	frappe.db.commit()


def _relabel(doctype, fieldname):
	name = frappe.db.get_value(
		"Property Setter",
		{"doc_type": doctype, "field_name": fieldname, "property": "label"},
		"name",
	)
	if name:
		if frappe.db.get_value("Property Setter", name, "value") == NEW_LABEL:
			return
		frappe.db.set_value("Property Setter", name, "value", NEW_LABEL)
	else:
		# No relabel in play means the field already reads "Set Target
		# Warehouse" / "Accepted Warehouse" and is not ambiguous — leave it.
		return

	frappe.make_property_setter(
		{
			"doctype": doctype,
			"fieldname": fieldname,
			"property": "description",
			"value": DESCRIPTION,
			"property_type": "Text",
		},
		is_system_generated=False,
	)
	frappe.clear_cache(doctype=doctype)
	frappe.logger("gofix").info(
		f"GoFix: {doctype}.{fieldname} relabelled to {NEW_LABEL!r}"
	)
