"""Who is this, and what are they already waiting on?

The problem
-----------
A phone number is not a person. The same customer reaches us on a personal
number and a work number; a household shares one number between two people; a
number changes hands. Keying work on the number itself gets all three wrong,
and on this system both failures are already real: no contact has a second
number recorded, and several numbers resolve to two different customers.

So the number is treated as a *contact point* and the Customer as the identity
-- which is how every service desk of consequence models it. Salesforce and
Dynamics attach cases to a Contact that carries several phone fields; Zendesk
gives a user many identities in their own table and hangs tickets off the user;
SAP holds N communication records against a business partner. ERPNext already
ships the same shape: Contact, a Contact Phone child table that takes any
number of numbers, and a Dynamic Link to the Customer. Nothing new is needed --
the capability was simply never used.

Two rules follow, and they matter more than the plumbing:

**Resolve, never guess.** One number can legitimately produce two customers.
Picking one would attach a stranger's repair history to whoever is standing at
the counter, so an ambiguous number returns both and a person chooses.

**Merge where a human can confirm it.** A second number becomes part of an
identity at the counter, when someone who can see the customer says so -- not
by a background job matching names. A wrong merge is very hard to unpick.
"""

import frappe
from frappe import _
from frappe.utils import get_datetime, now_datetime

from gofix.gofix_services.doctype.gofix_service_inbox.gofix_service_inbox import (
    normalise_phone)

OPEN_INBOX = ("New", "Contacted", "Scheduled")


# ── Resolution ───────────────────────────────────────────────────────────────

def customers_for_phone(phone: str) -> list:
    """Every customer this number could belong to, with why.

    Two sources: the Customer's own mobile_no, and any Contact Phone on a
    Contact linked to a Customer. Returns a list because the honest answer is
    sometimes more than one.
    """
    number = normalise_phone(phone)
    if len(number) < 10:
        return []

    found = {}

    for row in frappe.get_all(
            "Customer", filters={"mobile_no": ("like", f"%{number}")},
            fields=["name", "customer_name", "mobile_no"], limit_page_length=20):
        if normalise_phone(row.mobile_no) == number:
            found[row.name] = {"customer": row.name, "customer_name": row.customer_name,
                               "matched_on": "mobile_no"}

    # Contact Phone is the multi-number store. A contact reaches its customer
    # through Dynamic Link, which is how ERPNext models the association.
    linked = frappe.db.sql("""
        SELECT dl.link_name AS customer, c.name AS contact
        FROM `tabContact Phone` cp
        JOIN `tabContact` c ON c.name = cp.parent
        JOIN `tabDynamic Link` dl
          ON dl.parent = c.name AND dl.parenttype = 'Contact'
         AND dl.link_doctype = 'Customer'
        WHERE cp.phone LIKE %(like)s
    """, {"like": f"%{number}"}, as_dict=True)
    for row in linked:
        if not row.customer:
            continue
        found.setdefault(row.customer, {
            "customer": row.customer,
            "customer_name": frappe.db.get_value("Customer", row.customer, "customer_name"),
            "matched_on": "contact",
        })

    return list(found.values())


def numbers_for_customer(customer: str) -> list:
    """Every number we can reach this customer on."""
    if not customer:
        return []
    numbers = []

    primary = frappe.db.get_value("Customer", customer, "mobile_no")
    if primary:
        numbers.append(normalise_phone(primary))

    rows = frappe.db.sql("""
        SELECT cp.phone
        FROM `tabContact Phone` cp
        JOIN `tabDynamic Link` dl
          ON dl.parent = cp.parent AND dl.parenttype = 'Contact'
         AND dl.link_doctype = 'Customer' AND dl.link_name = %(c)s
    """, {"c": customer}, as_dict=True)
    numbers.extend(normalise_phone(r.phone) for r in rows)

    return [n for n in dict.fromkeys(numbers) if len(n) >= 10]


