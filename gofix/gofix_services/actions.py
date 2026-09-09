"""What may be done to a repair right now, decided in one place.

Every surface used to answer this for itself. The POS card carried inline lists
-- ``["Draft","Accepted","In Service"]`` here, ``!["Delivered","Invoiced",...]``
there -- and the Ops Hub carried different ones, so the same ticket offered
different actions depending on which screen you happened to be looking at. The
screenshot that prompted this showed a ticket sitting at Invoice with a red
"Not Repairable" button still on it: the server would have refused the click,
but nothing had told the button to go away.

That is the shape every serious service desk avoids. ServiceNow computes UI
actions from the record's state server-side and the client renders what it is
given; SAP CS gates transactions on status profiles; Zendesk derives available
macros from ticket state. The rule lives with the data, once, and every screen
asks.

So this module answers one question -- "what can be done to this ticket?" --
and returns, for each action, whether it is allowed and, when it is not, the
sentence to show the person asking. A hidden button and a refused click now
come from the same rule, which is the only way they stay in step.
"""

import frappe
from frappe import _
from frappe.utils import flt

from gofix.gofix_services import lifecycle

# A ticket that has reached one of these is finished; nothing operational is
# offered on it beyond looking at it.
TERMINAL_DECISIONS = ("Delivered", "Cancelled", "Rejected", "Withdrawn", "Expired")

# Work is genuinely under way: the device is with us and the job is live.
IN_PROGRESS_DECISIONS = ("Accepted", "In Service")


def _sr(sr):
    return frappe.get_doc("Service Request", sr) if isinstance(sr, str) else sr


def _decision(sr) -> str:
    return (sr.get("decision") or sr.get("service_outcome") or "").strip()


def _has_open_job(sr) -> bool:
    """Is a repair job already running on this ticket?

    Read from Job Assignment rather than a field on the request: there is no
    such column, and the board row that carries ``job_assignment`` is computed
    the same way. Asking the source keeps the button and the server in step even
    when the caller passes a bare docname.
    """
    return bool(frappe.db.exists("Job Assignment", {
        "service_request": sr.name,
        "docstatus": ("<", 2),
        "assignment_status": ("not in", ("Completed", "Cancelled", "Rejected")),
    }))


def _allow(reason=None) -> dict:
    return {"allowed": not reason, "reason": reason or ""}


@frappe.whitelist()
def available_actions(service_request) -> dict:
    """Every action a screen might offer, and whether this ticket permits it.

    Returned rather than assumed, so the POS card, the Ops Hub and anything
    built later all hide the same buttons for the same reasons.
    """
    sr = _sr(service_request)
    decision = _decision(sr)
    qc_closed = lifecycle.qc_is_closed(sr)
    billing_started = lifecycle.invoicing_has_started(sr)
    billing = lifecycle.invoice_is_complete(sr)
    terminal = decision in TERMINAL_DECISIONS

    actions = {}

    # ── Close without repair ────────────────────────────────────────────
    # Only while the job is actually running. Before it starts there is
    # nothing to abandon; after QC has judged it the workshop has already
    # answered, and after billing there is a document that says otherwise.
    if terminal:
        reason = _("This repair is already {0}.").format(decision.lower())
    elif decision not in IN_PROGRESS_DECISIONS:
        reason = _("The repair has not started yet.") if not decision else \
            _("The repair is no longer in progress (it is {0}).").format(decision.lower())
    elif qc_closed:
        reason = _("Quality check closed as {0} — reopen it if that verdict was wrong.").format(
            sr.get("qc_status"))
    elif billing_started:
        reason = _("Billing has already started on this repair.")
    else:
        reason = None
    actions["close_without_repair"] = _allow(reason)

    # ── Customer return ─────────────────────────────────────────────────
    # The device goes home once it has been billed and settled. Not before:
    # releasing it earlier is giving away a repair nobody has paid for.
    if terminal:
        reason = _("This repair is already {0}.").format(decision.lower())
    elif not billing.get("complete"):
        reason = billing.get("reason") or _("The repair has not been invoiced.")
    else:
        reason = None
    actions["customer_return"] = _allow(reason)

    # ── Reopen ──────────────────────────────────────────────────────────
    blockers = lifecycle.reopen_blockers(sr)
    actions["reopen"] = _allow("; ".join(blockers) if blockers else None)
    actions["reopen"]["approver_role"] = lifecycle.REOPEN_APPROVER_ROLE
    actions["reopen"]["needs_approval"] = True

    # ── Billing ─────────────────────────────────────────────────────────
    if terminal:
        reason = _("This repair is already {0}.").format(decision.lower())
    elif not qc_closed:
        reason = _("Quality check has not closed yet.")
    elif sr.get("qc_status") == "Fail":
        reason = _("Quality check failed — a failed repair is not billed.")
    else:
        reason = None
    actions["add_to_bill"] = _allow(reason)

    # ── Create job ──────────────────────────────────────────────────────
    # Opening a repair job is how work starts, so it belongs to a ticket that
    # has been accepted and is not yet finished.
    if terminal:
        reason = _("This repair is already {0}.").format(decision.lower())
    elif _has_open_job(sr):
        reason = _("A repair job is already open on this ticket.")
    elif decision not in IN_PROGRESS_DECISIONS:
        reason = _("Accept the repair before opening a job.")
    elif qc_closed:
        reason = _("The work is finished — quality check closed as {0}.").format(
            sr.get("qc_status"))
    else:
        reason = None
    actions["create_job"] = _allow(reason)

    # ── Sending the device away ─────────────────────────────────────────
    # Movement itself is decided by custody state (device_movement_options),
    # which knows where the device physically is. What belongs here is whether
    # the ticket is in a state where sending it anywhere makes sense at all.
    if terminal:
        reason = _("This repair is already {0}.").format(decision.lower())
    elif billing.get("complete"):
        reason = _("The repair is billed and settled — it goes to the customer, "
                   "not to another store.")
    elif decision not in ("Draft",) + IN_PROGRESS_DECISIONS:
        reason = _("The repair is not in a state to be moved ({0}).").format(
            decision.lower() or _("no decision yet"))
    else:
        reason = None
    actions["dispatch"] = _allow(reason)

    # ── Always available while the ticket exists ────────────────────────
    # A note is a record of what happened, and refusing one loses information
    # for no benefit. It stays available even on a finished ticket.
    actions["add_note"] = _allow(None)
    actions["raise_exception"] = _allow(
        _("This repair is already {0}.").format(decision.lower()) if terminal else None)

    return {
        "service_request": sr.name,
        "decision": decision,
        "qc_status": sr.get("qc_status") or "",
        "qc_closed": qc_closed,
        "billing_started": billing_started,
        "billing_complete": bool(billing.get("complete")),
        "terminal": terminal,
        "actions": actions,
    }


def assert_action_allowed(service_request, action: str) -> None:
    """Refuse an action the ticket's state does not permit.

    The server check and the hidden button read the same rule, so a button that
    is gone was never going to work and a button that is there always will.
    """
    state = available_actions(service_request)
    entry = state["actions"].get(action)
    if entry is None:
        frappe.throw(_("Unknown action {0}.").format(action), title=_("Not Allowed"))
    if not entry["allowed"]:
        frappe.throw(entry["reason"] or _("This action is not available now."),
                     title=_("Not Allowed"))
