"""Who took the device in.

The ticket recorded the customer, the device and the fault, and said nothing
about which member of counter staff stood there and accepted it. ``owner`` is
the login that saved the record -- often a shared till account, and never the
name a customer would recognise if they came back to ask "who did I hand it
to?".

The POS already tracks that person for a sale: the executive shown as "Billed
By" in the till. The intake screen keeps that control and drops the rest of the
cart, so the same name that would be on an invoice is on the device receipt.

It is a Link to POS Executive rather than free text so it stays one person from
one company's roster, and so reporting can count intakes by whoever took them.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def create_service_request_intake_executive_field():
	create_custom_fields(
		{
			"Service Request": [
				{
					"fieldname": "intake_executive",
					"fieldtype": "Link",
					"options": "POS Executive",
					"label": "Taken In By",
					"insert_after": "accepted_by",
					"read_only": 1,
					"in_standard_filter": 1,
					"description": (
						"Counter staff who accepted the device, as chosen in the "
						"till's Billed By. Set at intake and not edited after."
					),
				},
			]
		},
		update=True,
	)
	frappe.clear_cache(doctype="Service Request")
