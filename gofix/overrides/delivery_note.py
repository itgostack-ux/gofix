# Copyright (c) 2025, GoFix and contributors
# Delivery Note hooks for Service Order functionality

import frappe
from frappe import _


def update_service_request_on_delivery(doc, method=None):
	"""Update Service Request status when Delivery Note is created/submitted
	
	Args:
		doc: Delivery Note document
		method: Hook method (on_submit, on_cancel, etc.)
	"""
	# Check if this delivery is linked to a Service Order
	if not doc.items:
		return
	
	# Get Sales Order from first item
	sales_order = None
	for item in doc.items:
		if item.against_sales_order:
			sales_order = item.against_sales_order
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
			# Delivery Note submitted - mark as Delivered
			sr.db_set("status", "Delivered", update_modified=True)
			sr.db_set("decision", "Delivered", update_modified=False)
			sr.db_set("walkin_status", None, update_modified=False)  # Device no longer with you
			
			frappe.msgprint(
				_("Service Request {0} marked as Delivered").format(so.service_request),
				indicator="green",
				alert=True
			)
		
		elif method == "on_cancel":
			# Delivery cancelled - revert to Invoiced or Completed
			previous_status = "Invoiced" if sr.status == "Delivered" else "Completed"
			sr.db_set("status", previous_status, update_modified=True)
			sr.db_set("decision", previous_status, update_modified=False)
			sr.db_set("walkin_status", "Accepted", update_modified=False)  # Device back with you
	
	except Exception as e:
		frappe.log_error(f"Failed to update SR on delivery: {str(e)}")
