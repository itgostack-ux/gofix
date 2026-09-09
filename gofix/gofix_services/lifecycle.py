"""When a repair may still be changed, and when it may not.

Three moments decide almost everything about a ticket's back half.

**Quality check closing** is the point the workshop stops having an opinion. A
device that has passed or failed QC has been judged; calling it unrepairable
afterwards contradicts a decision somebody already signed, and the two verdicts
then disagree on the same ticket with no way to tell which is true.

**Invoicing starting** is the point money enters. Before it, a ticket can be
put back to work and nothing outside the workshop notices. After it there is a
document with a number on it, in the customer's hands and in the books, and
reopening quietly would leave that invoice describing work that is no longer
what happened.

**Invoicing completing** is the point the device stops being ours to hold. The
handover already refused to release a device with money outstanding, but it did
not notice when *no invoice existed at all* -- a repair that was never billed
sailed through the gate, because zero unpaid invoices reads the same as zero
invoices. Both mean "nothing owed" to a naive check; only one means the customer
has paid.

Reopening between those last two points is legitimate -- QC has judged it,
nobody has billed it, and the workshop wants another go. It is also the one
change that undoes a completed quality decision, so it is not a click: it goes
to the zonal sales manager as an exception, like every other departure from the
normal path on this bench.
"""

import frappe
from frappe import _
from frappe.utils import flt

REOPEN_EXCEPTION_TYPE = "Service Reopen After QC"
REOPEN_APPROVER_ROLE = "CH Zonal Sales Manager"

# A quality check that has reached a verdict. Awaiting and Pending are the
# workshop still looking at it; blank is nobody having started.
QC_CLOSED_STATES = ("Pass", "Fail")

_EXCEPTION_APPROVED_STATES = ("Approved", "Auto-Approved")


def _doc(sr):
    return frappe.get_doc("Service Request", sr) if isinstance(sr, str) else sr


def qc_is_closed(sr) -> bool:
    """True once the quality check has reached a verdict."""
    return (_doc(sr).get("qc_status") or "") in QC_CLOSED_STATES


def _invoice_names(sr) -> list:
    sr = _doc(sr)
    names = [r.invoice for r in (sr.get("service_invoices") or []) if r.invoice]
    if sr.get("service_invoice") and sr.service_invoice not in names:
        names.append(sr.service_invoice)
    return names


def invoicing_has_started(sr) -> bool:
    """True once a bill exists for this repair, in any state including draft.

    A draft counts. Somebody has begun billing, the numbers are being prepared,
    and putting the job back on the bench underneath that is how an invoice ends
    up describing work that did not happen.
    """
    names = _invoice_names(sr)
    if not names:
        return False
    return bool(frappe.db.exists(
        "Sales Invoice", {"name": ("in", names), "docstatus": ("<", 2)}))


def invoice_is_complete(sr) -> dict:
    """Whether this repair has been billed and settled.

    Complete means a submitted invoice exists AND nothing is outstanding on it.
    "No outstanding" alone is not enough: a repair nobody ever billed also has
    nothing outstanding.
    """
    names = _invoice_names(sr)
    if not names:
        return {"complete": False, "reason": _("No invoice has been raised for this repair.")}

    submitted = frappe.get_all(
        "Sales Invoice",
        filters={"name": ("in", names), "docstatus": 1},
        fields=["name", "outstanding_amount", "grand_total"],
        limit_page_length=50)
    if not submitted:
        return {"complete": False,
                "reason": _("The invoice for this repair has not been submitted.")}

    unpaid = [r for r in submitted if flt(r.outstanding_amount) > 0]
    if unpaid:
        return {
            "complete": False,
            "reason": _("{0} outstanding on {1}.").format(
                frappe.format_value(sum(flt(r.outstanding_amount) for r in unpaid),
                                    {"fieldtype": "Currency"}),
                ", ".join(r.name for r in unpaid)),
            "outstanding": [{"invoice": r.name, "outstanding": flt(r.outstanding_amount)}
                            for r in unpaid],
        }
    return {"complete": True, "invoices": [r.name for r in submitted]}


# ── Not repairable ───────────────────────────────────────────────────────────

def assert_repairable_verdict_open(sr) -> None:
    """A quality check that has closed has already answered this question."""
    sr = _doc(sr)
    if qc_is_closed(sr):
        frappe.throw(
            _("Quality check on {0} closed as {1}. A device that has been through "
              "QC cannot then be called unrepairable — reopen the repair if the "
              "verdict was wrong.").format(sr.name, sr.get("qc_status")),
            title=_("Quality Check Already Closed"))


