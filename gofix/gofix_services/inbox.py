"""The front desk: every way a customer reaches us, in one queue.

Why there is only one record type here
--------------------------------------
A customer wanting something is one fact, however they said it. Modelling the
person standing at the counter separately from the person who messaged an hour
ago gives the shop two lists to work, two places to look someone up, and two
chances to chase them twice.

``POS Kiosk Token`` is already that one record and always was: it is used by
both companies, it covers Sales, Repair, Buyback and Enquiry alike, it carries
the customer, the device, the issue and the links out to a Service Request, an
invoice or a buyback -- and its ``visit_source`` field already listed Web and
WhatsApp beside Kiosk and Counter. The remote channels were designed for; they
had simply never been written.

So a walk-in and a WhatsApp message are the same kind of thing, separated by
one field. That is the shape every serious service desk uses -- ServiceNow logs
a walk-up and a portal request as the same Interaction, Zendesk makes channel
an attribute of a ticket, Qmatic treats a booked appointment and a walk-in as
one visit. The physical queue mechanics (a token number, calling the next
customer) belong to the visits that are actually in the shop, and are a view
over this record rather than a second one.

This module is the API onto that queue for requests that arrive remotely.
"""

import frappe
from frappe import _
from frappe.utils import now_datetime

# Statuses that mean somebody still owes this customer something.
OPEN_STATUSES = ("Waiting", "Hold", "Engaged", "In Progress")

# Channels that arrive before the customer does. Kiosk and Counter are the two
# that mean a person is physically present.
REMOTE_CHANNELS = ("Web", "Mobile App", "WhatsApp", "Phone Call", "Email",
                   "Social", "Marketplace", "Partner", "Appointment")
IN_PERSON_CHANNELS = ("Kiosk", "Counter")
ALL_CHANNELS = IN_PERSON_CHANNELS + REMOTE_CHANNELS + ("Other",)


def normalise_phone(number) -> str:
    """Reduce a number to its last ten digits.

    The same customer reaches us as +91 98404 22782 from WhatsApp, 09840422782
    from a web form and 9840422782 at the counter. Matching on the raw string
    means three records and a counter that recognises none of them.
    """
    digits = "".join(c for c in str(number or "") if c.isdigit())
    return digits[-10:] if len(digits) >= 10 else digits


# ── In ───────────────────────────────────────────────────────────────────────

@frappe.whitelist(methods=["POST"])
def push_request(channel, contact_number, company=None, customer_name=None,
                 issue_description=None, external_ref=None, **kwargs) -> dict:
    """Land a request that arrived from somewhere other than the shop floor.

    Writes the same record a walk-in writes, with the channel saying how it
    got here. Idempotent on (channel, external_ref): a webhook that retries, or
    a customer who taps Send twice, updates the existing request rather than
    creating a second one.
    """
    phone = normalise_phone(contact_number)
    if not phone:
        frappe.throw(_("A contact number is required to log a request."),
                     title=_("Contact Number Missing"))
    if channel not in ALL_CHANNELS:
        frappe.throw(_("{0} is not a channel we recognise.").format(channel),
                     title=_("Unknown Channel"))

    if external_ref:
        existing = frappe.db.get_value(
            "POS Kiosk Token", {"visit_source": channel, "external_ref": external_ref},
            "name")
        if existing:
            add_note(existing, _("Channel re-sent this request."), channel)
            return {"ok": True, "name": existing, "duplicate": True,
                    "message": _("This request was already received.")}

    company = _assert_company(company)

    doc = frappe.new_doc("POS Kiosk Token")
    doc.update({
        "visit_source": channel,
        "customer_phone": phone,
        "customer_name": customer_name,
        "issue_description": issue_description,
        "external_ref": external_ref,
        "status": "Waiting",
        "company": company,
        # A remote request has no purpose until someone reads it; Enquiry is
        # the honest default and the counter narrows it when they respond.
        "visit_purpose": kwargs.get("visit_purpose") or "Enquiry",
    })
    for field in ("email", "alternate_number", "city", "pos_profile", "store",
                  "device_category", "device_brand", "device_model", "device_item",
                  "serial_no", "issue_category", "preferred_datetime",
                  "referral_source", "attachment", "visit_reason"):
        if kwargs.get(field):
            doc.set(field, kwargs[field])

    _match_customer(doc)
    doc.flags.ignore_permissions = True
    doc.flags.ignore_mandatory = True
    doc.insert()
    return {"ok": True, "name": doc.name, "duplicate": False,
            "message": _("Request logged as {0}").format(doc.name)}


