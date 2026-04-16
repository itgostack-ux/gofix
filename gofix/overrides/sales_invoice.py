# Copyright (c) 2025, GoFix and contributors
# Sales Invoice hooks for Service Order functionality

import frappe
from frappe import _


def resolve_gofix_links(doc, method=None):
	"""Before insert: auto-fill GoFix link fields from Repair Intake or
	item Sales Orders.

	Wrapped in try/except so a failure here never blocks invoice creation.
	"""
	try:
		_resolve_gofix_links_inner(doc)
	except Exception:
		frappe.log_error(
			title=f"GoFix SI resolve_links error: {doc.name}",
			message=frappe.get_traceback(),
		)


def _resolve_gofix_links_inner(doc):
	"""Inner implementation — separated for clean error isolation."""
	# 1) From POS Repair Intake → look up the Service Request
	if doc.get("custom_repair_intake") and not doc.get("custom_gofix_service_request"):
		sr_name = frappe.db.get_value(
			"POS Repair Intake", doc.custom_repair_intake, "service_request"
		)
		if sr_name:
			doc.custom_gofix_service_request = sr_name
			if not doc.get("custom_gofix_service_order"):
				so_name = frappe.db.get_value("Service Request", sr_name, "service_order")
				if so_name:
					doc.custom_gofix_service_order = so_name

	# 2) From item-level Sales Order (Service Order) linkage
	if not doc.get("custom_gofix_service_request"):
		for item in (doc.items or []):
			if item.sales_order:
				is_so, sr = frappe.db.get_value(
					"Sales Order", item.sales_order,
					["is_service_order", "service_request"]
				) or (0, None)
				if is_so and sr:
					doc.custom_gofix_service_request = sr
					doc.custom_gofix_service_order = item.sales_order
					break


def update_service_request_on_invoice(doc, method=None):
	"""Update Service Request status when a *service* Invoice is
	submitted or cancelled.

	This hook runs on ALL Sales Invoices but exits immediately for
	non-service documents.  Wrapped in try/except so a failure here
	never blocks the standard Sales Invoice pipeline.
	"""
	try:
		_update_service_request_on_invoice_inner(doc, method)
	except Exception:
		frappe.log_error(
			title=f"GoFix SI hook error: {doc.name}",
			message=frappe.get_traceback(),
		)


def _update_service_request_on_invoice_inner(doc, method):
	"""Inner implementation — separated for clean error isolation."""
	if not doc.items:
		return

	# Find a Sales Order linked to this invoice
	sales_order = None
	for item in doc.items:
		if item.sales_order:
			sales_order = item.sales_order
			break

	if not sales_order:
		return

	# ── Guard: only act on GoFix Service Orders ──
	so = frappe.get_doc("Sales Order", sales_order)
	if not getattr(so, "is_service_order", False) or not so.service_request:
		return

	# Back-fill GoFix link fields on the invoice (idempotent)
	if not doc.get("custom_gofix_service_request"):
		doc.db_set("custom_gofix_service_request", so.service_request, update_modified=False)
	if not doc.get("custom_gofix_service_order"):
		doc.db_set("custom_gofix_service_order", sales_order, update_modified=False)

	sr = frappe.get_doc("Service Request", so.service_request)

	if method == "on_submit":
		# Invoice submitted - mark as Invoiced
		sr.db_set("status", "Invoiced", update_modified=True)
		sr.db_set("decision", "Invoiced", update_modified=False)

		frappe.msgprint(
			_("Service Request {0} marked as Invoiced").format(so.service_request),
			indicator="green",
			alert=True
		)

	elif method == "on_cancel":
		# Invoice cancelled - revert to Completed
		sr.db_set("status", "Completed", update_modified=True)
		sr.db_set("decision", "Completed", update_modified=False)