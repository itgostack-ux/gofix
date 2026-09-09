"""Getting the device back to its owner.

A repair shop is asked one question all day: is the customer coming for it, or
are we sending it? The system could not answer. Everything recorded was about
the device arriving -- a pickup agent, an inbound mode -- and nothing about the
journey home, so a repaired phone sat on a shelf while whoever answered the
phone guessed.

Three things make that answerable here.

**The method carries its own rules.** ``Device Logistics Method`` says whether a
mode needs a partner, a tracking number, an address or a slot, so one form
serves a customer collecting at the counter and a waybill going out by courier
without asking either of them for the other's details.

**It is agreed before the customer pays.** Billing is the last moment everyone
is still talking, so it is the right place to settle who is collecting. After
the invoice the customer has gone and the question becomes a phone call.

**Dispatch and handover are different acts.** A customer collecting in person
reads back an OTP at the counter. A courier return is a dispatch: the device
leaves before it arrives, and what proves delivery is the waybill, not somebody
standing in front of you. Treating them as one act is how a device gets marked
delivered while it is still in a van.
"""

import frappe
from frappe import _
from frappe.utils import now_datetime


def method(name):
    """The rules attached to a logistics method."""
    if not name:
        return None
    return frappe.db.get_value(
        "Device Logistics Method", name,
        ["name", "direction", "customer_present", "requires_partner",
         "requires_tracking", "requires_address", "requires_schedule", "disabled"],
        as_dict=True)


def is_in_person_return(sr) -> bool:
    """True when the customer is coming to collect it themselves.

    Anything else leaves the shop without them, which changes what proves it
    was handed over.
    """
    if isinstance(sr, str):
        sr = frappe.db.get_value("Service Request", sr, "return_method")
        cfg = method(sr)
    else:
        cfg = method(sr.get("return_method"))
    # Unset is treated as in person: it is the common case and the safe one,
    # because it demands the OTP rather than skipping it.
    return True if not cfg else bool(cfg.customer_present)


# ── What each method needs ───────────────────────────────────────────────────

def validate_logistics(doc, method_name=None):
    """Ask for exactly what the chosen method involves, and nothing else."""
    _check_leg(doc, "intake_method", "intake_partner", "intake_tracking_number",
               "pickup_address", "pickup_scheduled_datetime", _("Arrived By"),
               allowed_direction=("Both", "Inbound"))
    _check_leg(doc, "return_method", "return_partner", "return_tracking_number",
               "return_address", "return_scheduled_datetime", _("Goes Back By"),
               allowed_direction=("Both", "Outbound"))


def _check_leg(doc, method_field, partner_field, tracking_field, address_field,
               schedule_field, label, allowed_direction):
    cfg = method(doc.get(method_field))
    if not cfg:
        return
    if cfg.disabled:
        frappe.throw(_("{0} is no longer offered.").format(cfg.name),
                     title=_("Method Withdrawn"))
    if cfg.direction not in allowed_direction:
        frappe.throw(
            _("{0} cannot be used for {1} — it is {2} only.").format(
                cfg.name, label, cfg.direction.lower()),
            title=_("Wrong Direction"))

    missing = []
    if cfg.requires_partner and not doc.get(partner_field):
        missing.append(_("who is carrying it"))
    if cfg.requires_address and not doc.get(address_field):
        missing.append(_("an address"))
    if cfg.requires_schedule and not doc.get(schedule_field):
        missing.append(_("a slot"))
    # A tracking number is not demanded up front: it exists only once the
    # consignment is actually booked, which is at dispatch, not at intake.
    if missing:
        frappe.throw(
            _("{0} is set to {1}, which needs {2}.").format(
                label, cfg.name, ", ".join(missing)),
            title=_("Movement Details Missing"))


# ── The gate before money changes hands ──────────────────────────────────────

def assert_return_agreed(sr):
    """Refuse to bill a repair nobody has agreed how to return.

    This is the last moment the customer is still in the conversation. After
    the invoice they have gone, and "who is collecting this?" becomes a phone
    call somebody has to make.
    """
    if not frappe.db.exists("DocType", "Device Logistics Method"):
        return
    if sr.get("return_method"):
        return
    frappe.throw(
        _("Agree how the device goes back before billing. Set <b>Goes Back By</b> "
          "— the customer collecting, our rider, a hyperlocal partner or a "
          "courier — so nobody has to chase it afterwards."),
        title=_("How Does It Go Back?"))


