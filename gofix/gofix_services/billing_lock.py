"""Billing lock, multi-invoice register, and recorded reopen for a repair.

The problem this closes
----------------------
A billed repair stayed fully editable. Nothing stopped a Service Request or its
Service Order being changed after the customer had paid, and because the invoice
was never linked back to the order there was no trail saying which figure the
customer actually agreed to. A repair could also only ever hold one invoice, in a
single Link field, so billing additional work after a reopen silently overwrote
the record of the first bill.

How it works
------------
1. **Register.** Every invoice raised against a repair is appended to
   ``Service Request.service_invoices``. ``service_invoice`` is kept pointing at
   the first one, because a great deal of existing code reads it and expects the
   original bill.
2. **Lock.** The moment a submitted invoice exists the repair is locked. While
   locked, neither the request nor its Service Order accepts edits.
3. **Reopen.** The lock is lifted only by ``reopen_service_request``, which
   demands a reason, stamps who and when, increments a counter and writes the
   event to the request's own status log. Reopening is therefore an act with a
   record, not an edit that leaves no trace. Billing again clears the flag and
   re-locks, and the new invoice is registered against that reopen number.

Deliberately not enforced here
------------------------------
Cancelling an invoice does not by itself unlock the repair. A cancelled bill is
a correction, and the accepted correction path is to reopen with a reason so the
history says why. The lock is also advisory for System Managers by design -- it
guards the ordinary counter workflow, not a deliberate administrative fix.

Retail is untouched: everything here keys on Service Request and on Sales Orders
flagged ``is_service_order``.
"""

import frappe
from frappe import _
from frappe.utils import flt, now_datetime


# Fields a locked repair may still change. These are operational stamps written
# by the system after billing -- the device physically moving, the handover
# being recorded -- not commercial terms. Blocking them would freeze the very
# steps that follow payment.
_ALLOWED_WHILE_LOCKED = {
    "is_billing_locked", "reopen_active", "reopen_count", "reopen_reason",
    "last_reopened_by", "last_reopened_at", "service_invoices", "service_invoice",
    "decision", "workflow_state", "status_log", "current_location",
    "transfer_status", "return_delivered_date", "return_dispatched_date",
    "modified", "modified_by", "_comments", "_assign", "_liked_by", "_user_tags",
    "docstatus", "idx", "delivery_otp", "delivery_otp_verified",
    "delivery_otp_sent_at", "delivery_otp_attempts", "delivery_otp_locked_until",
    "delivery_otp_consumed_at", "accessories_returned", "customer_signature",
    "delivered_datetime", "delivery_remarks",
    # QC certifies the work; it is not a commercial term. A repair billed
    # before its final check must still be able to record that check, and a
    # rework after a reopen must be able to re-answer it.
    "qc_status", "qc_checked_by", "qc_datetime", "qc_remarks", "qc_checklist",
    "rework_count", "qc_pass_datetime",
    # Costing is recomputed by the system when the repair is billed -- it is a
    # measurement of the repair, not a term of it.
    "spare_parts_cost", "spare_parts_revenue", "labor_cost", "suggested_labor_cost",
    "total_repair_cost", "technician_damage_cost", "actual_billed",
    "suggested_total_cost", "price_override_amount", "price_overridden_by",
    "repair_margin", "repair_margin_pct",
}


def is_locked(sr) -> bool:
    """True when this repair is billed and not currently reopened."""
    if isinstance(sr, str):
        sr = frappe.db.get_value(
            "Service Request", sr, ["is_billing_locked", "reopen_active"], as_dict=True) or {}
    return bool(sr.get("is_billing_locked")) and not bool(sr.get("reopen_active"))


def register_invoice(sr, invoice_name: str, billing_reason: str = None) -> None:
    """Record an invoice against the repair and lock it.

    Called after the invoice is submitted. Idempotent: raising the same invoice
    twice cannot double-register it, which matters because two separate billing
    paths both end here.
    """
    if isinstance(sr, str):
        sr = frappe.get_doc("Service Request", sr)

    existing = {r.invoice for r in (sr.get("service_invoices") or [])}
    if invoice_name in existing:
        return

    inv = frappe.db.get_value(
        "Sales Invoice", invoice_name,
        ["posting_date", "grand_total", "outstanding_amount", "docstatus"], as_dict=True) or {}

    reopen_no = int(sr.get("reopen_count") or 0)
    if not billing_reason:
        billing_reason = "Initial" if not existing else (
            "Reopen" if reopen_no else "Additional Work")

    sr.append("service_invoices", {
        "invoice": invoice_name,
        "posting_date": inv.get("posting_date"),
        "grand_total": flt(inv.get("grand_total")),
        "outstanding_amount": flt(inv.get("outstanding_amount")),
        "billing_reason": billing_reason,
        "reopen_sequence": reopen_no,
        "docstatus_label": {0: "Draft", 1: "Submitted", 2: "Cancelled"}.get(
            inv.get("docstatus"), ""),
    })

    updates = {"is_billing_locked": 1, "reopen_active": 0, "reopen_reason": ""}
    # service_invoice keeps pointing at the FIRST bill. Existing code reads it
    # expecting the original, and repointing it at the latest would rewrite
    # history every time additional work was billed.
    if not sr.get("service_invoice"):
        updates["service_invoice"] = invoice_name

    sr.flags.ignore_billing_lock = True
    sr.save(ignore_permissions=True)
    for k, v in updates.items():
        sr.db_set(k, v, update_modified=False)

    # Until now the margin was measured against the approved estimate. What the
    # customer was actually charged is known only here.
    try:
        from gofix.gofix_services.costing import update_service_costing

        update_service_costing(sr)
    except Exception:
        frappe.log_error(frappe.get_traceback(), f"billing_lock: costing failed for {sr.name}")

    _log(sr, "Billed", _("Invoice {0} registered ({1}). Repair locked.").format(
        invoice_name, billing_reason))


