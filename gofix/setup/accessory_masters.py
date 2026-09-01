# Copyright (c) 2026, GoFix and contributors

"""Seed the accessory catalogue backing Service Request's multi-select.

Accessories used to be typed free-hand into a Small Text, which made them
impossible to report on and inconsistent between advisors ("charger", "Charger",
"chrgr"). These are the items a counter actually takes custody of at intake, so
the list is deliberately short -- anything rarer still goes in the notes field
that sits beside the multi-select.
"""

import frappe


DEFAULT_ACCESSORIES = (
	"Charger",
	"Charging Cable",
	"Power Adapter",
	"Earphones",
	"Back Cover / Case",
	"Screen Guard",
	"SIM Tray",
	"SIM Card",
	"Memory Card",
	"Battery",
	"Stylus",
	"Original Box",
	"Purchase Invoice / Bill",
	"Warranty Card",
)


def ensure_accessory_masters() -> None:
	if not frappe.db.table_exists("GoFix Accessory"):
		return

	for name in DEFAULT_ACCESSORIES:
		if frappe.db.exists("GoFix Accessory", name):
			continue
		doc = frappe.new_doc("GoFix Accessory")
		doc.accessory_name = name
		doc.is_active = 1
		doc.insert(ignore_permissions=True)
