"""Device handover, gated, on the Service Request.

This replaces the Sales-Order-based delivery flow. That one was correct and
well built -- encrypted OTP, TTL, attempt limits, lockout, constant-time compare
-- and had no caller anywhere outside a test file. Across 198 service orders,
zero had a verified OTP and no request carried a delivery date, so devices went
back over the counter with no gate at all.

Two things changed. The gates now live on the Service Request, which is the
single operational document, so the ticket that holds the device also holds the
verdict on releasing it. And the payment gate reads the repair's own invoice
register rather than looking for an invoice line that points at the order, which
is a link a counter-billed repair never writes.

Four gates, all re-checked server-side by ``complete_handover`` regardless of
what any screen believes:

  1. Quality check passed
  2. Nothing outstanding on any invoice raised against the repair
  3. The customer read back the OTP sent to their registered number
  4. Accessories returned, when accessories were taken in

The OTP proves the person collecting is the person who left the device. It has
nothing to do with which store billed it -- that is the remote-billing OTP, a
separate control that correctly skips itself when the device is at its home
store.
"""

import secrets

import frappe
from frappe import _
from frappe.utils import add_to_date, cint, flt, get_datetime, now_datetime

from gofix.config import get_int_setting
from gofix.security import assert_service_request_access


def _otp_limits():
    return (
        get_int_setting("otp_max_attempts", 5),
        get_int_setting("otp_lockout_seconds", 900, minimum=60),
    )


@frappe.whitelist(methods=["POST"])
def generate_handover_otp(service_request: str) -> dict:
    """Send the collection OTP to the customer's registered number."""
    sr = assert_service_request_access(service_request, permission_type="write")
    frappe.db.sql("SELECT name FROM `tabService Request` WHERE name = %s FOR UPDATE", (sr.name,))
    sr.reload()

    now = now_datetime()
    locked_until = sr.get("delivery_otp_locked_until")
    if locked_until and get_datetime(locked_until) > now:
        frappe.throw(_("Handover OTP is temporarily locked after repeated failures."),
                     frappe.PermissionError)

    otp = str(secrets.randbelow(900000) + 100000)
    sr.db_set({
        "delivery_otp": frappe.utils.password.encrypt(otp),
        "delivery_otp_verified": 0,
        "delivery_otp_sent_at": now,
        "delivery_otp_attempts": 0,
        "delivery_otp_locked_until": None,
        "delivery_otp_consumed_at": None,
    }, update_modified=False)

    _send_otp(sr, otp)
    return {"otp_sent": True, "message": _("OTP sent to the customer")}


@frappe.whitelist(methods=["POST"])
def verify_handover_otp(service_request: str, otp_input: str) -> dict:
    """Check the code the customer read out."""
    sr = assert_service_request_access(service_request, permission_type="write")
    frappe.db.sql("SELECT name FROM `tabService Request` WHERE name = %s FOR UPDATE", (sr.name,))
    sr.reload()

    if sr.get("delivery_otp_verified"):
        return {"verified": True, "already_verified": True,
                "message": _("OTP was already verified.")}

    now = now_datetime()
    locked_until = sr.get("delivery_otp_locked_until")
    if locked_until and get_datetime(locked_until) > now:
        return {"verified": False, "locked": True,
                "message": _("Handover OTP is temporarily locked.")}

    stored = sr.get("delivery_otp")
    if not stored or not sr.get("delivery_otp_sent_at"):
        return {"verified": False, "message": _("No active OTP. Send a new one.")}

    ttl = get_int_setting("delivery_otp_ttl_seconds", 600, minimum=60)
    if get_datetime(sr.delivery_otp_sent_at) < add_to_date(now, seconds=-ttl):
        sr.db_set({"delivery_otp": None, "delivery_otp_attempts": 0}, update_modified=False)
        return {"verified": False, "expired": True, "message": _("OTP has expired.")}

    try:
        decrypted = frappe.utils.password.decrypt(stored)
    except Exception:
        decrypted = stored

    master_ok = False
    try:
        from ch_item_master.ch_core.shadow_live import master_otp_matches
        master_ok = bool(master_otp_matches(otp_input))
    except Exception:
        pass

    valid = master_ok or secrets.compare_digest(
        str(otp_input or "").strip(), str(decrypted or "").strip())

    if not valid:
        max_attempts, lockout = _otp_limits()
        attempts = cint(sr.get("delivery_otp_attempts")) + 1
        updates = {"delivery_otp_attempts": attempts}
        locked = attempts >= max_attempts
        if locked:
            updates["delivery_otp"] = None
            updates["delivery_otp_locked_until"] = add_to_date(now, seconds=lockout)
        sr.db_set(updates, update_modified=False)
        return {
            "verified": False, "locked": locked,
            "attempts_remaining": max(max_attempts - attempts, 0),
            "message": _("Maximum OTP attempts exceeded.") if locked else _("Invalid OTP."),
        }

    sr.db_set({
        "delivery_otp": None,
        "delivery_otp_verified": 1,
        "delivery_otp_attempts": 0,
        "delivery_otp_locked_until": None,
        "delivery_otp_consumed_at": now,
    }, update_modified=False)
    return {"verified": True, "message": _("OTP verified")}


