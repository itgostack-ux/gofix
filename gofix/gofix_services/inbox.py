"""The service inbox: taking requests in, and turning them into tickets.

Three jobs.

**Ingestion.** ``push_request`` is the one door every channel comes through --
website form, mobile app, WhatsApp webhook, a call the front desk logs. It is
idempotent on the channel's own reference, because webhooks retry and a customer
who taps Send twice should not become two requests.

**Recognition.** ``lookup_by_phone`` is what the counter calls when it types a
number. It answers one question -- have we heard from this person before? -- and
returns their open requests, their open repairs and their waiting token together,
so the store greets a returning customer instead of starting from a blank form.

**Conversion.** ``convert_to_service_request`` turns a request into a ticket,
carrying across everything the customer already told us and linking the two so
the enquiry is not orphaned. The request is not the ticket: it may never become
one, may be a duplicate, or may be spam, and each of those is a legitimate end.
"""

import frappe
from frappe import _
from frappe.utils import now_datetime

from gofix.gofix_services.doctype.gofix_service_inbox.gofix_service_inbox import normalise_phone

OPEN_STATUSES = ("New", "Contacted", "Scheduled")


# ── In ───────────────────────────────────────────────────────────────────────

@frappe.whitelist(methods=["POST"])
def push_request(channel, contact_number, company=None, customer_name=None,
                 issue_description=None, external_ref=None, **kwargs) -> dict:
    """Land a request from any channel.

    Idempotent on (channel, external_ref): a webhook that retries, or a customer
    who taps Send twice, updates the existing request rather than creating a
    second one. Without a reference there is nothing to match on, so a new
    request is created and de-duplication is left to the person handling it.
    """
    phone = normalise_phone(contact_number)
    if not phone:
        frappe.throw(_("A contact number is required to log a request."),
                     title=_("Contact Number Missing"))

    if external_ref:
        existing = frappe.db.get_value(
            "GoFix Service Inbox",
            {"channel": channel, "external_ref": external_ref}, "name")
        if existing:
            doc = frappe.get_doc("GoFix Service Inbox", existing)
            doc.add_note(_("Channel re-sent this request."), channel)
            doc.save(ignore_permissions=True)
            return {"ok": True, "name": doc.name, "duplicate": True,
                    "message": _("This request was already received.")}

    doc = frappe.new_doc("GoFix Service Inbox")
    doc.update({
        "channel": channel,
        "contact_number": phone,
        "customer_name": customer_name,
        "issue_description": issue_description,
        "external_ref": external_ref,
        "received_at": now_datetime(),
        "status": "New",
        "company": company or _default_company(),
    })
    for field in ("email", "alternate_number", "city", "preferred_store",
                  "device_category", "device_brand", "device_model", "device_item",
                  "serial_no", "issue_category", "preferred_datetime",
                  "referral_source", "attachment"):
        if kwargs.get(field):
            doc.set(field, kwargs[field])

    doc.insert(ignore_permissions=True)
    return {"ok": True, "name": doc.name, "duplicate": False,
            "message": _("Request logged as {0}").format(doc.name)}


def _default_company():
    return (frappe.defaults.get_user_default("company")
            or frappe.db.get_single_value("Global Defaults", "default_company"))


# ── Recognition ──────────────────────────────────────────────────────────────

@frappe.whitelist()
def lookup_by_phone(phone, company=None) -> dict:
    """Everything we already know about whoever is on this number.

    Called the moment the counter types a number. Resolution goes through
    ``identity.resolve``, so it answers for the *person* rather than the
    string: once a customer is recognised, their open requests are gathered
    across every number we hold for them. A number that resolves to more than
    one customer says so instead of picking.
    """
    from gofix.gofix_services.identity import resolve

    found = resolve(phone, company=company)
    return {
        "phone": found["phone"],
        "requests": found["requests"],
        "service_requests": found["repairs"],
        "token": found["token"],
        "customer": ({"name": found["customer"], "customer_name": found["customer_name"]}
                     if found["customer"] else None),
        "customers": found["customers"],
        "ambiguous": found["ambiguous"],
        "numbers": found["numbers"],
        "latest_contact": found["latest_contact"],
        "known": found["known"],
    }


# ── Out ──────────────────────────────────────────────────────────────────────