def resolve(phone: str, company: str = None) -> dict:
    """Everything known about whoever is on this number.

    Widens from the number to the identity: once a customer is resolved, open
    work is gathered across *all* their numbers, so a person who wrote in from
    their work phone and walked in on their personal one is one customer with
    one history, not two strangers.
    """
    number = normalise_phone(phone)
    blank = {"phone": number, "customers": [], "customer": None, "ambiguous": False,
             "numbers": [number] if number else [], "requests": [], "repairs": [],
             "token": None, "latest_contact": None, "known": False}
    if len(number) < 10:
        return blank

    candidates = customers_for_phone(number)
    ambiguous = len(candidates) > 1
    customer = candidates[0]["customer"] if len(candidates) == 1 else None

    # Search every number of a confidently-resolved identity. An ambiguous one
    # stays on the number that was actually typed -- widening on a guess would
    # show one customer another's repairs.
    numbers = numbers_for_customer(customer) if customer else []
    if number not in numbers:
        numbers.append(number)

    inbox_filters = {"contact_number": ("in", numbers), "status": ("in", OPEN_INBOX)}
    if company:
        inbox_filters["company"] = company
    requests = frappe.get_list(
        "GoFix Service Inbox", filters=inbox_filters,
        fields=["name", "channel", "status", "received_at", "customer_name",
                "contact_number", "device_category", "device_brand", "device_model",
                "device_item", "serial_no", "issue_category", "issue_description",
                "preferred_store", "preferred_datetime", "referral_source", "email"],
        order_by="received_at desc", limit_page_length=20)

    sr_filters = {"contact_number": ("in", numbers), "docstatus": ("<", 2)}
    if company:
        sr_filters["company"] = company
    repairs = frappe.get_list(
        "Service Request", filters=sr_filters,
        fields=["name", "decision", "device_model", "issue_category", "qc_status",
                "delivered_datetime", "service_invoice", "creation"],
        order_by="creation desc", limit_page_length=10)

    token = _waiting_token(numbers)

    return {
        "phone": number,
        "customers": candidates,
        "customer": customer,
        "customer_name": candidates[0]["customer_name"] if len(candidates) == 1 else None,
        "ambiguous": ambiguous,
        "numbers": numbers,
        "requests": requests,
        "repairs": repairs,
        "token": token,
        "latest_contact": _latest_contact(requests, token),
        "known": bool(candidates or requests or repairs or token),
    }


def _waiting_token(numbers) -> dict:
    """A walk-in token still waiting on any of these numbers."""
    if not numbers:
        return None
    try:
        rows = frappe.get_all(
            "POS Kiosk Token",
            filters={"customer_phone": ("in", numbers),
                     "status": ("in", ["Waiting", "Hold", "Engaged"])},
            fields=["name", "token_display", "customer_name", "customer_phone",
                    "visit_reason", "issue_description", "creation", "status",
                    "linked_customer"],
            order_by="creation desc", limit_page_length=1)
        return rows[0] if rows else None
    except Exception:
        # The queue is a convenience here; it must never fail a lookup.
        return None


def _latest_contact(requests, token) -> dict:
    """The most recent way this person reached us.

    When both a written request and a walk-in exist, the request wins on equal
    footing: it carries what the customer actually described, whereas a token
    carries only why they came in. That is the record worth attaching the
    ticket to.
    """
    newest_request = requests[0] if requests else None
    if newest_request:
        return {"kind": "inbox", "name": newest_request["name"],
                "at": str(newest_request["received_at"]),
                "channel": newest_request["channel"]}
    if token:
        return {"kind": "token", "name": token["name"],
                "at": str(token["creation"]), "channel": "Walk-in"}
    return None


# ── Consolidation ────────────────────────────────────────────────────────────