def _match_customer(doc) -> None:
    """Recognise a returning customer by their number."""
    if doc.get("linked_customer") or not doc.get("customer_phone"):
        return
    from gofix.gofix_services.identity import customers_for_phone

    found = customers_for_phone(doc.customer_phone)
    if len(found) == 1:                      # never guess between two
        doc.linked_customer = found[0]["customer"]
        if not doc.customer_name:
            doc.customer_name = found[0]["customer_name"]


def _default_company():
    return (frappe.defaults.get_user_default("company")
            or frappe.db.get_single_value("Global Defaults", "default_company"))


def _assert_company(company: str) -> str:
    """Refuse to log a request into a company the caller cannot see.

    The insert runs with ignore_permissions so a kiosk or a webhook can write,
    which means the company has to be checked here rather than relied on from
    the document's own permission check. Without this a Bestbuy user could post
    a request straight into GoFix's queue by naming it in the payload.
    """
    company = company or _default_company()
    if not company:
        frappe.throw(_("A company is required to log a request."),
                     title=_("Company Missing"))

    from ch_item_master.security import get_user_mapped_companies

    allowed = get_user_mapped_companies(frappe.session.user)
    # None means an unrestricted caller -- Administrator, System Manager, and
    # the integration users that legitimately serve every company.
    if allowed is not None and company not in allowed:
        frappe.throw(
            _("You cannot log a request for {0}.").format(company),
            frappe.PermissionError, title=_("Not Your Company"))
    return company


# ── Recognition ──────────────────────────────────────────────────────────────

