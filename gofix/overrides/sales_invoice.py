# Copyright (c) 2025, GoFix and contributors
# Sales Invoice hooks for Service Order functionality

import frappe
from frappe import _


def update_service_request_on_invoice(doc, method=None):
	"""Update Service Request status when Invoice is created/submitted
	
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