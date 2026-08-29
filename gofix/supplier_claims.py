# Copyright (c) 2026, GoFix and contributors

"""Turning a faulty spare into money back from the supplier.

Recovering a spare as "Faulty — Supplier Return" moved the part into the
supplier-return warehouse and stopped there. The stock was tidy and the money
was gone: nobody billed the supplier, so parts that arrived defective were
quietly absorbed as cost.

``CH Supplier Return`` — the group-level RMA document, with credit notes,
debit notes and dispute handling already built — carries a
``source_service_request`` field, so it was always meant to accept a repair as
an origin. Nothing ever created one. This does.

The claim is raised as a DRAFT. Which supplier owes what, and whether to ask for
credit or a replacement, is a purchasing decision; this fills in everything the
purchase team would otherwise retype and leaves the judgement to them.
"""

import frappe
from frappe import _
from frappe.utils import flt, nowdate

RETURN_DOCTYPE = "CH Supplier Return"
FAULTY_DISPOSITION = "Faulty - Supplier Return"

# The spare came out of a device faulty, which is a defect claim rather than
# transit damage or a picking error.
RETURN_TYPE = "Manufacturing Defect"
DEFAULT_RESOLUTION = "Return for Credit"


def raise_claim_for_recovered_spare(usage, remarks: str | None = None) -> str | None:
	"""Open a supplier claim for one spare recovered as faulty.

	Returns the claim name, or None when there is nothing to claim against —
	no receipt can be traced, or the doctype is not installed. Never raises:
	a recovery that has already moved stock must not be undone because the
	paperwork behind it could not be created.
	"""
	if not frappe.db.exists("DocType", RETURN_DOCTYPE):
		return None

	try:
		supplier, source = _trace_supplier(usage.spare_part_item, usage.warehouse)
		# A Post-Receipt Return needs the Purchase Receipt specifically, and that
		# is the right requirement: you cannot claim a defect against a supplier
		# without the record of receiving the part from them. A spare that was
		# fitted into a device was necessarily received, so in a system where
		# procurement actually runs, this is always present.
		if not (supplier and source.get("purchase_receipt")):
			_note_untraceable(usage, supplier)
			return None

		claim = frappe.new_doc(RETURN_DOCTYPE)
		claim.return_date = nowdate()
		claim.company = _company_for(usage)
		claim.return_scenario = "Post-Receipt Return"
		claim.supplier = supplier
		claim.return_type = RETURN_TYPE
		claim.resolution_action = DEFAULT_RESOLUTION
		if claim.meta.get_field("source_service_request"):
			claim.source_service_request = usage.service_request
		if source.get("purchase_receipt") and claim.meta.get_field("purchase_receipt"):
			claim.purchase_receipt = source["purchase_receipt"]
		if source.get("purchase_order") and claim.meta.get_field("purchase_order"):
			claim.purchase_order = source["purchase_order"]
		if claim.meta.get_field("return_notes"):
			claim.return_notes = _(
				"Spare failed in service and was recovered from repair {0}. {1}"
			).format(usage.service_request, remarks or "")

		claim.append("items", {
			"item_code": usage.spare_part_item,
			"item_name": usage.item_name or usage.spare_part_item,
			"uom": usage.uom,
			"qty_to_return": flt(usage.qty_used),
			"rate": flt(usage.purchase_cost),
			"amount": flt(usage.purchase_cost) * flt(usage.qty_used),
			"damage_reason": RETURN_TYPE,
			"damage_description": remarks
			or _("Fitted during repair {0} and found faulty.").format(usage.service_request),
			"inspection_result": "Rejected",
			"serial_nos": usage.barcode_value or "",
		})

		claim.flags.ignore_permissions = True
		claim.insert(ignore_permissions=True)

		if usage.meta.get_field("supplier_return_claim"):
			usage.db_set("supplier_return_claim", claim.name, update_modified=False)

		_link_back(usage.service_request, claim.name, usage.spare_part_item)
		return claim.name

	except Exception:
		frappe.log_error(
			frappe.get_traceback(),
			f"GoFix: could not raise supplier claim for {usage.name}",
		)
		return None


def _company_for(usage) -> str:
	return (
		frappe.db.get_value("Service Request", usage.service_request, "company")
		or frappe.db.get_value("Warehouse", usage.warehouse, "company")
		or frappe.defaults.get_user_default("Company")
	)


def _trace_supplier(item_code: str, warehouse: str | None) -> tuple:
	"""Who sold us this part.

	Most recent receipt first, because a spare bought repeatedly should be
	claimed against the batch it actually came from rather than the first
	supplier ever used.
	"""
	rows = frappe.db.sql(
		"""
		SELECT pr.supplier, pr.name AS purchase_receipt, pri.purchase_order
		FROM `tabPurchase Receipt Item` pri
		JOIN `tabPurchase Receipt` pr ON pr.name = pri.parent
		WHERE pri.item_code = %(item)s AND pr.docstatus = 1
		ORDER BY pr.posting_date DESC, pr.creation DESC
		LIMIT 1
		""",
		{"item": item_code},
		as_dict=True,
	)
	if rows:
		return rows[0].supplier, {
			"purchase_receipt": rows[0].purchase_receipt,
			"purchase_order": rows[0].purchase_order,
		}

	# No receipt means no claimable evidence. The item's default supplier is
	# still worth returning so the message on the ticket can name who to chase
	# manually, but it is not enough to open an RMA against.
	default = frappe.db.get_value("Item Default", {"parent": item_code}, "default_supplier")
	return default, {}


def _note_untraceable(usage, supplier: str | None = None) -> None:
	"""Say so on the ticket rather than losing the claim silently."""
	reason = (
		_("its supplier is known ({0}) but no Purchase Receipt could be traced, and a "
		  "defect claim needs the receipt as evidence").format(supplier)
		if supplier
		else _("no supplier could be traced from its purchase history")
	)
	frappe.log_error(
		f"No claimable purchase record for {usage.spare_part_item} "
		f"(recovered faulty from {usage.service_request}); no claim raised.",
		"GoFix: untraceable faulty spare",
	)
	if usage.service_request:
		frappe.get_doc("Service Request", usage.service_request).add_comment(
			"Info",
			_("Spare {0} was recovered as faulty, but {1} — no supplier claim was raised. "
			  "Open one manually if the part is under supplier warranty.").format(
				usage.spare_part_item, reason
			),
		)


def _link_back(sr_name: str, claim_name: str, item_code: str) -> None:
	if not sr_name:
		return
	frappe.get_doc("Service Request", sr_name).add_comment(
		"Info",
		_("Supplier claim {0} opened for faulty spare {1}.").format(
			f'<a href="/app/ch-supplier-return/{claim_name}">{claim_name}</a>', item_code
		),
	)