@frappe.whitelist(methods=["POST"])
def consolidate_into_request(service_request, phone=None, inbox=None, token=None) -> dict:
    """Attach everything this person is waiting on to the ticket we just made.

    One device, one ticket. A customer who messaged on Tuesday, rang on
    Wednesday and walked in on Thursday should not leave three open items
    behind -- so the newest written request becomes the ticket's origin and
    every other open request on that identity is closed against it, with the
    walk-in token released.
    """
    sr = frappe.get_doc("Service Request", service_request)
    sr.check_permission("write")

    number = normalise_phone(phone or sr.get("contact_number"))
    found = resolve(number, company=sr.get("company"))

    primary = inbox
    if not primary and found["latest_contact"] and found["latest_contact"]["kind"] == "inbox":
        primary = found["latest_contact"]["name"]

    linked, superseded = None, []
    if primary:
        from gofix.gofix_services.inbox import link_to_service_request

        result = link_to_service_request(primary, sr.name)
        linked = primary
        superseded = result.get("superseded_names") or []

    # Anything else still open on this identity is the same conversation.
    others = [r["name"] for r in found["requests"] if r["name"] != primary]
    for name in others:
        if frappe.db.get_value("GoFix Service Inbox", name, "status") in OPEN_INBOX:
            frappe.db.set_value("GoFix Service Inbox", name, {
                "status": "Duplicate", "duplicate_of": primary or None,
                "closed_reason": _("Booked in as {0}").format(sr.name),
            }, update_modified=False)
            superseded.append(name)

    closed_token = _release_token(token or (found["token"] or {}).get("name"), sr)

    return {
        "ok": True,
        "linked": linked,
        "superseded": sorted(set(superseded)),
        "token_closed": closed_token,
        "numbers_searched": found["numbers"],
        "message": _("{0} linked, {1} other request(s) closed").format(
            linked or _("no request"), len(set(superseded))),
    }


def _release_token(token_name, sr) -> str:
    """Close the walk-in token, if the intake has not already done it."""
    if not token_name:
        return None
    try:
        from ch_pos.ch_pos.api.token_api import link_token_to_service_request

        link_token_to_service_request(token_name, sr.name)
        return token_name
    except Exception:
        frappe.log_error(frappe.get_traceback(),
                         f"identity: could not release token {token_name} for {sr.name}")
        return None


# ── The confirmed merge ──────────────────────────────────────────────────────

@frappe.whitelist(methods=["POST"])
def remember_number(customer, phone, label=None) -> dict:
    """Record a second number against a customer, so it resolves next time.

    Called from the counter when someone who can see the customer confirms the
    number is theirs. Deliberately not automatic: matching on a name would
    merge two strangers who share one, and that is very hard to undo.

    Written to Contact Phone, the field ERPNext already provides for this and
    which nothing on this system had yet used.
    """
    number = normalise_phone(phone)
    if len(number) < 10:
        frappe.throw(_("That does not look like a phone number."),
                     title=_("Cannot Remember It"))
    if not frappe.db.exists("Customer", customer):
        frappe.throw(_("Customer {0} does not exist.").format(customer))

    frappe.has_permission("Customer", "write", throw=True)

    if number in numbers_for_customer(customer):
        return {"ok": True, "already": True,
                "message": _("We already have that number for this customer.")}

    contact = _primary_contact(customer)
    contact.append("phone_nos", {"phone": number})
    contact.flags.ignore_permissions = True
    contact.save()

    return {"ok": True, "contact": contact.name,
            "numbers": numbers_for_customer(customer),
            "message": _("{0} will now be recognised as this customer.").format(number)}


def _primary_contact(customer):
    """The customer's contact, created on first use.

    Most customers here have a number on the Customer record and no Contact at
    all, so the second number has nowhere to live until one exists.
    """
    name = frappe.db.sql("""
        SELECT dl.parent FROM `tabDynamic Link` dl
        WHERE dl.parenttype = 'Contact' AND dl.link_doctype = 'Customer'
          AND dl.link_name = %(c)s LIMIT 1
    """, {"c": customer})
    if name:
        return frappe.get_doc("Contact", name[0][0])

    customer_name = frappe.db.get_value("Customer", customer, "customer_name") or customer
    contact = frappe.new_doc("Contact")
    contact.first_name = customer_name[:140]
    contact.append("links", {"link_doctype": "Customer", "link_name": customer})

    primary = frappe.db.get_value("Customer", customer, "mobile_no")
    if primary:
        contact.append("phone_nos", {"phone": normalise_phone(primary),
                                     "is_primary_mobile_no": 1})
    contact.flags.ignore_permissions = True
    contact.insert()
    return contact