@frappe.whitelist(methods=["POST"])
def reopen_service_request(service_request: str, reason: str) -> dict:
    """Lift the billing lock so a billed repair can be worked again.

    A reason is required and recorded. This is the only supported way past the
    lock, so "who reopened this, when, and why" is always answerable.
    """
    from gofix.security import assert_service_request_access

    sr = assert_service_request_access(service_request, permission_type="write")
    reason = (reason or "").strip()
    if not reason:
        frappe.throw(_("A reason is required to reopen a repair."),
                     title=_("Reason Required"))
    # This used to require the repair to be BILLED, and asked nobody. That is
    # backwards: once an invoice exists there is a document in the customer's
    # hands and in the books describing work that reopening would change. A
    # repair may go back to the bench only while the quality check has closed
    # and no bill has been raised -- and because that overturns a completed
    # quality decision, the zonal sales manager approves it.
    from gofix.gofix_services import lifecycle

    return lifecycle.request_reopen(sr.name, reason)


@frappe.whitelist()
def get_billing_state(service_request: str) -> dict:
    """What the counter needs to know before offering an edit or a reopen."""
    from gofix.security import assert_service_request_access

    sr = assert_service_request_access(service_request, permission_type="read")
    invoices = [{
        "invoice": r.invoice, "posting_date": str(r.posting_date or ""),
        "grand_total": flt(r.grand_total), "outstanding": flt(r.outstanding_amount),
        "reason": r.billing_reason, "status": r.docstatus_label,
    } for r in (sr.get("service_invoices") or [])]
    return {
        "locked": is_locked(sr),
        "is_billing_locked": bool(sr.get("is_billing_locked")),
        "reopen_active": bool(sr.get("reopen_active")),
        "reopen_count": int(sr.get("reopen_count") or 0),
        "reopen_reason": sr.get("reopen_reason") or "",
        "invoices": invoices,
        "billed_total": sum(i["grand_total"] for i in invoices),
        "service_order": sr.get("service_order"),
    }


# ── Guards, wired from hooks ─────────────────────────────────────────────────

def guard_service_request(doc, method=None):
    """Refuse commercial edits to a billed repair."""
    if doc.flags.get("ignore_billing_lock") or doc.get("__islocal"):
        return
    if not is_locked(doc):
        return
    changed = _changed_fields(doc)
    if not changed:
        return
    frappe.throw(
        _("This repair has been billed and is locked. Reopen it with a reason "
          "before changing {0}.").format(", ".join(sorted(changed)[:6])),
        title=_("Repair Is Billed"),
    )


def guard_service_order(doc, method=None):
    """Refuse edits to the Service Order of a billed repair."""
    if doc.flags.get("ignore_billing_lock") or doc.get("__islocal"):
        return
    if not doc.get("is_service_order") or not doc.get("service_request"):
        return          # retail Sales Orders are none of this module's business
    if not is_locked(doc.get("service_request")):
        return
    changed = _changed_fields(doc)
    # The close routine legitimately writes these after the invoice exists.
    changed -= {"status", "workflow_state", "qc_status", "per_billed",
                "per_delivered", "billing_status", "advance_paid", "current_location"}
    if not changed:
        return
    frappe.throw(
        _("Service Order {0} belongs to a billed repair and is locked. Reopen "
          "the Service Request with a reason first.").format(doc.name),
        title=_("Repair Is Billed"),
    )


def _changed_fields(doc) -> set:
    """Fields this save would actually change, ignoring the allowed stamps."""
    if not doc.get("name"):
        return set()
    before = frappe.db.get_value(doc.doctype, doc.name, "*", as_dict=True)
    if not before:
        return set()
    changed = set()
    for field, old in before.items():
        if field in _ALLOWED_WHILE_LOCKED:
            continue
        new = doc.get(field)
        if new is None and old is None:
            continue
        if str(old or "") != str(new if new is not None else ""):
            changed.add(field)
    return changed


def _log(sr, event: str, note: str) -> None:
    """Append to the repair's own status log, which is the history readers use."""
    try:
        if not sr.meta.get_field("status_log"):
            return
        sr.append("status_log", {
            "event_type": event if _accepts(sr, "event_type", event) else None,
            "from_status": sr.get("decision"),
            "to_status": event,
            "changed_by": frappe.session.user,
            "changed_at": now_datetime(),
        })
        sr.flags.ignore_billing_lock = True
        sr.save(ignore_permissions=True)
    except Exception:
        # History is valuable but never worth failing a bill or a reopen over.
        frappe.log_error(frappe.get_traceback(), f"billing_lock log failed for {sr.name}")


def _accepts(sr, fieldname: str, value: str) -> bool:
    df = sr.meta.get_field(fieldname)
    if not df or df.fieldtype != "Select":
        return True
    return value in (df.options or "").split("\n")
