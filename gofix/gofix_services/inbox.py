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
from frappe.utils import add_days, get_datetime, now_datetime

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


@frappe.whitelist()
def get_options() -> dict:
    """Every list the front desk offers, read from where it is configured.

    Channels, purposes and statuses are the Select options on POS Kiosk Token;
    visit reasons and referral sources are their own masters. Nothing is
    written into the screen, so adding a channel or retiring a purpose is a
    configuration change rather than a release.
    """
    meta = frappe.get_meta("POS Kiosk Token")

    def options(fieldname):
        df = meta.get_field(fieldname)
        return [o for o in (df.options or "").split("\n") if o.strip()] if df else []

    def master(doctype, order="name"):
        if not frappe.db.exists("DocType", doctype):
            return []
        fields = ["name"]
        m = frappe.get_meta(doctype)
        for extra in ("display_order", "disabled"):
            if m.get_field(extra):
                fields.append(extra)
        rows = frappe.get_all(doctype, fields=fields,
                              order_by="display_order asc" if "display_order" in fields else order,
                              limit_page_length=0)
        return [r["name"] for r in rows if not r.get("disabled")]

    channels = options("visit_source")
    return {
        "channels": channels,
        "in_person_channels": [c for c in channels if c in IN_PERSON_CHANNELS],
        "remote_channels": [c for c in channels if c not in IN_PERSON_CHANNELS],
        "purposes": options("visit_purpose"),
        "statuses": options("status"),
        "open_statuses": list(OPEN_STATUSES),
        "visit_reasons": master("GoFix Visit Reason"),
        "referral_sources": master("GoFix Referral Source"),
        "followup_days": _followup_days(),
    }


def _followup_days() -> int:
    """How long a written request waits before the desk must decide again."""
    try:
        from ch_pos.config import get_control_setting

        return max(1, int(get_control_setting("remote_request_followup_days", 3) or 3))
    except Exception:
        return 3


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

    # A written request is waiting on the customer, not on us: it sits on Hold
    # until an agreed date rather than in the live queue. This is the same shape
    # a CRM gives a deal -- an expected close date that somebody has to act on
    # when it arrives -- and it is what keeps the end-of-day sweep from
    # cancelling a message that is still perfectly alive.
    if channel not in IN_PERSON_CHANNELS:
        doc.status = "Hold"
        doc.expires_at = kwargs.get("follow_up_on") or add_days(
            now_datetime(), _followup_days())
    doc.pos_profile = _resolve_store(company, phone, kwargs.get("pos_profile"))
    if doc.pos_profile and not kwargs.get("store"):
        doc.store = frappe.db.get_value("POS Profile", doc.pos_profile, "warehouse")

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
    # Submitted like a walk-in. A draft is invisible to the end-of-day sweep and
    # to the settlement guard, so leaving it as one would quietly exempt written
    # requests from both.
    if frappe.get_meta("POS Kiosk Token").is_submittable and doc.docstatus == 0:
        doc.submit()
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


def _resolve_store(company, phone, explicit=None) -> str:
    """Which store owns this request.

    A lead belongs somewhere. Left unassigned it appeared on every store's
    desk in the company, so four people saw the same customer and any of them
    might have rung. Routing, in order:

      1. The store the customer asked for, when the channel captured one.
      2. The store that served them last -- a returning customer's enquiry
         belongs where they already go, which is also how a CRM assigns by
         territory rather than round-robin.
      3. The company's default front desk, from CH POS Control Settings.

    Returning nothing is allowed and is not a leak: an unrouted request shows
    only under the explicit "Unassigned" filter, where a manager routes it.
    """
    if explicit and not _store_is_open(explicit, company):
        explicit = None
    if explicit:
        return explicit

    number = normalise_phone(phone)
    if number:
        # Their last store, but only if it is still trading. A branch that has
        # since closed would take the request nobody can open the till to see.
        last = frappe.db.sql("""
            SELECT t.pos_profile
            FROM `tabPOS Kiosk Token` t
            JOIN `tabPOS Profile` p ON p.name = t.pos_profile AND p.disabled = 0
            WHERE t.customer_phone = %(p)s AND t.company = %(c)s
            ORDER BY t.creation DESC LIMIT 1
        """, {"p": number, "c": company})
        if last:
            return last[0][0]

    try:
        from ch_pos.config import get_control_setting

        default = get_control_setting("default_front_desk_profile", "")
        if default and _store_is_open(default, company):
            return default
    except Exception:
        pass
    return None


def _store_is_open(pos_profile: str, company: str) -> bool:
    """A store that can actually take the work: right company, not disabled."""
    row = frappe.db.get_value("POS Profile", pos_profile,
                              ["company", "disabled"], as_dict=True)
    return bool(row and row.company == company and not row.disabled)


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


