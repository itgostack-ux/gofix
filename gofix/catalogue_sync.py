# Copyright (c) 2026, GoFix and contributors

"""Keep the GoFix service catalogue and the ERPNext Item master in step.

**The Item is the master.** Everything a customer can be charged for -- a spare
part and a repair service alike -- is an Item, because that is what carries the
price, the tax class (HSN/SAC) and the income account, and what ends up on the
invoice. The GoFix doctypes describe how a repair is *performed*; they mirror
the Item, they do not compete with it.

Two relationships are kept in sync here:

1. ``Item.gofix_repair_solutions`` -> ``Solution Spare Mapping``
   A spare Item declares which repairs consume it. Those rows are mirrored into
   Solution Spare Mapping so the repair flow can resolve the link from either
   side. Editing the Item is the only way to change it; the mirror is rebuilt,
   never hand-edited.

2. ``Repair Solution.service_item`` <-> ``Item``
   Every billable solution owns one non-stock service Item under the
   repair-labour sub-category, so each repair invoices as itself rather than
   collapsing into one generic line. The *rate* deliberately does not live on
   the Item: ``GoFix Pricing Rule`` already resolves labour rate by solution x
   brand x device group x warranty status, which is what lets the same repair
   cost different amounts on different devices.
"""

import frappe
from frappe import _
from frappe.utils import cstr

REPAIR_LABOUR_SUB_CATEGORY_FLAG = "is_repair_labour"
SERVICE_ITEM_GROUP = "Services"


# ── Spare Item -> Solution Spare Mapping ─────────────────────────────────────

def sync_spare_mappings_from_item(item, method=None):
	"""Mirror ``Item.gofix_repair_solutions`` into Solution Spare Mapping.

	Rebuilds the mirror for this Item only: rows for solutions the Item no
	longer lists are removed, new ones inserted, changed ones updated. Runs on
	every Item save, so a spare added in the morning is offered by the repair
	flow the same minute.
	"""
	if not _mapping_ready():
		return

	declared = {}
	# Runs on every Item save, so get out early for the ones that can never be a
	# spare -- a non-stock service Item is not issuable and never carries rows.
	if not item.get("is_stock_item") and not (item.get("gofix_repair_solutions") or []):
		return

	for row in item.get("gofix_repair_solutions") or []:
		solution = cstr(row.get("repair_solution")).strip()
		if not solution:
			continue
		declared[solution] = {
			"default_qty": row.get("default_qty") or 1,
			"is_mandatory": 1 if row.get("is_mandatory") else 0,
		}

	existing = {
		r.repair_solution: r
		for r in frappe.get_all(
			"Solution Spare Mapping",
			filters={"spare_item": item.name},
			fields=["name", "repair_solution", "default_qty", "is_mandatory", "is_active"],
		)
	}

	for solution in set(existing) - set(declared):
		frappe.delete_doc(
			"Solution Spare Mapping", existing[solution].name,
			ignore_permissions=True, force=True, delete_permanently=True,
		)

	for solution, spec in declared.items():
		if not frappe.db.exists("Repair Solution", solution):
			continue
		current = existing.get(solution)
		if current:
			changed = (
				(current.default_qty or 0) != spec["default_qty"]
				or (current.is_mandatory or 0) != spec["is_mandatory"]
				or not current.is_active
			)
			if changed:
				frappe.db.set_value(
					"Solution Spare Mapping", current.name,
					{**spec, "is_active": 1, "item_name": item.item_name},
					update_modified=False,
				)
			continue
		frappe.get_doc({
			"doctype": "Solution Spare Mapping",
			"repair_solution": solution,
			"issue_category": frappe.db.get_value("Repair Solution", solution, "issue_category"),
			"spare_item": item.name,
			"item_name": item.item_name,
			"uom": item.stock_uom,
			"is_active": 1,
			**spec,
		}).insert(ignore_permissions=True)


def repoint_spare_mappings_on_rename(doc, method=None, old_name=None, new_name=None, merge=False):
	"""Follow an Item rename so mappings never dangle."""
	if not old_name or old_name == new_name or not _mapping_ready():
		return
	frappe.db.sql(
		"UPDATE `tabSolution Spare Mapping` SET spare_item = %s WHERE spare_item = %s",
		(new_name, old_name),
	)


# ── Repair Solution <-> service Item ─────────────────────────────────────────

