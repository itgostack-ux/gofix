"""How the device gets here, and how it goes back.

Why
---
The request already knew a little about this and used almost none of it. There
was a ``mode_of_service`` Select -- Walk-in, Pickup, Courier, On-site -- written
into the doctype, plus a pickup agent, a return courier and a tracking number
that no repair had ever filled in. Twenty-seven tickets said Walk-in and
thirty-nine said nothing.

Two things were missing, and they are the ones the counter actually needs.

The first is the return. Everything recorded was about the device arriving;
nothing said how it goes back, so nobody could tell whether the customer was
coming to collect, whether we were couriering it, or whether it was sitting on
a shelf waiting for a call that was never planned. That is the question a
repair shop is asked all day.

The second is that the mode was a hardcoded list. Same-day riders -- Dunzo,
Porter, Swiggy -- had no home in it, and the Courier Partner master that could
have held them shipped with zero rows. So a repair that went out by rider was
recorded as "Courier" with nothing about who carried it or what to quote the
customer when they rang.

Both directions now point at ``Device Logistics Method``, whose flags say what
each mode involves, and at ``Courier Partner`` for who carried it.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


SERVICE_REQUEST_LOGISTICS_FIELDS = {
	"Service Request": [
		{
			"fieldname": "logistics_section",
			"fieldtype": "Section Break",
			"label": "Device Movement",
			"insert_after": "front_desk_visit",
			"collapsible": 1,
			"description": "How the device reached us, and how it goes back.",
		},
		{
			"fieldname": "intake_method",
			"fieldtype": "Link",
			"options": "Device Logistics Method",
			"label": "Arrived By",
			"insert_after": "logistics_section",
			"in_standard_filter": 1,
			"description": "How the device got to us.",
		},
		{"fieldname": "intake_partner", "fieldtype": "Link", "options": "Courier Partner",
		 "label": "Collected By", "insert_after": "intake_method",
		 "depends_on": "eval:doc.intake_method"},
		{"fieldname": "intake_tracking_number", "fieldtype": "Data",
		 "label": "Inbound Tracking / Task No", "insert_after": "intake_partner",
		 "depends_on": "eval:doc.intake_partner"},
		{"fieldname": "intake_received_datetime", "fieldtype": "Datetime",
		 "label": "Device Received At", "insert_after": "intake_tracking_number",
		 "read_only": 1},

		{"fieldname": "logistics_column", "fieldtype": "Column Break",
		 "insert_after": "intake_received_datetime"},

		{
			"fieldname": "return_method",
			"fieldtype": "Link",
			"options": "Device Logistics Method",
			"label": "Goes Back By",
			"insert_after": "logistics_column",
			"in_standard_filter": 1,
			"description": "Agreed with the customer before they are billed, so "
			               "nobody has to ring round asking who is collecting what.",
		},
		{"fieldname": "return_partner", "fieldtype": "Link", "options": "Courier Partner",
		 "label": "Returned By", "insert_after": "return_method",
		 "depends_on": "eval:doc.return_method"},
		{"fieldname": "return_address", "fieldtype": "Small Text",
		 "label": "Return Address", "insert_after": "return_partner",
		 "depends_on": "eval:doc.return_method"},
		{"fieldname": "return_scheduled_datetime", "fieldtype": "Datetime",
		 "label": "Return Slot", "insert_after": "return_address",
		 "depends_on": "eval:doc.return_method"},
		{"fieldname": "return_confirmed_by", "fieldtype": "Link", "options": "User",
		 "label": "Return Agreed By", "insert_after": "return_scheduled_datetime",
		 "read_only": 1},
		{"fieldname": "return_confirmed_at", "fieldtype": "Datetime",
		 "label": "Return Agreed At", "insert_after": "return_confirmed_by",
		 "read_only": 1},
	]
}


def create_service_request_logistics_fields():
	# Every one of these is written after the ticket is submitted: the return
	# is agreed during the repair, and the dispatch happens at the very end.
	fields = {
		"Service Request": [
			{**f, "allow_on_submit": 1}
			if f.get("fieldtype") not in ("Section Break", "Column Break") else f
			for f in SERVICE_REQUEST_LOGISTICS_FIELDS["Service Request"]
		]
	}
	create_custom_fields(fields, update=True)
	frappe.clear_cache(doctype="Service Request")


# The old Select said Walk-in / Pickup / Courier / On-site. Only the first was
# ever used in anger, but the mapping keeps those twenty-seven tickets honest.
_LEGACY_MODE_TO_METHOD = {
	"Walk-in": "Customer In Person",
	"Pickup": "Our Rider",
	"Courier": "Courier",
	"On-site": "On-site Visit",
}


def backfill_intake_method() -> int:
	"""Carry the old mode_of_service across, once, where it said something."""
	if not frappe.db.has_column("Service Request", "intake_method"):
		return 0
	moved = 0
	for legacy, method in _LEGACY_MODE_TO_METHOD.items():
		if not frappe.db.exists("Device Logistics Method", method):
			continue
		rows = frappe.get_all("Service Request",
			filters={"mode_of_service": legacy, "intake_method": ("in", ["", None])},
			pluck="name", limit_page_length=0)
		for name in rows:
			frappe.db.set_value("Service Request", name, "intake_method", method,
			                    update_modified=False)
			moved += 1
	return moved
