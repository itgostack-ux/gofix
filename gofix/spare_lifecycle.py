# Copyright (c) 2026, GoStack and contributors

"""Closing out a repair spare: the commercial end, and letting go of dead holds.

The STOCK lifecycle of a spare is already complete and correct:

    Reserved -> Issued -> Consumed

and the part physically leaves the building at ``Consumed``, when the
technician fits it and the bound Spare Parts Usage submits a Material Issue.
That is the right moment: once a screen is inside a customer's phone it cannot
go on being reserved, and its cost belongs in the P&L whether or not anyone has
paid the bill yet.

What was missing is the COMMERCIAL end of the same line. Nothing recorded that a
fitted part had been billed, and nothing recorded that the bill had been paid —
so "which parts have we actually been paid for?" had no answer on the ticket.

``Sold`` fills exactly that hole, and nothing more. It is a terminal state
applied to lines that are already ``Consumed``, once the ticket's Sales Invoice
is submitted AND settled in full. It moves no stock — there is none left to move
— and it never overwrites a live stock state, so a part awaiting fitting is
never marked sold just because a deposit cleared.

The second job here is the mirror image: a ticket that dies (cancelled,
rejected, withdrawn, expired) used to keep its reservations forever. Nothing
released them, so the parts stayed invisible to every other ticket — the search
would route around stock that was free in reality. A dead ticket holds nothing.
"""

import frappe
from frappe import _
from frappe.utils import flt

# Stock states a line can still be in. "Sold" only ever follows "Consumed".
CONSUMED = "Consumed"
SOLD = "Sold"

# A ticket in one of these is over; anything it was holding goes back to the pool.
DEAD_DECISIONS = ("Cancelled", "Rejected", "Withdrawn", "Expired")

# Holds that mean "this part is promised to me" and can be given up untouched.
RELEASABLE = ("Reserved", "Pending", "Awaiting Procurement", "In Transit")


def settle_spares_for_invoice(invoice_name: str) -> int:
	"""Mark this invoice's fitted spares Sold once it is submitted and paid.

	Returns how many lines were closed. Quiet and idempotent — it runs from
	several hooks and most of the time there is nothing to do.
	"""
	invoice = frappe.db.get_value(
		"Sales Invoice", invoice_name,
		["name", "docstatus", "outstanding_amount", "status", "is_return"],
		as_dict=True,
	)
	if not invoice or invoice.docstatus != 1 or invoice.is_return:
		return 0

	# Paid in full. Credit notes and part payments leave the line as it was.
	if flt(invoice.outstanding_amount) > 0.005:
		return 0

	service_requests = _service_requests_for_invoice(invoice.name)
	closed = 0
	for sr_name in service_requests:
		closed += _mark_sold(sr_name, invoice.name)
	return closed


def _service_requests_for_invoice(invoice_name: str) -> list:
	"""Tickets billed by this invoice, from either side of the link."""
	names = set()
	for field in ("service_invoice", "custom_service_invoice"):
		if frappe.db.has_column("Service Request", field):
			names.update(frappe.get_all(
				"Service Request", filters={field: invoice_name}, pluck="name"
			))

	if frappe.db.has_column("Sales Invoice", "service_request"):
		linked = frappe.db.get_value("Sales Invoice", invoice_name, "service_request")
		if linked:
			names.add(linked)
	return [n for n in names if n]


def _mark_sold(sr_name: str, invoice_name: str) -> int:
	"""Close every Consumed line on this ticket. Never touches other states."""
	rows = frappe.get_all(
		"SR Spare Line",
		filters={"parent": sr_name, "parenttype": "Service Request", "status": CONSUMED},
		pluck="name",
	)
	if not rows:
		return 0

	for row in rows:
		frappe.db.set_value("SR Spare Line", row, "status", SOLD, update_modified=False)

	frappe.get_doc("Service Request", sr_name).add_comment(
		"Info",
		_("{0} fitted spare(s) marked Sold — invoice {1} is settled in full.").format(
			len(rows), f'<a href="/app/sales-invoice/{invoice_name}">{invoice_name}</a>'
		),
	)
	return len(rows)


# ── hooks ────────────────────────────────────────────────────────────────────

def on_sales_invoice_update(doc, method=None):
	"""Sales Invoice submitted or its outstanding changed."""
	settle_spares_for_invoice(doc.name)


def on_payment_entry(doc, method=None):
	"""A payment landed — settle every repair invoice it cleared."""
	seen = set()
	for ref in (doc.get("references") or []):
		if ref.reference_doctype == "Sales Invoice" and ref.reference_name not in seen:
			seen.add(ref.reference_name)
			settle_spares_for_invoice(ref.reference_name)


def release_holds_on_dead_ticket(doc, method=None):
	"""Give back what a cancelled / rejected / withdrawn ticket was holding.

	Only un-fitted holds are released. A Consumed or Sold line is history and
	stays exactly as it is — the part is gone and its cost is already posted.
	"""
	if doc.get("decision") not in DEAD_DECISIONS:
		return

	rows = [r for r in (doc.get("spare_lines") or []) if r.status in RELEASABLE]
	if not rows:
		return

	for row in rows:
		frappe.db.set_value(
			"SR Spare Line", row.name, "status", "Returned", update_modified=False
		)
		usage = row.get("spare_usage") or frappe.db.get_value(
			"Spare Parts Usage",
			{"service_request_spare_line": row.name, "docstatus": 0},
			"name",
		)
		# Only a DRAFT usage is a mere hold. A submitted one has already moved
		# stock and must be recovered deliberately, not cancelled from here.
		if usage and frappe.db.get_value("Spare Parts Usage", usage, "docstatus") == 0:
			frappe.delete_doc("Spare Parts Usage", usage, force=1, ignore_permissions=True)

	doc.add_comment(
		"Info",
		_("{0} spare hold(s) released back to stock — ticket is {1}.").format(
			len(rows), doc.decision
		),
	)
