# Copyright (c) 2026, GoFix and contributors

"""Ending a repair without repairing anything.

Most jobs end with a working device. The ones that do not are the jobs an
operation actually needs to understand, and they were the ones being recorded
worst: a free-text sentence on the ticket, written by whoever happened to close
it, in no vocabulary anything could count.

Four ways a job ends without a fix, which every service desk distinguishes
because they lead to different conversations and different numbers:

    Not Repairable      the device cannot be fixed — no part, no method
    BER                 it can be fixed, but not for what the device is worth
    Customer Declined   we quoted, the customer said no
    Customer Cancelled  the customer pulled out, estimate or no estimate

The first two are ours to answer for; the second two are the customer's
decision. Reading them as one number — "jobs we did not fix" — hides whether
the problem is capability or price.

Every close carries a CODED reason from Withdrawal Reason, not prose, because
"why do repairs fail here" is a question somebody will ask of a year's data.
The note stays for the detail a code cannot hold, and is demanded outright for
reasons that mean nothing without it.

The same function serves the counter and the workshop. A device is refused at
the counter as often as at the bench, and two implementations of one decision
drift.
"""

import frappe
from frappe import _
from frappe.utils import now_datetime

from gofix.security import assert_service_request_access

#: Outcome → the decision the ticket lands on. Rejected is ours, Cancelled is
#: theirs; the ticket's own status has to say which, because reporting reads it.
CLOSE_OUTCOMES = {
	"Not Repairable": {"decision": "Rejected", "repairability": "Not Repairable"},
	"BER": {"decision": "Rejected", "repairability": "BER"},
	"Customer Declined": {"decision": "Cancelled", "repairability": "Customer Declined"},
	"Customer Cancelled": {"decision": "Cancelled", "repairability": "Customer Declined"},
}

#: Decisions that mean the job is already over.
CLOSED_DECISIONS = ("Delivered", "Withdrawn", "Cancelled", "Rejected")


@frappe.whitelist()
def get_close_reasons(outcome: str | None = None) -> list[dict]:
	"""The reasons a ticket may be closed with, for one outcome.

	Filtered rather than dumped: offering "Parts Not Available" as a reason for
	a customer cancelling their own job is how coded reasons end up as noise.
	"""
	filters = {"is_active": 1}
	rows = frappe.get_all(
		"Withdrawal Reason", filters=filters,
		fields=["name", "reason_name", "reason_type", "applies_to", "requires_note"],
		order_by="reason_type, reason_name", limit_page_length=200,
	)
	if outcome:
		rows = [r for r in rows if (r.applies_to or "Any") in ("Any", outcome)]
	return rows


@frappe.whitelist(methods=["POST"])
def close_without_repair(service_request, outcome, reason, note=None) -> dict:
	"""Close a ticket that will not end in a working device.

	Refuses rather than warns on the two things that cost money later: fitted
	parts still inside the device, and a reason nobody will be able to
	interpret. Both are cheap to supply now and impossible to reconstruct after
	the customer has gone.
	"""
	if outcome not in CLOSE_OUTCOMES:
		frappe.throw(
			_("Unknown outcome {0}. Expected one of: {1}").format(
				outcome, ", ".join(CLOSE_OUTCOMES)),
			title=_("Invalid Outcome"),
		)

	sr = assert_service_request_access(service_request, permission_type="write")
	if sr.decision in CLOSED_DECISIONS:
		frappe.throw(
			_("{0} is already {1}.").format(sr.name, sr.decision), title=_("Already Closed")
		)

	reason_row = frappe.db.get_value(
		"Withdrawal Reason", reason,
		["name", "reason_name", "applies_to", "requires_note", "is_active"], as_dict=True,
	)
	if not reason_row or not reason_row.is_active:
		frappe.throw(_("Pick a reason from the list."), title=_("Reason Required"))
	if (reason_row.applies_to or "Any") not in ("Any", outcome):
		frappe.throw(
			_("{0} is not a reason for {1}.").format(reason_row.reason_name or reason, outcome),
			title=_("Reason Does Not Fit"),
		)
	note = (note or "").strip()
	if reason_row.requires_note and not note:
		frappe.throw(
			_("\"{0}\" needs a note saying what actually happened — on its own it "
			  "tells the next person nothing.").format(reason_row.reason_name or reason),
			title=_("Note Required"),
		)

	# Our stock does not go home inside a customer's device. This throws when a
	# fitted part has not been recovered, and it is the one gate here that has
	# to hold before anything else is written.
	from gofix.spare_lifecycle import assert_spares_recovered

	assert_spares_recovered(sr.name, _("close this ticket as {0}").format(outcome))

	mapping = CLOSE_OUTCOMES[outcome]
	summary = reason_row.reason_name or reason_row.name
	if note:
		summary = f"{summary} — {note}"

	sr.flags.ignore_validate_update_after_submit = True
	updates = {
		"decision": mapping["decision"],
		"repairability_status": mapping["repairability"],
		"repairability_reason": summary,
		"repairability_decided_by": frappe.session.user,
		"repairability_decided_at": now_datetime(),
		"withdrawal_reason": reason_row.name,
	}
	if sr.meta.get_field("rejection_reason"):
		updates["rejection_reason"] = summary
	if sr.meta.get_field("close_outcome"):
		updates["close_outcome"] = outcome
	sr.db_set(updates, update_modified=True)

	# The order has to agree with the ticket, or billing and reporting read two
	# different endings for the same job.
	if sr.service_order:
		try:
			so = frappe.get_doc("Sales Order", sr.service_order)
			so.flags.ignore_validate_update_after_submit = True
			so.db_set("repair_outcome",
			          outcome if outcome != "Customer Cancelled" else "Customer Cancelled",
			          update_modified=True)
		except Exception:
			frappe.log_error(frappe.get_traceback(),
			                 f"GoFix: could not set outcome on {sr.service_order}")

	handback = _release_device(sr, outcome, summary)

	sr.add_comment(
		"Info",
		_("Closed as {0} — {1}.{2}").format(
			outcome, summary,
			_(" Device issued back on {0}.").format(handback) if handback else "",
		),
	)
	return {
		"ok": True,
		"service_request": sr.name,
		"outcome": outcome,
		"decision": mapping["decision"],
		"reason": reason_row.name,
		"summary": summary,
		"handback_entry": handback,
	}


def _release_device(sr, outcome: str, summary: str) -> str | None:
	"""Issue the customer's device back out of custody, if we are holding it."""
	try:
		from gofix.customer_device_stock import release_customer_device

		entry = release_customer_device(sr, reason=_("Closed {0}: {1}").format(outcome, summary))
		if entry:
			sr.db_set("customer_device_released_entry", entry, update_modified=False)
		return entry
	except Exception:
		frappe.log_error(frappe.get_traceback(),
		                 f"GoFix: custody release failed closing {sr.name}")
		return None
