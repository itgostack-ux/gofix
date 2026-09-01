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
# SAC 998716 -- maintenance and repair of telecommunication equipment. Used only
# when the repair-labour sub-category carries no code of its own: india_compliance
# rejects any sales Item without an HSN/SAC, so a service Item built without one
# cannot be saved at all, and the failure surfaces as a dead migrate rather than
# as the configuration gap it is.
DEFAULT_SERVICE_HSN = "998716"


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

	sub_category = _ensure_repair_labour_sub_category()
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
		# Governance blocks Draft items from being sold or stocked, so a service
		# item created Draft can never reach an invoice. These are generated from
		# an approved Repair Solution, so they are born Active.
		# Two independent governance gates block a Draft/NPI item from being
		# stocked or sold. A service item generated from an approved Repair
		# Solution has already passed the product decision, so it is born past
		# both — otherwise it could never reach an invoice.
		if item.meta.get_field("ch_lifecycle_status"):
			item.ch_lifecycle_status = "Active"
		if item.meta.get_field("ch_plm_status"):
			item.ch_plm_status = "Active Production"
		if sub_category:
			item.ch_sub_category = sub_category
			cat = frappe.db.get_value("CH Sub Category", sub_category, "category")
			if cat:
				item.ch_category = cat
		# Set the code unconditionally, not just when a sub-category resolved:
		# india_compliance makes HSN/SAC mandatory on every sales Item, and item
		# governance demands it again to go Active. Leaving it blank does not
		# produce a draft Item to fix later -- it produces an exception that takes
		# down whatever was doing the saving, up to and including a bench migrate.
		hsn = _service_hsn_for(sub_category)
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


def resolve_solution(label, issue_category=None):
	"""Docname of a Repair Solution from a docname, a solution code, or a label.

	The document name is the ``solution_code``. Seed maps and mapping tables
	written before that re-key refer to solutions by their old
	label-as-docname, so every lookup that starts from a human-written string
	goes through here and keeps working on either side of the change.
	"""
	label = cstr(label).strip()
	if not label:
		return None
	if frappe.db.exists("Repair Solution", label):
		return label
	found = frappe.db.get_value("Repair Solution", {"solution_code": label}, "name")
	if found:
		return found
	filters = {"solution_name": label}
	if issue_category:
		filters["issue_category"] = issue_category
	return frappe.db.get_value("Repair Solution", filters, "name")


# ── helpers ──────────────────────────────────────────────────────────────────

def _mapping_ready() -> bool:
	return bool(
		frappe.db.exists("DocType", "Solution Spare Mapping")
		and frappe.get_meta("Item").get_field("gofix_repair_solutions")
	)


def _repair_labour_sub_category():
	"""The sub-category every repair service Item is filed under.

	Resolution is deliberately ordered. The flag is not unique -- test fixtures
	and hand-made rows carry it too -- and an unordered lookup on the flag alone
	picks an arbitrary winner, which is how service Items ended up filed under a
	``_Test`` sub-category carrying the wrong SAC and no income account. The row
	this app seeds wins; anything else is a fallback, oldest first, and fixture
	rows are never eligible.
	"""
	if not frappe.db.has_column("CH Sub Category", REPAIR_LABOUR_SUB_CATEGORY_FLAG):
		return None

	from gofix.setup.service_billing_setup import REPAIR_SUB_CATEGORY

	if frappe.db.exists("CH Sub Category", REPAIR_SUB_CATEGORY):
		return REPAIR_SUB_CATEGORY

	for name in frappe.get_all(
		"CH Sub Category",
		filters={REPAIR_LABOUR_SUB_CATEGORY_FLAG: 1},
		pluck="name",
		order_by="creation asc",
	):
		if not name.startswith("_Test"):
			return name
	return None


def _ensure_repair_labour_sub_category():
	"""Resolve the repair-labour sub-category, seeding it if this site has none.

	Service Items are provisioned from patches, and patches run inside
	``run_schema_updates`` -- long before the ``after_migrate`` hook that seeds
	this taxonomy. So on any site whose ``after_migrate`` has never completed (a
	fresh site, or one whose previous migrate aborted) the sub-category is simply
	absent, the Item is built with no HSN, and india_compliance kills the whole
	migrate with a MandatoryError. Seeding on demand makes provisioning
	self-sufficient wherever it is called from, instead of depending on a hook
	that runs later.
	"""
	sub_category = _repair_labour_sub_category()
	if sub_category:
		return sub_category

	try:
		from gofix.setup.service_billing_setup import _ensure_repair_taxonomy

		_ensure_repair_taxonomy()
	except Exception:
		# A throw leaves its message queued even when caught, so every later
		# save would replay this popup. Drop it and carry on with the fallback
		# SAC: an incomplete taxonomy is a configuration problem to report, not
		# a reason to abort a migrate.
		frappe.clear_messages()
		frappe.log_error(
			frappe.get_traceback(), "GoFix: could not seed the repair-labour sub-category"
		)
		return None
	return _repair_labour_sub_category()


def _service_hsn_for(sub_category):
	"""HSN/SAC to stamp on a service Item, never blank if we can help it."""
	hsn = None
	if sub_category:
		hsn = frappe.db.get_value("CH Sub Category", sub_category, "hsn_code")
	if not hsn:
		hsn = DEFAULT_SERVICE_HSN
	# gst_hsn_code is a Link; a code with no master row would fail link
	# validation just as loudly as a blank one.
	return hsn if frappe.db.exists("GST HSN Code", hsn) else None


def _service_item_code(solution) -> str:
	"""Stable, readable code: GFR-SCREEN-REPLACEMENT."""
	base = (solution.get("solution_code") or solution.solution_name or solution.name)
	slug = "".join(ch if ch.isalnum() else "-" for ch in cstr(base).upper())
	while "--" in slug:
		slug = slug.replace("--", "-")
	return f"GFR-{slug.strip('-')}"[:140]
