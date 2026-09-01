# Copyright (c) 2026, GoFix and contributors

"""Idempotent billing masters required by the GoFix repair workflow."""

import frappe


DEFAULT_REPAIR_ITEM = "GOFIX-REPAIR-SERVICE"
REPAIR_CATEGORY = "Repair Services"
REPAIR_SUB_CATEGORY = "Repair Services-Mobile Repair Labour"


def _ensure_services_item_group() -> None:
	# ERPNext only creates "All Item Groups" and "Services" via its
	# interactive setup wizard (erpnext/setup/setup_wizard/operations/
	# install_fixtures.py) — a site provisioned by scripting
	# bench install-app directly (no wizard) gets neither. Without this, the
	# very first migrate after install crashes here with
	# "Could not find Item Group: Services" (or, once that's papered over,
	# "Could not find Parent Item Group: All Item Groups").
	if not frappe.db.exists("Item Group", "All Item Groups"):
		frappe.get_doc({
			"doctype": "Item Group",
			"item_group_name": "All Item Groups",
			"is_group": 1,
		}).insert(ignore_permissions=True, ignore_if_duplicate=True)

	if not frappe.db.exists("Item Group", "Services"):
		frappe.get_doc({
			"doctype": "Item Group",
			"item_group_name": "Services",
			"parent_item_group": "All Item Groups",
			"is_group": 0,
		}).insert(ignore_permissions=True, ignore_if_duplicate=True)


def _ensure_repair_taxonomy() -> None:
	_ensure_services_item_group()
	income_account = frappe.db.get_value(
		"Company", {"gofix_enabled": 1}, "default_income_account"
	)
	if not frappe.db.exists("CH Category", REPAIR_CATEGORY):
		category = frappe.new_doc("CH Category")
		category.category_name = REPAIR_CATEGORY
		category.item_group = "Services"
		category.lifecycle_status = "Active"
		category.insert(ignore_permissions=True)

	if not frappe.db.exists("CH Sub Category", REPAIR_SUB_CATEGORY):
		sub_category = frappe.new_doc("CH Sub Category")
		sub_category.category = REPAIR_CATEGORY
		sub_category.sub_category_name = "Mobile Repair Labour"
		sub_category.prefix = "GFR"
		sub_category.lifecycle_status = "Active"
		sub_category.status = "Active"
		sub_category.item_nature = "Service"
		sub_category.default_uom = "Nos"
		sub_category.is_repair_labour = 1
		sub_category.gofix_service_category = "Repair"
		sub_category.income_account = income_account
		sub_category.hsn_code = "998716"
		sub_category.gst_rate = 18
		sub_category.insert(ignore_permissions=True)
	else:
		frappe.db.set_value(
			"CH Sub Category",
			REPAIR_SUB_CATEGORY,
			{"gofix_service_category": "Repair", "income_account": income_account},
			update_modified=False,
		)


def ensure_service_billing_setup() -> None:
	"""Create the explicit repair item and bind it to GoFix-enabled companies."""
	_ensure_repair_taxonomy()
	if not frappe.db.exists("Item", DEFAULT_REPAIR_ITEM):
		item = frappe.new_doc("Item")
		item.item_code = DEFAULT_REPAIR_ITEM
		item.item_name = "GoFix Repair Service"
		item.description = "Labour and service charges for an approved GoFix repair."
		item.item_group = "Services"
		item.stock_uom = "Nos"
		item.gst_hsn_code = "998716"
		item.ch_category = REPAIR_CATEGORY
		item.ch_sub_category = REPAIR_SUB_CATEGORY
		item.is_stock_item = 0
		item.is_sales_item = 1
		item.is_purchase_item = 0
		item.include_item_in_manufacturing = 0
		item.ch_approval_status = "Approved"
		item.ch_lifecycle_status = "Active"
		item.ch_plm_status = "Approved"
		item.insert(ignore_permissions=True)
	else:
		item = frappe.get_doc("Item", DEFAULT_REPAIR_ITEM)
		if (
			item.get("ch_lifecycle_status") != "Active"
			or item.get("ch_plm_status") not in ("Approved", "Active Production")
		):
			item.ch_approval_status = "Approved"
			item.ch_lifecycle_status = "Active"
			item.ch_plm_status = "Approved"
			item.flags.ignore_lifecycle_transition = True
			item.flags.ignore_plm_transition = True
			item.save(ignore_permissions=True)

	if not frappe.db.has_column("Company", "gofix_default_service_item"):
		return

	for company in frappe.get_all("Company", filters={"gofix_enabled": 1}, pluck="name"):
		if not frappe.db.get_value("Company", company, "gofix_default_service_item"):
			frappe.db.set_value(
				"Company", company, "gofix_default_service_item", DEFAULT_REPAIR_ITEM
			)