@frappe.whitelist(methods=["POST"])
def confirm_return(service_request, return_method, return_partner=None,
                   return_address=None, return_scheduled_datetime=None,
                   tracking_number=None) -> dict:
    """Record what was agreed with the customer about getting it back."""
    from gofix.security import assert_service_request_access

    sr = assert_service_request_access(service_request, permission_type="write")
    cfg = method(return_method)
    if not cfg:
        frappe.throw(_("{0} is not a movement method.").format(return_method))
    if cfg.direction not in ("Both", "Outbound"):
        frappe.throw(_("{0} cannot be used for a return.").format(cfg.name))

    updates = {
        "return_method": cfg.name,
        "return_partner": return_partner or None,
        "return_address": return_address or sr.get("return_address"),
        "return_scheduled_datetime": return_scheduled_datetime
                                     or sr.get("return_scheduled_datetime"),
        "return_confirmed_by": frappe.session.user,
        "return_confirmed_at": now_datetime(),
    }
    if tracking_number:
        updates["return_tracking_number"] = tracking_number

    missing = []
    if cfg.requires_partner and not updates["return_partner"]:
        missing.append(_("who is carrying it"))
    if cfg.requires_address and not updates["return_address"]:
        missing.append(_("an address"))
    if cfg.requires_schedule and not updates["return_scheduled_datetime"]:
        missing.append(_("a slot"))
    if missing:
        frappe.throw(_("{0} needs {1}.").format(cfg.name, ", ".join(missing)),
                     title=_("Movement Details Missing"))

    sr.flags.ignore_billing_lock = True
    sr.db_set(updates, update_modified=True)
    sr.add_comment("Comment", _("Return agreed: {0}{1}.").format(
        cfg.name, f" via {updates['return_partner']}" if updates["return_partner"] else ""))
    return {"ok": True, "return_method": cfg.name,
            "in_person": bool(cfg.customer_present),
            "message": _("Return agreed: {0}").format(cfg.name)}


@frappe.whitelist(methods=["POST"])
def dispatch_return(service_request, tracking_number=None, remarks=None) -> dict:
    """Send the device back by whatever was agreed, when it is not collected.

    Separate from handover on purpose. A dispatch is the device leaving; the
    handover it eventually gets is the courier's proof of delivery, not ours.
    Marking it delivered here would say a customer had it while it was still
    in a van.
    """
    from gofix.gofix_services.handover import handover_readiness
    from gofix.security import assert_service_request_access

    sr = assert_service_request_access(service_request, permission_type="write")
    cfg = method(sr.get("return_method"))
    if not cfg:
        frappe.throw(_("Agree how the device goes back first."),
                     title=_("No Return Method"))
    if cfg.customer_present:
        frappe.throw(
            _("{0} means the customer collects it here. Use Hand Over To "
              "Customer so the collection OTP is checked.").format(cfg.name),
            title=_("Collected In Person"))

    # The money and quality gates hold whichever way it travels. Only the OTP
    # is particular to somebody standing at the counter.
    readiness = handover_readiness(service_request)
    blockers = [b for b in readiness["blockers"] if "OTP" not in b]
    if blockers:
        frappe.throw(_("Cannot dispatch. Outstanding: {0}").format("; ".join(blockers)),
                     title=_("Dispatch Blocked"))

    if cfg.requires_tracking and not (tracking_number or sr.get("return_tracking_number")):
        frappe.throw(_("{0} needs a tracking or task number before it goes.").format(cfg.name),
                     title=_("Tracking Number Missing"))

    now = now_datetime()
    updates = {"return_dispatched_date": now.date()}
    if tracking_number:
        updates["return_tracking_number"] = tracking_number
    if remarks and sr.meta.get_field("delivery_remarks"):
        updates["delivery_remarks"] = remarks
    if sr.meta.get_field("transfer_status"):
        updates["transfer_status"] = "Dispatched"

    sr.flags.ignore_billing_lock = True
    sr.db_set(updates, update_modified=True)
    sr.add_comment("Comment", _("Dispatched by {0}{1}.{2}").format(
        cfg.name,
        f" ({sr.get('return_partner')})" if sr.get("return_partner") else "",
        f" {tracking_number or sr.get('return_tracking_number') or ''}"))

    return {"ok": True, "dispatched_on": str(now.date()),
            "tracking_number": tracking_number or sr.get("return_tracking_number"),
            "tracking_url": tracking_url(sr.get("return_partner"),
                                         tracking_number or sr.get("return_tracking_number")),
            "message": _("Dispatched by {0}").format(cfg.name)}


def tracking_url(partner, number) -> str:
    """The carrier's own tracking page, when it gave us a template."""
    if not partner or not number:
        return ""
    template = frappe.db.get_value("Courier Partner", partner, "tracking_url_template")
    if not template:
        return ""
    return template.replace("{tracking_number}", str(number).strip())


@frappe.whitelist()
def movement_options(direction=None) -> dict:
    """The ways a device can reach us and go home, with their own rules.

    The counter form needs to know not just which methods exist but what each
    one demands, so it can ask a courier for a waybill and a customer for
    nothing. Sending the flags with the list keeps that decision in the master
    where ops can change it, instead of in a switch statement in the browser.
    """
    filters = {"disabled": 0}
    methods = frappe.get_all(
        "Device Logistics Method", filters=filters,
        fields=["name", "direction", "customer_present", "requires_partner",
                "requires_tracking", "requires_address", "requires_schedule"],
        order_by="customer_present desc, name")
    if direction:
        # "Both" serves either leg; a one-way method only shows on its own leg.
        methods = [m for m in methods
                   if not m.direction or m.direction in ("Both", direction)]
    return {
        "methods": methods,
        # Courier Partner marks availability with is_active, not disabled.
        "partners": frappe.get_all("Courier Partner", filters={"is_active": 1},
                                   fields=["name", "partner_name", "partner_type",
                                           "tracking_url_template"],
                                   order_by="partner_name"),
    }