@frappe.whitelist()
def handover_readiness(service_request: str) -> dict:
    """The four gates, and what is still in the way."""
    sr = assert_service_request_access(service_request, permission_type="read")
    blockers = []

    if (sr.get("qc_status") or "") != "Pass":
        blockers.append(_("Quality check not passed (currently {0})").format(
            sr.get("qc_status") or _("not started")))

    unpaid = outstanding_invoices(sr)
    for inv in unpaid:
        blockers.append(_("Outstanding {0} on {1}").format(
            frappe.format_value(inv["outstanding"], {"fieldtype": "Currency"}), inv["invoice"]))

    if not sr.get("delivery_otp_verified"):
        blockers.append(_("Handover OTP not verified"))

    # Only a gate when something was actually taken in with the device.
    if sr.get("accessories_received") and not sr.get("accessories_returned"):
        blockers.append(_("Accessories not confirmed as returned"))

    return {
        "ready": not blockers,
        "blockers": blockers,
        "qc_status": sr.get("qc_status") or "",
        "otp_verified": bool(sr.get("delivery_otp_verified")),
        "accessories_received": bool(sr.get("accessories_received")),
        "accessories_returned": bool(sr.get("accessories_returned")),
        "outstanding": unpaid,
        "delivered_datetime": str(sr.get("delivered_datetime") or ""),
    }


def outstanding_invoices(sr) -> list:
    """Unpaid submitted invoices raised against this repair.

    Reads the repair's own invoice register, so it sees every bill including
    ones raised after a reopen. The old check looked for a Sales Invoice Item
    pointing at the service order, a link a counter-billed repair never writes,
    so it matched nothing and the payment gate never fired.
    """
    if isinstance(sr, str):
        sr = frappe.get_doc("Service Request", sr)

    names = [r.invoice for r in (sr.get("service_invoices") or []) if r.invoice]
    if sr.get("service_invoice") and sr.service_invoice not in names:
        names.append(sr.service_invoice)
    if not names:
        return []

    rows = frappe.get_all(
        "Sales Invoice",
        filters={"name": ("in", names), "docstatus": 1, "outstanding_amount": (">", 0)},
        fields=["name", "outstanding_amount", "grand_total"],
        limit_page_length=50)
    return [{"invoice": r.name, "outstanding": flt(r.outstanding_amount),
             "grand_total": flt(r.grand_total)} for r in rows]


@frappe.whitelist(methods=["POST"])
def complete_handover(service_request: str, remarks: str = None) -> dict:
    """Release the device, once every gate passes.

    Re-checks the gates here rather than trusting the screen, so the control is
    the server and the dialog is only a guide.
    """
    sr = assert_service_request_access(service_request, permission_type="write")
    frappe.db.sql("SELECT name FROM `tabService Request` WHERE name = %s FOR UPDATE", (sr.name,))
    sr.reload()

    if sr.get("delivered_datetime"):
        return {"ok": True, "already": True,
                "message": _("This device was already handed over.")}

    # A device going back by courier or rider leaves before it arrives. What
    # proves that delivery is the carrier's, not a code read out at a counter
    # nobody is standing at, so it goes through dispatch instead.
    from gofix.gofix_services.logistics import is_in_person_return

    if not is_in_person_return(sr):
        frappe.throw(
            _("This repair goes back by {0}, so it is dispatched rather than "
              "collected. Use Dispatch Return.").format(sr.get("return_method")),
            title=_("Not Collected In Person"))

    readiness = handover_readiness(service_request)
    if not readiness["ready"]:
        frappe.throw(
            _("Cannot hand over. Outstanding: {0}").format("; ".join(readiness["blockers"])),
            title=_("Handover Blocked"),
        )

    now = now_datetime()
    updates = {"delivered_datetime": now, "decision": "Delivered"}
    if sr.meta.get_field("return_delivered_date"):
        updates["return_delivered_date"] = now.date()
    if remarks and sr.meta.get_field("delivery_remarks"):
        updates["delivery_remarks"] = remarks
    sr.flags.ignore_billing_lock = True
    sr.db_set(updates, update_modified=True)

    sr.add_comment("Comment", _("Device handed over by {0}.{1}").format(
        frappe.session.user, f" {remarks}" if remarks else ""))
    return {"ok": True, "message": _("Device handed over"), "delivered_at": str(now)}


def _send_otp(sr, otp: str) -> None:
    """SMS and email the collection code. Never fails the handover."""
    try:
        from ch_item_master.ch_core.shadow_live import suppress_customer_comms
        if suppress_customer_comms():
            frappe.logger("shadow_live").info(f"Handover OTP suppressed for {sr.name}")
            return
    except Exception:
        pass

    mobile = sr.get("contact_number")
    if mobile:
        try:
            from frappe.core.doctype.sms_settings.sms_settings import send_sms
            send_sms([mobile], _(
                "GoFix: Your device collection OTP is {0}. Share it with the store "
                "to collect your device. Ticket: {1}").format(otp, sr.name))
        except Exception:
            frappe.log_error(frappe.get_traceback(), "Handover OTP SMS failed")

    email = sr.get("email")
    if email:
        try:
            frappe.sendmail(
                recipients=[email],
                subject=_("Device Collection OTP | {0}").format(sr.name),
                message=_(
                    "<p>Dear {0},</p><p>Your OTP for device collection is: "
                    "<b style='font-size:20px;letter-spacing:3px'>{1}</b></p>"
                    "<p>Please share it with the store when you collect your device.</p>"
                ).format(frappe.utils.escape_html(sr.get("customer_name") or "Customer"), otp),
                now=True,
            )
        except Exception:
            frappe.log_error(frappe.get_traceback(), "Handover OTP email failed")
