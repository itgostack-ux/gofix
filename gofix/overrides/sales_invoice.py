# Copyright (c) 2025, GoFix and contributors
# Sales Invoice hooks for Service Order functionality

import frappe
from frappe import _


def resolve_gofix_links(doc, method=None):
	"""Before insert: auto-fill GoFix link fields from Repair Intake or item Sales Orders."""
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
	"""Update Service Request status when Invoice is created/submitted.
	Also back-fills GoFix link fields on the Sales Invoice.

	Args:
		doc: Sales Invoice document
		method: Hook method (on_submit, on_cancel, etc.)
	"""
	# Check if this invoice is linked to a Service Order
	if not doc.items:
		return
	
	# Get Sales Order from first item
	sales_order = None
	for item in doc.items:
		if item.sales_order:
			sales_order = item.sales_order
			break
	
	if not sales_order:
		return
	
	# Check if it's a Service Order
	so = frappe.get_doc("Sales Order", sales_order)
	if not so.is_service_order or not so.service_request:
		return

	# Back-fill GoFix link fields on the invoice (idempotent)
	if not doc.get("custom_gofix_service_request"):
		doc.db_set("custom_gofix_service_request", so.service_request, update_modified=False)
	if not doc.get("custom_gofix_service_order"):
		doc.db_set("custom_gofix_service_order", sales_order, update_modified=False)
	
	try:
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
	
	except Exception as e:
		frappe.log_error(f"Failed to update SR on invoice: {str(e)}")