@frappe.whitelist()
def prefill_from_request(inbox) -> dict:
    """The intake form, already filled in with what the customer told us."""
    doc = frappe.get_doc("GoFix Service Inbox", inbox)
    doc.check_permission("read")
    return {
        "inbox": doc.name,
        "customer": doc.customer,
        "customer_name": doc.customer_name,
        "contact_number": doc.contact_number,
        "email": doc.email,
        "company": doc.company,
        "device_category": doc.device_category,
        "device_brand": doc.device_brand,
        "device_model": doc.device_model,
        "device_item": doc.device_item,
        "serial_no": doc.serial_no,
        "issue_category": doc.issue_category,
        "issue_description": doc.issue_description,
        "referral_source": doc.referral_source,
        "preferred_store": doc.preferred_store,
        "channel": doc.channel,
    }


@frappe.whitelist(methods=["POST"])
def link_to_service_request(inbox, service_request) -> dict:
    """Tie a request to the ticket it became.

    Kept separate from creating the ticket so the counter's existing intake --
    which already knows how to book a device in -- stays the only place a
    Service Request is born. This just closes the loop.
    """
    doc = frappe.get_doc("GoFix Service Inbox", inbox)
    doc.check_permission("write")

    if doc.service_request and doc.service_request != service_request:
        frappe.throw(
            _("This request is already linked to {0}.").format(doc.service_request),
            title=_("Already Converted"))

    if not frappe.db.exists("Service Request", service_request):
        frappe.throw(_("Service Request {0} does not exist.").format(service_request))

    doc.service_request = service_request
    doc.status = "Converted"
    if not doc.first_response_at:
        doc.first_response_at = now_datetime()
    doc.add_note(_("Converted to Service Request {0}.").format(service_request))
    doc.save(ignore_permissions=True)

    # Close the customer's other open enquiries about the same device, so the
    # inbox does not keep showing work that is now under way.
    siblings = frappe.get_all("GoFix Service Inbox", filters={
        "name": ("!=", doc.name), "contact_number": doc.contact_number,
        "status": ("in", OPEN_STATUSES)}, pluck="name")
    for name in siblings:
        frappe.db.set_value("GoFix Service Inbox", name,
                            {"status": "Duplicate", "duplicate_of": doc.name},
                            update_modified=False)

    return {"ok": True, "service_request": service_request,
            "superseded": len(siblings), "superseded_names": siblings,
            "message": _("Linked to {0}").format(service_request)}


@frappe.whitelist(methods=["POST"])
def set_status(inbox, status, reason=None) -> dict:
    """Move a request along, or end it.

    Not every request becomes a repair, and pretending otherwise leaves an
    inbox nobody trusts. Closing one with a reason is a legitimate outcome.
    """
    doc = frappe.get_doc("GoFix Service Inbox", inbox)
    doc.check_permission("write")

    if status == "Converted":
        frappe.throw(_("A request becomes Converted by booking the device in, "
                       "not by setting the status."), title=_("Convert It Instead"))

    doc.status = status
    if reason:
        doc.closed_reason = reason
    if not doc.first_response_at and status != "New":
        doc.first_response_at = now_datetime()
    doc.add_note(_("Status set to {0}.{1}").format(status, f" {reason}" if reason else ""))
    doc.save(ignore_permissions=True)
    return {"ok": True, "status": status}


@frappe.whitelist()
def get_inbox(company=None, status=None, channel=None, search=None, limit=50) -> dict:
    """The queue, as a screen would show it."""
    filters = {}
    if company:
        filters["company"] = company
    filters["status"] = status if status else ("in", OPEN_STATUSES)
    if channel:
        filters["channel"] = channel
    if search:
        number = normalise_phone(search)
        if len(number) >= 6:
            filters["contact_number"] = ("like", f"%{number}%")

    rows = frappe.get_list(
        "GoFix Service Inbox", filters=filters,
        fields=["name", "channel", "status", "received_at", "customer_name",
                "contact_number", "device_brand", "device_model", "issue_category",
                "issue_description", "preferred_store", "assigned_to",
                "service_request", "company"],
        order_by="received_at desc", limit_page_length=int(limit))

    # Counted one status at a time: get_list refuses "count(name) as n" as a
    # string, and the alternative that does not is raw SQL, which would sidestep
    # the company boundary this doctype is registered under.
    counts = {}
    base = {"company": company} if company else {}
    for state in ("New", "Contacted", "Scheduled", "Converted", "Duplicate",
                  "Spam", "Closed"):
        n = len(frappe.get_list("GoFix Service Inbox",
                                filters={**base, "status": state},
                                fields=["name"], limit_page_length=0))
        if n:
            counts[state] = n

    return {"rows": rows, "counts": counts,
            "open_total": sum(counts.get(s, 0) for s in OPEN_STATUSES)}
