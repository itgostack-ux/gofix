# Copyright (c) 2026, GoStack and contributors

"""Device taxonomy comes from the item master only.

The self check-in tablet had its own device-type and brand masters (GoFix
Device Type, GoFix Brand Option) next to the item master's CH Category /
Brand / CH Model. They are gone: the repairable categories are flagged on
CH Category, brands and models are read from CH Model, and the symptom
catalogue (GoFix Symptom) is keyed by CH Category instead of the old label.

Steps (idempotent, post-model-sync):
1. flag CH Category rows from the old GoFix Device Type mapping (or the
   static fallback) with icon and display order;
2. rename GoFix Symptom.device_type -> device_category and map the values
   (Mobile -> Smart Phones ...; Other -> blank = generic);
3. drop the two masters, their tables, permission rows and workspace links.
"""

import frappe

_FALLBACK = {
	"Mobile": ("Smart Phones", "\U0001f4f1", 10),
	"Tablet": ("Tablets", "\U0001f4f2", 20),
	"Laptop": ("Laptops", "\U0001f4bb", 30),
	"Smartwatch": ("Watches", "⌚", 40),
	"Smart Watch": ("Watches", "⌚", 40),
}
DEAD_DOCTYPES = ("GoFix Brand Option", "GoFix Device Type")


def execute():
	mapping = _device_type_mapping()
	_flag_repairable_categories(mapping)
	_rekey_symptoms(mapping)
	_drop_dead_masters()
	frappe.clear_cache()


def _device_type_mapping() -> dict:
	"""old label -> (CH Category, icon, order); blank category for Other."""
	mapping = {k: v for k, v in _FALLBACK.items()}
	if frappe.db.table_exists("GoFix Device Type"):
		for row in frappe.db.sql(
			"SELECT name, ch_category, icon, display_order FROM `tabGoFix Device Type`", as_dict=True
		):
			if row.ch_category:
				mapping[row.name] = (row.ch_category, row.icon, row.display_order)
			else:
				mapping[row.name] = (None, None, None)
	return mapping


def _flag_repairable_categories(mapping: dict) -> None:
	if not frappe.db.has_column("CH Category", "is_repairable_device"):
		return
	for _label, (category, icon, order) in mapping.items():
		if not category or not frappe.db.exists("CH Category", category):
			continue
		current = frappe.db.get_value(
			"CH Category", category, ["is_repairable_device", "device_icon", "kiosk_display_order"], as_dict=True)
		values = {}
		if not current.is_repairable_device:
			values["is_repairable_device"] = 1
		if icon and not current.device_icon:
			values["device_icon"] = icon
		if order and not current.kiosk_display_order:
			values["kiosk_display_order"] = order
		if values:
			frappe.db.set_value("CH Category", category, values, update_modified=False)


def _rekey_symptoms(mapping: dict) -> None:
	if not frappe.db.table_exists("GoFix Symptom"):
		return
	if frappe.db.has_column("GoFix Symptom", "device_type"):
		from frappe.model.utils.rename_field import rename_field

		rename_field("GoFix Symptom", "device_type", "device_category")
	if not frappe.db.has_column("GoFix Symptom", "device_category"):
		return
	for label, (category, _icon, _order) in mapping.items():
		frappe.db.sql(
			"UPDATE `tabGoFix Symptom` SET device_category = %s WHERE device_category = %s",
			(category, label),
		)
	frappe.db.sql(
		"""
		UPDATE `tabGoFix Symptom` s
		SET s.device_category = NULL
		WHERE IFNULL(s.device_category, '') <> ''
		  AND NOT EXISTS (SELECT 1 FROM `tabCH Category` c WHERE c.name = s.device_category)
		"""
	)


def _drop_dead_masters() -> None:
	for doctype in DEAD_DOCTYPES:
		frappe.db.delete("Custom DocPerm", {"parent": doctype})
		if frappe.db.exists("DocType", doctype):
			frappe.delete_doc("DocType", doctype, force=True, ignore_missing=True, ignore_permissions=True)
		frappe.db.sql_ddl(f"DROP TABLE IF EXISTS `tab{doctype}`")
	frappe.db.delete("Workspace Link", {"link_to": ("in", list(DEAD_DOCTYPES))})
	frappe.db.delete("Workspace Shortcut", {"link_to": ("in", list(DEAD_DOCTYPES))})