# ── Reopen ───────────────────────────────────────────────────────────────────

def reopen_blockers(sr) -> list:
    """Why this repair cannot be put back to work, in the customer's terms."""
    sr = _doc(sr)
    blockers = []
    if not qc_is_closed(sr):
        blockers.append(_("Quality check has not closed yet (currently {0}) — "
                          "the repair is still open, so there is nothing to reopen.").format(
                              sr.get("qc_status") or _("not started")))
    if sr.get("reopen_active"):
        blockers.append(_("This repair is already back on the bench."))
    if invoicing_has_started(sr):
        blockers.append(_("Billing has already started on this repair. Once an "
                          "invoice exists the job cannot be reopened — raise a "
                          "fresh repair against the same device instead."))
    return blockers


@frappe.whitelist()
def can_reopen(service_request) -> dict:
    sr = _doc(service_request)
    blockers = reopen_blockers(sr)
    return {
        "can_reopen": not blockers,
        "blockers": blockers,
        "qc_status": sr.get("qc_status") or "",
        "approver_role": REOPEN_APPROVER_ROLE,
        "reopen_count": int(sr.get("reopen_count") or 0),
        "already_open": bool(sr.get("reopen_active")),
    }


@frappe.whitelist(methods=["POST"])
def request_reopen(service_request, reason) -> dict:
    """Ask the zonal sales manager to put a QC-closed repair back to work."""
    reason = (reason or "").strip()
    if not reason:
        frappe.throw(_("Say why this repair needs to go back to the bench."),
                     title=_("Reason Required"))

    sr = _doc(service_request)
    blockers = reopen_blockers(sr)
    if blockers:
        frappe.throw("<br>".join(blockers), title=_("Cannot Reopen"))

    try:
        # Same entry point the estimate override uses, so a reopen is approved
        # through the machinery the bench already routes exceptions with.
        from ch_item_master.ch_item_master.exception_api import raise_exception
    except ImportError:
        frappe.throw(_("The exception framework is not installed, so a reopen "
                       "cannot be approved by anyone."),
                     title=_("Missing App Dependency"))

    result = raise_exception(
        exception_type=REOPEN_EXCEPTION_TYPE,
        company=sr.get("company"),
        reason=_("Reopen {0} after quality check closed as {1}. {2}").format(
            sr.name, sr.get("qc_status"), reason),
        reference_doctype="Service Request",
        reference_name=sr.name,
        store_warehouse=sr.get("source_warehouse"),
        customer=sr.get("customer"),
    )
    name = result.get("name") or ""
    status = result.get("status") or "Pending"

    if status in _EXCEPTION_APPROVED_STATES:
        _apply_reopen(sr, name, reason)
        return {"reopened": 1, "exception": name, "status": status}

    return {"reopened": 0, "exception": name, "status": status,
            "message": _("Sent to the {0} for approval. The repair stays closed "
                         "until they approve it.").format(REOPEN_APPROVER_ROLE)}


def _apply_reopen(sr, exception_name: str, reason: str = "") -> None:
    """Put the repair back to work, on the fields the table already has.

    No new columns: tabService Request carries 243 of them and 63,460 of the
    65,535 bytes a MySQL row allows, so widening it fails the ALTER. reopen_reason
    is TEXT and stored off-row, which is where the approval reference goes.
    """
    from frappe.utils import now_datetime

    sr = _doc(sr)
    count = int(sr.get("reopen_count") or 0) + 1
    note = _("Reopened after QC closed as {0}, approved under {1}. {2}").format(
        sr.get("qc_status") or "—", exception_name, (reason or "").strip())
    frappe.db.set_value("Service Request", sr.name, {
        "reopen_active": 1,
        "reopen_count": count,
        "reopen_reason": note,
        "last_reopened_by": frappe.session.user,
        "last_reopened_at": now_datetime(),
        # The workshop has to judge it again; the old verdict stays in the note.
        "qc_status": "Awaiting",
    }, update_modified=True)
    frappe.get_doc("Service Request", sr.name).add_comment("Info", note)


def on_reopen_exception_approved(exception_doc, method=None) -> None:
    """Apply a reopen the moment its exception is approved."""
    if exception_doc.get("exception_type") != REOPEN_EXCEPTION_TYPE:
        return
    if exception_doc.get("status") not in _EXCEPTION_APPROVED_STATES:
        return
    if exception_doc.get("reference_doctype") != "Service Request":
        return
    sr_name = exception_doc.get("reference_name")
    if sr_name and frappe.db.exists("Service Request", sr_name):
        _apply_reopen(sr_name, exception_doc.name,
                      exception_doc.get("reason") or "")
