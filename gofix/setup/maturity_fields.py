# Copyright (c) 2026, GoFix and contributors

"""The fields behind the things a repair operation gets measured on.

None of these change how a repair is done. They are what turns a working
service desk into one that can be managed: a promise the customer can hold you
to, an answer when they are asked how it went, a certificate when an inspector
asks where the scrap went, and a technician roster that knows when an
authorisation lapses.

Each is deliberately small. The capability they unlock mostly already existed —
custody tracking, WhatsApp delivery, the damaged-stock warehouse, technician
grades — and was missing only the field that made it usable.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

LOANER_STATUSES = "Not Issued\nIssued\nReturned\nNot Returned\nWritten Off"
DISPOSAL_STATUSES = "In Damaged Stock\nAwaiting Collection\nHanded to Recycler\nWritten Off"


def create_maturity_fields():
	_service_request_fields()
	_employee_certification_fields()
	_disposal_fields()
	_settings_fields()


def _service_request_fields():
	if not frappe.db.exists("DocType", "Service Request"):
		return

	create_custom_fields({
		"Service Request": [
			# ── Appointment ────────────────────────────────────────────
			{
				"fieldname": "appointment_section",
				"label": "Appointment",
				"fieldtype": "Section Break",
				"insert_after": "service_date",
				"collapsible": 1,
			},
			{
				"fieldname": "appointment_datetime",
				"label": "Booked Slot",
				"fieldtype": "Datetime",
				"insert_after": "appointment_section",
				"allow_on_submit": 1,
				"description": "Booked against the store's bench capacity for that day.",
			},
			{
				"fieldname": "appointment_source",
				"label": "Booked Via",
				"fieldtype": "Select",
				"options": "\nWalk-in\nPhone\nWebsite\nWhatsApp",
				"insert_after": "appointment_datetime",
				"allow_on_submit": 1,
			},
			{
				"fieldname": "column_break_appointment",
				"fieldtype": "Column Break",
				"insert_after": "appointment_source",
			},
			# promised_completion_datetime is now a first-class DocField on
			# Service Request (the ETA-revision work promoted it); installing a
			# Custom Field of the same name would duplicate it in meta.

			# ── Loaner device ──────────────────────────────────────────
			{
				"fieldname": "loaner_section",
				"label": "Loaner Device",
				"fieldtype": "Section Break",
				"insert_after": "accessories_received",
				"collapsible": 1,
			},
			{
				"fieldname": "loaner_status",
				"label": "Loaner Status",
				"fieldtype": "Select",
				"options": LOANER_STATUSES,
				"default": "Not Issued",
				"insert_after": "loaner_section",
				"allow_on_submit": 1,
			},
			{
				"fieldname": "loaner_serial_no",
				"label": "Loaner Serial / IMEI",
				"fieldtype": "Data",
				"insert_after": "loaner_status",
				"allow_on_submit": 1,
				"depends_on": "eval:doc.loaner_status && doc.loaner_status != 'Not Issued'",
			},
			{
				"fieldname": "column_break_loaner",
				"fieldtype": "Column Break",
				"insert_after": "loaner_serial_no",
			},
			{
				"fieldname": "loaner_issued_at",
				"label": "Issued At",
				"fieldtype": "Datetime",
				"insert_after": "column_break_loaner",
				"read_only": 1,
				"allow_on_submit": 1,
			},
			{
				"fieldname": "loaner_returned_at",
				"label": "Returned At",
				"fieldtype": "Datetime",
				"insert_after": "loaner_issued_at",
				"read_only": 1,
				"allow_on_submit": 1,
			},

			# ── Customer feedback ──────────────────────────────────────
			{
				"fieldname": "feedback_section",
				"label": "Customer Feedback",
				"fieldtype": "Section Break",
				"insert_after": "customer_remarks",
				"collapsible": 1,
			},
			{
				"fieldname": "csat_score",
				"label": "Satisfaction (1–5)",
				"fieldtype": "Rating",
				"insert_after": "feedback_section",
				"allow_on_submit": 1,
			},
			{
				"fieldname": "nps_score",
				"label": "Recommend Score (0–10)",
				"fieldtype": "Int",
				"insert_after": "csat_score",
				"allow_on_submit": 1,
				"description": "0–10. Promoters are 9–10, detractors 0–6.",
			},
			{
				"fieldname": "column_break_feedback",
				"fieldtype": "Column Break",
				"insert_after": "nps_score",
			},
			{
				"fieldname": "feedback_comment",
				"label": "What the Customer Said",
				"fieldtype": "Small Text",
				"insert_after": "column_break_feedback",
				"allow_on_submit": 1,
			},
			{
				"fieldname": "feedback_received_at",
				"label": "Feedback Received",
				"fieldtype": "Datetime",
				"insert_after": "feedback_comment",
				"read_only": 1,
				"allow_on_submit": 1,
			},
		]
	}, ignore_validate=True)


def _employee_certification_fields():
	"""Brand authorisations lapse; the roster should know when."""
	if not frappe.db.exists("DocType", "Employee"):
		return

	create_custom_fields({
		"Employee": [
			{
				"fieldname": "gofix_certification_expiry",
				"label": "Repair Certification Valid Until",
				"fieldtype": "Date",
				"insert_after": "gofix_service_warehouse",
				"description": (
					"Brand or safety authorisation expiry. Past this date the technician is "
					"skipped by skill-based routing."
				),
			},
			{
				"fieldname": "gofix_certification_body",
				"label": "Certifying Body",
				"fieldtype": "Data",
				"insert_after": "gofix_certification_expiry",
			},
			{
				"fieldname": "gofix_daily_bench_hours",
				"label": "Bench Hours per Day",
				"fieldtype": "Float",
				"default": "7.5",
				"insert_after": "gofix_certification_body",
				"description": "Hours actually available at the bench, used for capacity planning.",
			},
		]
	}, ignore_validate=True)


def _disposal_fields():
	"""Where scrap actually went, on the Stock Entry that moved it.

	Damaged stock accumulated forever with nothing to show an inspector. Under
	the E-Waste Rules a producer has to be able to name the authorised recycler
	and produce the handover certificate, and the natural place to record that
	is the stock movement that took the parts out of the building.
	"""
	if not frappe.db.exists("DocType", "Stock Entry"):
		return

	create_custom_fields({
		"Stock Entry": [
			{
				"fieldname": "gofix_disposal_section",
				"label": "E-Waste Disposal",
				"fieldtype": "Section Break",
				"insert_after": "remarks",
				"collapsible": 1,
				"depends_on": "eval:doc.gofix_disposal_recycler || doc.stock_entry_type == 'Material Issue'",
			},
			{
				"fieldname": "gofix_disposal_recycler",
				"label": "Authorised Recycler",
				"fieldtype": "Data",
				"insert_after": "gofix_disposal_section",
				"description": "Name of the CPCB/SPCB-authorised recycler the scrap was handed to.",
			},
			{
				"fieldname": "gofix_disposal_authorisation_no",
				"label": "Recycler Authorisation No.",
				"fieldtype": "Data",
				"insert_after": "gofix_disposal_recycler",
			},
			{
				"fieldname": "column_break_gofix_disposal",
				"fieldtype": "Column Break",
				"insert_after": "gofix_disposal_authorisation_no",
			},
			{
				"fieldname": "gofix_disposal_date",
				"label": "Handover Date",
				"fieldtype": "Date",
				"insert_after": "column_break_gofix_disposal",
			},
			{
				"fieldname": "gofix_disposal_certificate",
				"label": "Disposal Certificate",
				"fieldtype": "Attach",
				"insert_after": "gofix_disposal_date",
				"description": "The certificate the recycler issues on collection.",
			},
		]
	}, ignore_validate=True)


def _settings_fields():
	if not frappe.db.exists("DocType", "GoFix Settings"):
		return

	create_custom_fields({
		"GoFix Settings": [
			{
				"fieldname": "ber_cost_ratio",
				"label": "Beyond-Economic-Repair Ratio",
				"fieldtype": "Float",
				"default": "0.7",
				"insert_after": "not_repairable_insight_threshold",
				"description": (
					"Warn when a quote reaches this share of the device's market value. "
					"0.7 means a repair quoted at 70% of what the device is worth."
				),
			},
			{
				"fieldname": "capacity_warning_ratio",
				"label": "Bench Capacity Warning Ratio",
				"fieldtype": "Float",
				"default": "0.9",
				"insert_after": "ber_cost_ratio",
				"description": "Warn when a day's booked work reaches this share of available bench hours.",
			},
		]
	}, ignore_validate=True)

	frappe.logger("gofix").info("GoFix: maturity fields installed")
