# Copyright (c) 2026, GoFix and contributors

"""Idempotent billing masters required by the GoFix repair workflow."""

import frappe


DEFAULT_REPAIR_ITEM = "GOFIX-REPAIR-SERVICE"
REPAIR_CATEGORY = "Repair Services"
REPAIR_SUB_CATEGORY = "Repair Services-Mobile Repair Labour"
REPAIR_SAC_CODE = "998716"

# The fields that make this row a *usable* repair-labour sub-category. Item
# governance refuses to activate a Service Item whose sub-category is missing
# income_account or gofix_service_category, and india_compliance refuses to save
# one with no HSN -- so a row lacking any of these is not a partial config, it is
# a broken one that stops service Items being created at all.
_REPAIR_SUB_CATEGORY_PROFILE = {
	"is_repair_labour": 1,
	"item_nature": "Service",
	"hsn_code": REPAIR_SAC_CODE,
	"gst_rate": 18,
	"default_uom": "Nos",
	"gofix_service_category": "Repair",
	"lifecycle_status": "Active",
	"status": "Active",
}


def _repair_income_account():
	"""Income account for repair revenue.

	Prefers a GoFix-enabled company but falls back to any company that has a
	default: this seeder also runs from a patch on a fresh site, where nobody has
	ticked ``gofix_enabled`` yet and the custom field may not even exist. A
	sub-category written with a blank income account can never activate a Service
	Item, so guessing beats leaving it empty.
	"""
	if frappe.db.has_column("Company", "gofix_enabled"):
		account = frappe.db.get_value(
			"Company", {"gofix_enabled": 1}, "default_income_account"
		)
		if account:
			return account
	return frappe.db.get_value(
		"Company", {"default_income_account": ("!=", "")}, "default_income_account"
	)


def _ensure_erpnext_baseline_defaults() -> None:
	# ERPNext's Item Groups (All Item Groups, Services, ...), UOMs (Nos, Kg,
	# Unit, ...), Territories, Customer/Supplier Groups etc. are normally
	# seeded by its interactive Setup Wizard
	# (erpnext/setup/setup_wizard/operations/install_fixtures.py). A site
	# provisioned by scripting bench install-app directly (no wizard) never
	# gets any of it — this function's own steps below assume it's all
	# there, and previously failed one missing default at a time as each
	# one was reached ("Item Group: Services", then "Item Group: All Item
	# Groups", then "UOM: Nos", ...). Rather than hand-seed each one as it
	# surfaces, defensively run the wizard's own installer once, idempotently
	# (it uses insert(..., ignore_if_duplicate=True) per record, so it's
	# safe to call even on a site that already has some or all of this).
	if frappe.db.exists("Item Group", "All Item Groups"):
		return
	from erpnext.setup.setup_wizard.operations.install_fixtures import install as install_erpnext_defaults

	install_erpnext_defaults()


def _ensure_repair_taxonomy() -> None:
	# Both sides of this were right about different halves of the problem, so
	# both stay. Seed ERPNext's own company defaults first -- on a fresh site
	# there may be no default income account to read at all -- then resolve
	# through _repair_income_account(), which falls back to any company with a
	# default when nobody has ticked gofix_enabled yet, and tolerates the custom
	# field not existing. Reading default_income_account directly would return
	# None on exactly the fresh-install path this seeder runs from.
	_ensure_erpnext_baseline_defaults()
	income_account = _repair_income_account()
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
		sub_category.hsn_code = REPAIR_SAC_CODE
		sub_category.gst_rate = 18
		sub_category.insert(ignore_permissions=True)
	else:
		_heal_repair_sub_category(income_account)


def _heal_repair_sub_category(income_account) -> None:
	"""Fill in whatever the existing row is missing, without overwriting ops.

	The row may pre-date this profile, or have arrived by import or by hand, and
	be missing the flag this app resolves it by or the accounts governance
	demands. Only blanks are filled. That matters in both directions: the
	previous version of this function assigned ``income_account`` unconditionally,
	so a migrate run before any company was GoFix-enabled resolved it to None and
	*wiped* a good account.
	"""
	profile = dict(_REPAIR_SUB_CATEGORY_PROFILE)
	if income_account:
		profile["income_account"] = income_account

	# A field this build added may not have a column yet when a patch calls us
	# before its custom field is installed; reading one raises 1054 rather than
	# returning None.
	fields = [f for f in profile if frappe.db.has_column("CH Sub Category", f)]
	if not fields:
		return

	current = frappe.db.get_value(
		"CH Sub Category", REPAIR_SUB_CATEGORY, fields, as_dict=True
	) or {}
	patch = {f: profile[f] for f in fields if not current.get(f)}

	if patch:
		frappe.db.set_value(
			"CH Sub Category", REPAIR_SUB_CATEGORY, patch, update_modified=False
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
