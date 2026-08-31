# Copyright (c) 2025, GoFix and contributors
# Delivery Note hooks for Service Order functionality

import frappe
from frappe import _


def update_service_request_on_delivery(doc, method=None):
	"""Update Service Request status when a *service* Delivery Note is
	submitted or cancelled.

	This hook runs on ALL Delivery Notes but exits immediately for
	non-service documents.  Wrapped in try/except so a failure here
	never blocks the standard Delivery Note pipeline.
	"""
	try:
		_update_service_request_on_delivery_inner(doc, method)
	except Exception:
		frappe.log_error(
			title=f"GoFix DN hook error: {doc.name}",
			message=frappe.get_traceback(),
		)


def _update_service_request_on_delivery_inner(doc, method):
	"""Inner implementation — separated for clean error isolation."""
	if not doc.items:
		return

	# Find a Sales Order linked to this DN
	sales_order = None
	for item in doc.items:
		if item.against_sales_order:
			sales_order = item.against_sales_order
			break

	if not sales_order:
		return

	# ── Guard: only act on GoFix Service Orders ──
	so = frappe.get_doc("Sales Order", sales_order)
	if not getattr(so, "is_service_order", False) or not so.service_request:
		return

	sr = frappe.get_doc("Service Request", so.service_request)

	if method == "on_submit":
		# ── Gate: spare recovery check for Not Repairable / BER ──
		repair_outcome = getattr(so, "repair_outcome", None) or ""
		if repair_outcome in ("Not Repairable", "Beyond Repair", "Customer Cancelled"):
			# One source of truth for "still inside the device and unaccounted
			# for" — it subtracts parts the customer has already been invoiced
			# and paid for, which are legitimately theirs to take home.
			from gofix.spare_lifecycle import spares_awaiting_recovery

			pending = spares_awaiting_recovery(so.service_request)
			if pending:
				items_str = ", ".join(
					f"{p.item_name or p.spare_part_item} (x{p.qty_used})" for p in pending
				)
				frappe.throw(
					_("Cannot deliver: {0} consumed spare(s) have not been recovered.<br>"
					  "Each spare must be dispositioned (Good → Stock / Faulty → Supplier Return / "
					  "Damaged → Damaged Stock) before returning the device.<br><br>"
					  "Pending: {1}").format(len(pending), items_str),
					title=_("Spare Recovery Required"),
				)

		# Delivery Note submitted - mark as Delivered
		sr.db_set("decision", "Delivered", update_modified=True)
		sr.db_set("walkin_status", None, update_modified=False)  # Device no longer with you

		frappe.msgprint(
			_("Service Request {0} marked as Delivered").format(so.service_request),
			indicator="green",
			alert=True
		)

	elif method == "on_cancel":
		# Delivery cancelled - revert to Invoiced or Completed
		previous_status = "Invoiced" if sr.decision == "Delivered" else "Completed"
		sr.db_set("decision", previous_status, update_modified=True)
		sr.db_set("walkin_status", "Accepted", update_modified=False)  # Device back with you
