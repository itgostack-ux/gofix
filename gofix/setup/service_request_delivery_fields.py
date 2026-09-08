"""Move the QC and handover gates onto the Service Request.

Why
---
The repair was described by two operational documents. The Service Request held
the device, the diagnosis, the parts and the custody; the Sales Order held the
quality verdict and the whole handover -- OTP, signature, delivered timestamp --
plus 15 fields duplicated from the request that were free to drift, and did.

One operational document is the industry norm for repair (Odoo's repair order,
Salesforce and Dynamics work orders): the ticket carries the job and the invoice
is raised from it. These fields are the half that was living on the wrong
document, so the request can stand on its own.

The Sales Order keeps its copies. 198 of them exist and several reports read
them, so nothing is removed -- new repairs simply stop needing one.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


SERVICE_REQUEST_DELIVERY_FIELDS = {
	"Service Request": [
		{
			"fieldname": "qc_handover_section",
			"fieldtype": "Section Break",
			"label": "Quality Check & Handover",
			"insert_after": "service_invoices",
			"collapsible": 1,
		},
		{
			"fieldname": "qc_status",
			"fieldtype": "Select",
			"label": "QC Status",
			"options": "\nPending\nAwaiting\nIn Progress\nPass\nFail",
			"insert_after": "qc_handover_section",
			"in_standard_filter": 1,
			"description": "Must read Pass before the device can be handed over.",
		},
		{"fieldname": "qc_checked_by", "fieldtype": "Link", "options": "User",
		 "label": "QC Checked By", "insert_after": "qc_status", "read_only": 1},
		{"fieldname": "qc_datetime", "fieldtype": "Datetime", "label": "QC Date & Time",
		 "insert_after": "qc_checked_by", "read_only": 1},
		{"fieldname": "qc_remarks", "fieldtype": "Text", "label": "QC Remarks",
		 "insert_after": "qc_datetime"},

		{"fieldname": "handover_column_break", "fieldtype": "Column Break",
		 "insert_after": "qc_remarks"},

		{
			"fieldname": "delivery_otp",
			"fieldtype": "Data",
			"label": "Delivery OTP",
			"insert_after": "handover_column_break",
			"read_only": 1,
			"hidden": 1,
			"description": "Stored encrypted. Never shown; verified against what the customer reads out.",
		},
		{"fieldname": "delivery_otp_verified", "fieldtype": "Check", "default": "0",
		 "label": "OTP Verified", "insert_after": "delivery_otp", "read_only": 1},
		{"fieldname": "delivery_otp_sent_at", "fieldtype": "Datetime", "label": "OTP Sent At",
		 "insert_after": "delivery_otp_verified", "read_only": 1},
		{"fieldname": "delivery_otp_attempts", "fieldtype": "Int", "default": "0",
		 "label": "OTP Attempts", "insert_after": "delivery_otp_sent_at", "read_only": 1},
		{"fieldname": "delivery_otp_locked_until", "fieldtype": "Datetime",
		 "label": "OTP Locked Until", "insert_after": "delivery_otp_attempts", "read_only": 1},
		{"fieldname": "delivery_otp_consumed_at", "fieldtype": "Datetime",
		 "label": "OTP Consumed At", "insert_after": "delivery_otp_locked_until", "read_only": 1},
		{
			"fieldname": "accessories_returned",
			"fieldtype": "Check",
			"default": "0",
			"label": "Accessories Returned",
			"insert_after": "delivery_otp_consumed_at",
			"description": "Only gates handover when accessories were received at intake.",
		},
		{"fieldname": "customer_signature", "fieldtype": "Signature",
		 "label": "Customer Signature", "insert_after": "accessories_returned"},
		{"fieldname": "delivered_datetime", "fieldtype": "Datetime",
		 "label": "Delivered Date & Time", "insert_after": "customer_signature", "read_only": 1},
		{"fieldname": "delivery_remarks", "fieldtype": "Small Text",
		 "label": "Handover Remarks", "insert_after": "delivered_datetime"},

		# The checklist itself. It was populated on the Sales Order from the
		# request's own solution lines, so the rows described work recorded on
		# one document and lived on another.
		# Where this ticket came from. The visit already points forward to the
		# ticket; without the reverse link the ticket cannot answer "how did
		# this customer reach us", which is the first thing anyone asks of it.
		{
			"fieldname": "front_desk_visit",
			"fieldtype": "Link",
			"options": "POS Kiosk Token",
			"label": "Front Desk Visit",
			"insert_after": "walkin_source",
			"read_only": 1,
			"description": "The walk-in or written request this repair came from.",
		},
		{
			"fieldname": "qc_checklist_section",
			"fieldtype": "Section Break",
			"label": "QC Checklist",
			"insert_after": "delivery_remarks",
			"collapsible": 1,
		},
		{
			"fieldname": "qc_checklist",
			"fieldtype": "Table",
			"label": "QC Checklist",
			"options": "GoFix QC Checklist",
			"insert_after": "qc_checklist_section",
		},
		{
			"fieldname": "rework_count",
			"fieldtype": "Int",
			"label": "Rework Count",
			"insert_after": "qc_checklist",
			"read_only": 1,
			"default": "0",
			"description": "How many times this repair has come back from a QC fail.",
		},
	]
}


def create_service_request_delivery_fields():
	# The ticket is submittable and every one of these is written *after*
	# submit -- QC is recorded during the repair, the OTP and the signature at
	# handover. Without allow_on_submit Frappe refuses the write with
	# "Not allowed to change ... after submission", which is exactly how the
	# first run of this failed.
	fields = {
		"Service Request": [
			{**f, "allow_on_submit": 1} if f.get("fieldtype") not in ("Section Break", "Column Break") else f
			for f in SERVICE_REQUEST_DELIVERY_FIELDS["Service Request"]
		]
	}
	create_custom_fields(fields, update=True)
	frappe.clear_cache(doctype="Service Request")


def backfill_from_service_orders():
	"""Copy the gate state off existing Service Orders, once.

	198 repairs already carry their QC verdict and handover state on the order.
	Without this they would look un-QC'd the moment the request became the
	source of truth. Only writes where the request is still blank, so a value
	set after the migration is never overwritten.
	"""
	if not frappe.db.has_column("Service Request", "qc_status"):
		return 0

	pairs = frappe.db.sql("""
		SELECT so.name AS so, so.service_request AS sr,
		       so.qc_status, so.qc_checked_by, so.qc_datetime, so.qc_remarks,
		       so.delivery_otp_verified, so.accessories_returned,
		       so.customer_signature, so.delivered_datetime
		FROM `tabSales Order` so
		WHERE so.is_service_order = 1 AND COALESCE(so.service_request,'') != ''
	""", as_dict=True)

	moved = 0
	for row in pairs:
		updates = {}
		for field in ("qc_status", "qc_checked_by", "qc_datetime", "qc_remarks",
		              "delivery_otp_verified", "accessories_returned",
		              "customer_signature", "delivered_datetime"):
			value = row.get(field)
			if value in (None, "", 0):
				continue
			if frappe.db.get_value("Service Request", row.sr, field):
				continue          # already set on the request; leave it alone
			updates[field] = value
		if updates:
			frappe.db.set_value("Service Request", row.sr, updates, update_modified=False)
			moved += 1
	return moved
