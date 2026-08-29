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

# What a technician can do with a part pulled back out of a device. These are
# SPARE_DISPOSITION_CHOICES on Spare Parts Usage; each one posts its own stock
# movement, which is why the part cannot simply be "un-consumed".
DISPOSITION_HELP = (
	("Good - Back to Stock", "returns it to the shelf it came from"),
	("Faulty - Supplier Return", "sends it to the supplier-return warehouse"),
	("Damaged by Technician", "writes it off to damaged stock"),
)


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


def spares_awaiting_recovery(sr_name: str) -> list:
	"""Parts that are physically inside the device and not yet accounted for.

	A ``Consumed`` spare has left stock through a Material Issue and is fitted.
	Closing the ticket does not undo that: the part is either taken back out and
	dispositioned, or it walks out of the building with the customer and nobody
	knows where it went. These are the rows still waiting for that decision.
	"""
	return frappe.get_all(
		"Spare Parts Usage",
		filters={
			"service_request": sr_name,
			"part_status": CONSUMED,
			"deleted": 0,
			"status": "Active",
		},
		fields=["name", "spare_part_item", "item_name", "qty_used", "uom"],
	)


def assert_spares_recovered(sr_name: str, action: str) -> None:
	"""Refuse to close a ticket while fitted parts are unaccounted for.

	This was previously a msgprint in ``mark_repairability`` — a warning the
	operator could click straight past, on one of the several paths that close a
	ticket, while the others said nothing at all. A part fitted into a device
	that is then handed back is a real stock loss, so it is a gate now.

	The way through is the Spare Recovery panel, not a flag: each part is taken
	out and dispositioned, and each disposition posts the stock movement that
	matches what actually happened to it.
	"""
	pending = spares_awaiting_recovery(sr_name)
	if not pending:
		return

	items = "".join(
		f"<li>{frappe.utils.escape_html(p.item_name or p.spare_part_item)} "
		f"&times; {p.qty_used:g}</li>"
		for p in pending
	)
	options = "".join(f"<li><b>{d}</b> — {why}</li>" for d, why in DISPOSITION_HELP)
	frappe.throw(
		_("Cannot {0}: {1} fitted spare(s) are still inside the device.").format(
			action, len(pending)
		)
		+ f"<ul>{items}</ul>"
		+ _("Remove each part and record what happened to it in the Spare Recovery panel:")
		+ f"<ul>{options}</ul>"
		+ _("If a part is staying in the device, the repair was delivered — "
		    "complete and invoice the ticket instead of closing it."),
		title=_("Spare Recovery Required"),
	)


def release_holds_on_dead_ticket(doc, method=None):
	"""Give back what a cancelled / rejected / withdrawn ticket was holding.

	Only un-fitted holds are released. A fitted part is not a hold — it is
	inside the device, and :func:`assert_spares_recovered` blocks the close
	until someone takes it out and says what became of it. A Consumed or Sold
	line that survives to here is history and stays exactly as it is.
	"""
	if doc.get("decision") not in DEAD_DECISIONS:
		return

	# Backstop for save() paths that did not go through a close API.
	assert_spares_recovered(doc.name, _("close this ticket"))

	# Our courtesy device comes home too.
	from gofix.service_maturity import assert_loaner_returned

	assert_loaner_returned(doc, _("close this ticket"))

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
			# Drop the pointer BEFORE the record it points at. A submitted
			# Service Request may not link to something that no longer exists,
			# so leaving it behind made the very next save of the ticket fail
			# with "Could not find Spare Parts Usage" — which is what blocked
			# closing a ticket as Not Repairable.
			if row.get("spare_usage"):
				frappe.db.set_value(
					"SR Spare Line", row.name, "spare_usage", None, update_modified=False
				)
			frappe.delete_doc("Spare Parts Usage", usage, force=1, ignore_permissions=True)

	doc.add_comment(
		"Info",
		_("{0} spare hold(s) released back to stock — ticket is {1}.").format(
			len(rows), doc.decision
		),
	)
