# Copyright (c) 2026, GoStack and contributors

"""Seed the default labour charge so a repair invoice is never billed with no labour.

The SR-side service invoice adds a labour line even when the Ops Hub flow left
``service_items`` empty; when no priced source resolves, it uses this default
(₹200). Set only if the field is currently blank, so an ops-tuned value survives.
"""

import frappe


def execute():
	# GoFix Settings is a Single doctype (no table), so probe the field via meta.
	if not frappe.get_meta("GoFix Settings").get_field("default_labour_charge"):
		return
	if not frappe.db.get_single_value("GoFix Settings", "default_labour_charge"):
		frappe.db.set_single_value("GoFix Settings", "default_labour_charge", 200)
		frappe.db.commit()
		frappe.logger("gofix").info("GoFix: seeded default_labour_charge = 200")