@frappe.whitelist()
def get_visit(name) -> dict:
    """One visit in full: what they told us, and everything said since.

    A request that arrived from a website form, WhatsApp or a helpdesk is a
    conversation, not a single line. The card can only ever show the opening
    remark, so the desk needs somewhere to read the whole exchange and the
    detail the customer supplied alongside it.
    """
    doc = frappe.get_doc("POS Kiosk Token", name)
    doc.check_permission("read")

    data = doc.as_dict()
    data["notes"] = [{
        "note": n.note, "channel": n.channel, "noted_by": n.noted_by,
        "note_datetime": str(n.note_datetime or ""),
    } for n in reversed(doc.get("notes") or [])]

    # The same customer's other open visits, so a person who messaged twice and
    # then walked in is read as one conversation rather than three strangers.
    data["other_visits"] = frappe.get_list(
        "POS Kiosk Token",
        filters={"customer_phone": doc.customer_phone, "name": ("!=", doc.name),
                 "status": ("in", OPEN_STATUSES)},
        fields=["name", "visit_source", "status", "creation", "issue_description"],
        order_by="creation desc", limit_page_length=5)

    data["repairs"] = frappe.get_list(
        "Service Request",
        filters={"contact_number": doc.customer_phone, "docstatus": ("<", 2)},
        fields=["name", "decision", "device_model", "qc_status",
                "delivered_datetime", "creation"],
        order_by="creation desc", limit_page_length=5)

    symptoms = [r.symptom_name for r in (doc.get("symptoms") or [])
                if r.get("symptom_name")]
    data["symptom_labels"] = symptoms
    return data


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


@frappe.whitelist(methods=["POST"])
def extend_follow_up(inbox, follow_up_on, note=None) -> dict:
    """Push the date this request has to be decided on.

    An expected close date that quietly slips is worse than none at all, so
    moving it is an action with a note against it rather than an edit.
    """
    doc = frappe.get_doc("POS Kiosk Token", inbox)
    doc.check_permission("write")

    when = get_datetime(follow_up_on)
    if when <= now_datetime():
        frappe.throw(_("Pick a date in the future — otherwise it is still overdue."),
                     title=_("Date Has Passed"))

    doc.flags.ignore_validate_update_after_submit = True
    doc.expires_at = when
    if doc.status not in OPEN_STATUSES:
        doc.status = "Hold"
    doc.append("notes", {
        "note_datetime": now_datetime(), "channel": doc.visit_source,
        "noted_by": frappe.session.user,
        "note": _("Follow-up moved to {0}.{1}").format(
            frappe.utils.format_datetime(when), f" {note}" if note else "")})
    doc.flags.ignore_permissions = True
    doc.flags.ignore_mandatory = True
    doc.save()
    return {"ok": True, "follow_up_on": str(when)}


@frappe.whitelist()
def overdue_requests(company=None, pos_profile=None) -> list:
    """Written requests whose agreed date has passed and nobody has acted.

    Deliberately its own call: these are the ones a desk must decide about --
    chase again, or close as withdrawn -- and burying them in the queue is how
    a request sits for a fortnight with nobody accountable.
    """
    filters = {
        "visit_source": ("not in", IN_PERSON_CHANNELS),
        "status": ("in", OPEN_STATUSES),
        "expires_at": ("<", now_datetime()),
    }
    if company:
        filters["company"] = company
    if pos_profile:
        filters["pos_profile"] = pos_profile
    return frappe.get_list(
        "POS Kiosk Token", filters=filters,
        fields=["name", "visit_source", "customer_name", "customer_phone",
                "expires_at", "issue_description", "status"],
        order_by="expires_at asc", limit_page_length=100)


@frappe.whitelist(methods=["POST"])
def assign_store(inbox, pos_profile, note=None) -> dict:
    """Route an unassigned request to the store that will handle it."""
    doc = frappe.get_doc("POS Kiosk Token", inbox)
    doc.check_permission("write")

    profile = frappe.db.get_value("POS Profile", pos_profile,
                                  ["name", "company", "warehouse", "disabled"], as_dict=True)
    if not profile:
        frappe.throw(_("{0} is not a store.").format(pos_profile))
    if profile.company != doc.company:
        frappe.throw(_("{0} belongs to another company.").format(pos_profile),
                     frappe.PermissionError, title=_("Wrong Company"))
    # A disabled store cannot be opened, so routing here would strand the
    # request where no till can ever see it -- which is exactly what happened
    # to two of them.
    if profile.disabled:
        frappe.throw(
            _("{0} is disabled — nobody can open a till there, so the request "
              "would be stranded. Pick a store that is trading.").format(pos_profile),
            title=_("Store Is Closed"))

    doc.flags.ignore_validate_update_after_submit = True
    doc.pos_profile = profile.name
    doc.store = profile.warehouse
    doc.append("notes", {
        "note_datetime": now_datetime(), "channel": doc.visit_source,
        "noted_by": frappe.session.user,
        "note": _("Routed to {0}.{1}").format(profile.name, f" {note}" if note else "")})
    doc.flags.ignore_permissions = True
    doc.flags.ignore_mandatory = True
    doc.save()
    return {"ok": True, "pos_profile": profile.name}


@frappe.whitelist()
def unassigned_requests(company=None) -> list:
    """Requests that arrived without a store, waiting to be routed.

    Their own list on purpose. Showing them at every store is what made the
    same customer appear four times.
    """
    filters = {
        "visit_source": ("not in", IN_PERSON_CHANNELS),
        "status": ("in", OPEN_STATUSES),
        "pos_profile": ("in", ["", None]),
    }
    if company:
        filters["company"] = company
    return frappe.get_list(
        "POS Kiosk Token", filters=filters,
        fields=["name", "visit_source", "customer_name", "customer_phone",
                "city", "issue_description", "creation", "status", "company"],
        order_by="creation desc", limit_page_length=100)


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
