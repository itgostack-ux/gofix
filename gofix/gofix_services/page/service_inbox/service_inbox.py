"""The screen for requests that have not become tickets yet.

The inbox API can already take a request in, recognise a caller and convert
one. What it could not do was show somebody the queue. Without a screen the
requests were only visible through a raw list view, which shows a row per
record and nothing about the conversation -- so the person whose job it is to
work through them could not see what the customer actually said, what has
already been done about it, or which ones have been sitting untouched.

This page is that view: the queue on the left, the whole of a request on the
right, and the handful of actions that move one along.
"""

import frappe
from frappe import _
from frappe.utils import now_datetime, time_diff_in_hours

from gofix.gofix_services.inbox import OPEN_STATUSES

CHANNELS = ["Website", "Mobile App", "WhatsApp", "Phone Call", "Email",
            "Social", "Marketplace", "Partner", "Walk-in", "Other"]
STATUSES = ["New", "Contacted", "Scheduled", "Converted", "Duplicate", "Spam", "Closed"]


@frappe.whitelist()
def get_context_data(company=None) -> dict:
    """What the toolbar needs, and the counts on the stat strip."""
    frappe.has_permission("GoFix Service Inbox", throw=True)

    companies = [r.name for r in frappe.get_all("Company", fields=["name"], order_by="name")]
    if company and company not in companies:
        company = None
    company = company or frappe.defaults.get_user_default("Company")

    return {
        "company": company,
        "companies": companies,
        "channels": CHANNELS,
        "statuses": STATUSES,
        "counts": _counts(company),
        "can_write": bool(frappe.has_permission("GoFix Service Inbox", "write")),
    }


def _counts(company=None) -> dict:
    """One count per status, plus what came in today.

    Counted through get_list rather than a grouped SQL count so the company
    boundary applies -- a raw count would happily tell a GoFix coordinator how
    many enquiries Bestbuy has.
    """
    base = {"company": company} if company else {}
    counts = {}
    for state in STATUSES:
        counts[state] = len(frappe.get_list(
            "GoFix Service Inbox", filters={**base, "status": state},
            fields=["name"], limit_page_length=0))
    counts["Open"] = sum(counts.get(s, 0) for s in OPEN_STATUSES)
    counts["Today"] = len(frappe.get_list(
        "GoFix Service Inbox",
        filters={**base, "received_at": (">=", frappe.utils.today())},
        fields=["name"], limit_page_length=0))
    return counts


@frappe.whitelist()
def get_requests(company=None, status=None, channel=None, search=None,
                 assigned=None, limit=100) -> dict:
    """The queue itself, newest first, with the age that makes one urgent."""
    frappe.has_permission("GoFix Service Inbox", throw=True)

    filters = {}
    if company:
        filters["company"] = company
    if channel:
        filters["channel"] = channel
    if assigned == "me":
        filters["assigned_to"] = frappe.session.user
    elif assigned == "unassigned":
        filters["assigned_to"] = ("in", ["", None])

    if status and status != "Open":
        filters["status"] = status
    else:
        filters["status"] = ("in", OPEN_STATUSES)

    if search:
        from gofix.gofix_services.doctype.gofix_service_inbox.gofix_service_inbox import (
            normalise_phone)

        digits = normalise_phone(search)
        if len(digits) >= 4:
            filters["contact_number"] = ("like", f"%{digits}%")
        else:
            filters["customer_name"] = ("like", f"%{search}%")

    rows = frappe.get_list(
        "GoFix Service Inbox", filters=filters,
        fields=["name", "channel", "status", "received_at", "customer_name",
                "contact_number", "customer", "device_brand", "device_model",
                "device_category", "issue_category", "issue_description",
                "preferred_store", "preferred_datetime", "assigned_to",
                "service_request", "company", "first_response_at", "referral_source"],
        order_by="received_at desc", limit_page_length=int(limit))

    now = now_datetime()
    for row in rows:
        row["age_hours"] = round(time_diff_in_hours(now, row.received_at), 1) \
            if row.received_at else 0
        row["awaiting_response"] = not row.first_response_at and row.status == "New"
    return {"rows": rows, "counts": _counts(company)}


@frappe.whitelist()
def get_request(name) -> dict:
    """Everything about one request, including what has been said about it."""
    doc = frappe.get_doc("GoFix Service Inbox", name)
    doc.check_permission("read")

    data = doc.as_dict()
    data["notes"] = [{
        "note": n.note, "channel": n.channel, "noted_by": n.noted_by,
        "note_datetime": str(n.note_datetime or ""),
    } for n in reversed(doc.get("notes") or [])]

    # What else we know about this number, so the coordinator sees the whole
    # customer rather than this one message.
    data["other_requests"] = frappe.get_list(
        "GoFix Service Inbox",
        filters={"contact_number": doc.contact_number, "name": ("!=", doc.name)},
        fields=["name", "channel", "status", "received_at", "issue_description"],
        order_by="received_at desc", limit_page_length=5)

    data["repairs"] = frappe.get_list(
        "Service Request", filters={"contact_number": doc.contact_number,
                                    "docstatus": ("<", 2)},
        fields=["name", "decision", "device_model", "qc_status",
                "delivered_datetime", "creation"],
        order_by="creation desc", limit_page_length=5)
    return data


@frappe.whitelist(methods=["POST"])
def add_note(name, note, channel=None) -> dict:
    """Record what was said, and treat it as the first response.

    Whether anyone has replied is the one thing an inbox must be able to
    answer, so the first note stamps it rather than leaving it to be inferred.
    """
    doc = frappe.get_doc("GoFix Service Inbox", name)
    doc.check_permission("write")
    if not (note or "").strip():
        frappe.throw(_("There is nothing to record."), title=_("Empty Note"))

    doc.add_note(note.strip(), channel)
    if not doc.first_response_at:
        doc.first_response_at = now_datetime()
    if doc.status == "New":
        doc.status = "Contacted"
    doc.save(ignore_permissions=True)
    return {"ok": True, "status": doc.status}


@frappe.whitelist(methods=["POST"])
def schedule(name, when, note=None) -> dict:
    """Agree a time with the customer."""
    doc = frappe.get_doc("GoFix Service Inbox", name)
    doc.check_permission("write")
    doc.preferred_datetime = when
    doc.status = "Scheduled"
    if not doc.first_response_at:
        doc.first_response_at = now_datetime()
    doc.add_note(note or _("Scheduled for {0}.").format(when))
    doc.save(ignore_permissions=True)
    return {"ok": True, "when": str(doc.preferred_datetime)}


@frappe.whitelist(methods=["POST"])
def assign(name, user=None) -> dict:
    """Give a request an owner, so it stops being everybody's and nobody's."""
    doc = frappe.get_doc("GoFix Service Inbox", name)
    doc.check_permission("write")
    doc.assigned_to = user or frappe.session.user
    doc.add_note(_("Assigned to {0}.").format(doc.assigned_to))
    doc.save(ignore_permissions=True)
    return {"ok": True, "assigned_to": doc.assigned_to}