def ensure_service_item(solution, commit=False):
	"""Return the service Item for ``solution``, creating it if needed.

	Idempotent. Does nothing for a non-billable solution.
	"""
	if isinstance(solution, str):
		solution = frappe.get_doc("Repair Solution", solution)
	if not solution.get("is_billable", 1):
		return None
	if solution.get("service_item") and frappe.db.exists("Item", solution.service_item):
		return solution.service_item

	sub_category = _repair_labour_sub_category()
	item_code = _service_item_code(solution)

	if not frappe.db.exists("Item", item_code):
		item = frappe.new_doc("Item")
		item.item_code = item_code
		item.item_name = solution.solution_name[:140]
		item.description = solution.get("description") or solution.solution_name
		item.item_group = SERVICE_ITEM_GROUP
		item.stock_uom = "Nos"
		item.is_stock_item = 0
		item.is_sales_item = 1
		item.is_purchase_item = 0
		if sub_category:
			item.ch_sub_category = sub_category
			cat, hsn = frappe.db.get_value(
				"CH Sub Category", sub_category, ["category", "hsn_code"]
			) or (None, None)
			if cat:
				item.ch_category = cat
			if hsn:
				item.gst_hsn_code = hsn
		item.flags.ignore_permissions = True
		item.insert(ignore_permissions=True)

	if solution.get("service_item") != item_code:
		frappe.db.set_value(
			"Repair Solution", solution.name, "service_item", item_code, update_modified=False
		)
		solution.service_item = item_code
	if commit:
		frappe.db.commit()
	return item_code


def on_repair_solution_update(doc, method=None):
	"""Provision the service Item and keep its name aligned with the solution."""
	item_code = ensure_service_item(doc)
	if not item_code:
		return
	name = doc.solution_name[:140]
	if frappe.db.get_value("Item", item_code, "item_name") != name:
		frappe.db.set_value("Item", item_code, "item_name", name, update_modified=False)


def validate_repair_solution(doc, method=None):
	"""Catch the catalogue inconsistencies that silently break a repair."""
	if doc.get("service_item"):
		is_stock, disabled = frappe.db.get_value(
			"Item", doc.service_item, ["is_stock_item", "disabled"]
		) or (None, None)
		if is_stock:
			frappe.throw(
				_("{0} is a stock item — a repair service must be invoiced as a non-stock Item.")
				.format(doc.service_item),
				title=_("Invalid Service Item"),
			)
		if disabled:
			frappe.throw(
				_("Service Item {0} is disabled.").format(doc.service_item),
				title=_("Invalid Service Item"),
			)

	# requires_spare is what the repair flow trusts to decide whether to ask for
	# a part, so a solution promising a spare it has no mapping for strands the job.
	if doc.get("requires_spare") and not doc.is_new():
		mapped = frappe.db.count(
			"Solution Spare Mapping", {"repair_solution": doc.name, "is_active": 1}
		)
		if not mapped:
			frappe.msgprint(
				_("{0} is marked as requiring a spare but no spare Item lists it under "
				  "<b>Serves Repair Solutions</b>. Technicians will not be offered a part.")
				.format(doc.name),
				title=_("No Spare Mapped"),
				indicator="orange",
			)


def validate_solution_spare_mapping(doc, method=None):
	"""A mapped spare must be something the warehouse can actually issue."""
	is_stock, disabled = frappe.db.get_value(
		"Item", doc.spare_item, ["is_stock_item", "disabled"]
	) or (None, None)
	if disabled:
		frappe.throw(
			_("Spare {0} is disabled and cannot be mapped to a repair.").format(doc.spare_item),
			title=_("Disabled Spare"),
		)
	if is_stock == 0:
		frappe.throw(
			_("{0} is a non-stock (service) Item and cannot be issued as a spare.")
			.format(doc.spare_item),
			title=_("Not a Stock Item"),
		)


# ── helpers ──────────────────────────────────────────────────────────────────

def _mapping_ready() -> bool:
	return bool(
		frappe.db.exists("DocType", "Solution Spare Mapping")
		and frappe.get_meta("Item").get_field("gofix_repair_solutions")
	)


def _repair_labour_sub_category():
	if not frappe.db.has_column("CH Sub Category", REPAIR_LABOUR_SUB_CATEGORY_FLAG):
		return None
	return frappe.db.get_value("CH Sub Category", {REPAIR_LABOUR_SUB_CATEGORY_FLAG: 1}, "name")


def _service_item_code(solution) -> str:
	"""Stable, readable code: GFR-SCREEN-REPLACEMENT."""
	base = (solution.get("solution_code") or solution.solution_name or solution.name)
	slug = "".join(ch if ch.isalnum() else "-" for ch in cstr(base).upper())
	while "--" in slug:
		slug = slug.replace("--", "-")
	return f"GFR-{slug.strip('-')}"[:140]