@frappe.whitelist()
def lookup_by_phone(phone, company=None) -> dict:
    """Everything we already know about whoever is on this number.

    Resolution goes through ``identity.resolve``, so it answers for the person
    rather than the string: once a customer is recognised, open work is
    gathered across every number we hold for them.
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


# ── Working the queue ────────────────────────────────────────────────────────

@frappe.whitelist()
def prefill_from_request(inbox) -> dict:
    """The intake form, already filled in with what the customer told us."""
    doc = frappe.get_doc("POS Kiosk Token", inbox)
    doc.check_permission("read")
    return {
        "inbox": doc.name,
        "customer": doc.linked_customer,
        "customer_name": doc.customer_name,
        "contact_number": doc.customer_phone,
        "email": doc.get("email"),
        "company": doc.company,
        "device_category": doc.get("device_category"),
        "device_brand": doc.get("device_brand"),
        "device_model": doc.get("device_model"),
        "device_item": doc.get("device_item"),
        "serial_no": doc.get("serial_no"),
        "issue_category": doc.get("issue_category"),
        "issue_description": doc.get("issue_description"),
        "referral_source": doc.get("referral_source"),
        "preferred_store": doc.get("pos_profile"),
        "channel": doc.get("visit_source"),
    }


@frappe.whitelist(methods=["POST"])
def add_note(inbox, note, channel=None) -> dict:
    """Record what was said, and treat it as the first response.

    Whether anyone has replied is the one thing a queue must be able to answer,
    so the first note stamps it rather than leaving it to be inferred.
    """
    doc = frappe.get_doc("POS Kiosk Token", inbox)
    doc.check_permission("write")
    if not (note or "").strip():
        frappe.throw(_("There is nothing to record."), title=_("Empty Note"))

    doc.append("notes", {
        "note_datetime": now_datetime(), "channel": channel or doc.visit_source,
        "noted_by": frappe.session.user, "note": note.strip()})
    if not doc.get("first_response_at"):
        doc.first_response_at = now_datetime()
    if doc.status == "Waiting":
        doc.status = "Engaged"
    doc.flags.ignore_permissions = True
    doc.flags.ignore_mandatory = True
    doc.save()
    return {"ok": True, "status": doc.status}


@frappe.whitelist(methods=["POST"])
def link_to_service_request(inbox, service_request) -> dict:
    """Tie a front-desk visit to the ticket it became."""
    doc = frappe.get_doc("POS Kiosk Token", inbox)
    doc.check_permission("write")

    if doc.linked_service_request and doc.linked_service_request != service_request:
        frappe.throw(
            _("This request is already linked to {0}.").format(doc.linked_service_request),
            title=_("Already Converted"))
    if not frappe.db.exists("Service Request", service_request):
        frappe.throw(_("Service Request {0} does not exist.").format(service_request))

    frappe.db.set_value("POS Kiosk Token", doc.name, {
        "linked_service_request": service_request,
        "status": "Converted",
        "first_response_at": doc.get("first_response_at") or now_datetime(),
    }, update_modified=True)

    siblings = frappe.get_all("POS Kiosk Token", filters={
        "name": ("!=", doc.name), "customer_phone": doc.customer_phone,
        "status": ("in", OPEN_STATUSES)}, pluck="name")
    for name in siblings:
        frappe.db.set_value("POS Kiosk Token", name, {
            "status": "Converted", "duplicate_of": doc.name,
            "linked_service_request": service_request,
        }, update_modified=False)

    return {"ok": True, "service_request": service_request,
            "superseded": len(siblings), "superseded_names": siblings,
            "message": _("Linked to {0}").format(service_request)}


@frappe.whitelist(methods=["POST"])
def set_status(inbox, status, reason=None) -> dict:
    """Move a visit along, or end it.

    Not every request becomes work, and pretending otherwise leaves a queue
    nobody trusts. Closing one with a reason is a legitimate outcome.
    """
    doc = frappe.get_doc("POS Kiosk Token", inbox)
    doc.check_permission("write")
    if status == "Converted":
        frappe.throw(_("A visit becomes Converted by booking the device in, "
                       "not by setting the status."), title=_("Convert It Instead"))

    doc.status = status
    if reason:
        doc.closed_reason = reason
    if not doc.get("first_response_at") and status != "Waiting":
        doc.first_response_at = now_datetime()
    doc.append("notes", {
        "note_datetime": now_datetime(), "channel": doc.visit_source,
        "noted_by": frappe.session.user,
        "note": _("Status set to {0}.{1}").format(status, f" {reason}" if reason else "")})
    doc.flags.ignore_permissions = True
    doc.flags.ignore_mandatory = True
    doc.save()
    return {"ok": True, "status": status}


@frappe.whitelist()
def get_inbox(company=None, status=None, channel=None, search=None, limit=50) -> dict:
    """The front-desk queue, as a screen would show it."""
    filters = {}
    if company:
        filters["company"] = company
    filters["status"] = status if status and status != "Open" else ("in", OPEN_STATUSES)
    if channel == "__remote":
        filters["visit_source"] = ("in", REMOTE_CHANNELS)
    elif channel == "__in_person":
        filters["visit_source"] = ("in", IN_PERSON_CHANNELS)
    elif channel:
        filters["visit_source"] = channel
    if search:
        digits = normalise_phone(search)
        if len(digits) >= 4:
            filters["customer_phone"] = ("like", f"%{digits}%")
        else:
            filters["customer_name"] = ("like", f"%{search}%")

    rows = frappe.get_list(
        "POS Kiosk Token", filters=filters,
        fields=["name", "token_display", "visit_source", "visit_purpose", "status",
                "creation", "customer_name", "customer_phone", "linked_customer",
                "device_brand", "device_model", "issue_category", "issue_description",
                "pos_profile", "preferred_datetime", "assigned_to",
                "linked_service_request", "company", "first_response_at"],
        order_by="creation desc", limit_page_length=int(limit))

    return {"rows": rows, "counts": _counts(company),
            "open_total": sum(1 for r in rows if r.status in OPEN_STATUSES)}


def _counts(company=None) -> dict:
    base = {"company": company} if company else {}
    counts = {}
    for state in ("Waiting", "Hold", "Engaged", "In Progress", "Converted",
                  "Dropped", "Expired", "Cancelled", "Completed"):
        n = len(frappe.get_list("POS Kiosk Token", filters={**base, "status": state},
                                fields=["name"], limit_page_length=0))
        if n:
            counts[state] = n
    counts["Open"] = sum(counts.get(s, 0) for s in OPEN_STATUSES)
    counts["Today"] = len(frappe.get_list(
        "POS Kiosk Token", filters={**base, "creation": (">=", frappe.utils.today())},
        fields=["name"], limit_page_length=0))
    return counts
