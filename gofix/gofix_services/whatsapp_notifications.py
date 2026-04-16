"""GoFix → WhatsApp notifications on Service Request status changes."""

import frappe


def on_service_request_update(doc, method):
    """Hook: Service Request.on_update — send WhatsApp on key status transitions."""
    if frappe.flags.in_import or frappe.flags.in_migrate:
        return

    old = doc.get_doc_before_save()
    if not old:
        return

    old_decision = old.decision
    new_decision = doc.decision
    if old_decision == new_decision:
        return

    phone = doc.contact_number
    if not phone:
        return

    customer_name = doc.customer_name or "Customer"

    if new_decision == "Accepted":
        _notify_device_received(doc, phone, customer_name)
        # Also send tracking link
        try:
            notify_tracking_link(doc.name)
        except Exception:
            pass
    elif new_decision == "Completed":
        _notify_repair_completed(doc, phone, customer_name)
    elif new_decision == "Delivered":
        _notify_ready_for_delivery(doc, phone, customer_name)
    elif new_decision == "Rejected":
        _notify_not_repairable(doc, phone, customer_name)


def notify_sla_breach(service_request_name: str):
    """Called from SLA breach checker to apologise for delay."""
    doc = frappe.get_doc("Service Request", service_request_name)
    phone = doc.contact_number
    if not phone:
        return

    settings = _get_settings()
    if not settings:
        return

    from ch_item_master.ch_core.whatsapp import send_template_message

    send_template_message(
        phone=phone,
        template_name=settings.gofix_sla_breach,
        body_values={
            "1": doc.customer_name or "Customer",
            "2": doc.name,
            "3": doc.device_item_name or "",
        },
        customer_name=doc.customer_name,
        ref_doctype="Service Request",
        ref_name=doc.name,
    )


# ── Private helpers ──────────────────────────────────────────────────

def _get_settings():
    try:
        s = frappe.get_cached_doc("CH WhatsApp Settings")
        return s if s.enabled else None
    except frappe.DoesNotExistError:
        return None


def _notify_device_received(doc, phone, customer_name):
    settings = _get_settings()
    if not settings:
        return

    from ch_item_master.ch_core.whatsapp import send_template_message

    send_template_message(
        phone=phone,
        template_name=settings.gofix_device_received,
        body_values={
            "1": customer_name,
            "2": doc.name,
            "3": doc.device_item_name or "",
            "4": str(doc.expected_completion_date or ""),
        },
        customer_name=customer_name,
        ref_doctype="Service Request",
        ref_name=doc.name,
    )


def _notify_repair_completed(doc, phone, customer_name):
    settings = _get_settings()
    if not settings:
        return

    from ch_item_master.ch_core.whatsapp import send_template_message

    send_template_message(
        phone=phone,
        template_name=settings.gofix_repair_completed,
        body_values={
            "1": customer_name,
            "2": doc.name,
            "3": doc.device_item_name or "",
        },
        customer_name=customer_name,
        ref_doctype="Service Request",
        ref_name=doc.name,
    )


def _notify_ready_for_delivery(doc, phone, customer_name):
    settings = _get_settings()
    if not settings:
        return

    from ch_item_master.ch_core.whatsapp import send_template_message

    send_template_message(
        phone=phone,
        template_name=settings.gofix_ready_for_delivery,
        body_values={
            "1": customer_name,
            "2": doc.name,
            "3": doc.device_item_name or "",
        },
        customer_name=customer_name,
        ref_doctype="Service Request",
        ref_name=doc.name,
    )


def _notify_not_repairable(doc, phone, customer_name):
    """Notify customer that device is Not Repairable / BER."""
    settings = _get_settings()
    if not settings:
        return

    template_name = getattr(settings, "gofix_not_repairable", None)
    if not template_name:
        # Fallback: use generic status update template or SMS
        _fallback_sms(
            phone,
            "not_repairable",
            {"sr_name": doc.name, "customer_name": customer_name},
        )
        return

    try:
        from ch_item_master.ch_core.whatsapp import send_template_message

        status_label = doc.repairability_status or "Not Repairable"
        reason = doc.rejection_reason or doc.get("repairability_reason") or ""

        send_template_message(
            phone=phone,
            template_name=template_name,
            body_values={
                "1": customer_name,
                "2": doc.name,
                "3": doc.device_item_name or "",
                "4": status_label,
                "5": reason[:200] if reason else "Device cannot be repaired",
            },
            customer_name=customer_name,
            ref_doctype="Service Request",
            ref_name=doc.name,
        )
    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            f"Not Repairable WhatsApp failed for {doc.name}",
        )


# ── Estimate Approval Notifications ─────────────────────────────────

def send_whatsapp_template(template_name, to_number, params, ref_doctype="Service Request", ref_name=None):
    """Generic wrapper to send any GoFix WhatsApp template.

    Called from orchestration.py and other modules.
    Falls back to SMS if WhatsApp is not configured.
    """
    settings = _get_settings()
    if not settings:
        _fallback_sms(to_number, template_name, params)
        return

    # Resolve template name from settings if it's a GoFix key
    actual_template = getattr(settings, template_name, None) or template_name

    try:
        from ch_item_master.ch_core.whatsapp import send_template_message

        # Build body_values from params
        body_values = {}
        i = 1
        for key in ("customer_name", "sr_name", "device_name", "estimate_amount", "version", "tracking_url"):
            if key in params:
                body_values[str(i)] = str(params[key])
                i += 1

        send_template_message(
            phone=to_number,
            template_name=actual_template,
            body_values=body_values,
            customer_name=params.get("customer_name", ""),
            ref_doctype=ref_doctype,
            ref_name=ref_name or params.get("sr_name", ""),
        )
    except Exception:
        frappe.log_error(frappe.get_traceback(), f"WhatsApp send failed: {template_name}")
        _fallback_sms(to_number, template_name, params)


def notify_estimate_for_approval(service_request_name, version_number, estimate_amount):
    """Send estimate approval request to customer via WhatsApp with tracking link."""
    doc = frappe.get_doc("Service Request", service_request_name)
    phone = doc.contact_number
    if not phone:
        return

    settings = _get_settings()
    if not settings:
        return

    # Generate tracking URL for the customer
    from gofix.www.track_repair.index import generate_tracking_url
    tracking_url = generate_tracking_url(service_request_name)

    template_key = "gofix_estimate_approval" if int(version_number) == 1 else "gofix_revised_estimate"
    actual_template = getattr(settings, template_key, None)

    if not actual_template:
        frappe.log_error(f"WhatsApp template '{template_key}' not configured in CH WhatsApp Settings")
        return

    try:
        from ch_item_master.ch_core.whatsapp import send_template_message

        send_template_message(
            phone=phone,
            template_name=actual_template,
            body_values={
                "1": doc.customer_name or "Customer",
                "2": doc.name,
                "3": doc.device_item_name or "",
                "4": frappe.format_value(estimate_amount, {"fieldtype": "Currency"}),
                "5": str(version_number),
                "6": tracking_url,
            },
            customer_name=doc.customer_name,
            ref_doctype="Service Request",
            ref_name=doc.name,
        )
    except Exception:
        frappe.log_error(frappe.get_traceback(), f"Estimate WhatsApp failed for {service_request_name}")


def notify_tracking_link(service_request_name):
    """Send the tracking link to customer on acceptance."""
    doc = frappe.get_doc("Service Request", service_request_name)
    phone = doc.contact_number
    if not phone:
        return

    settings = _get_settings()
    if not settings:
        return

    actual_template = getattr(settings, "gofix_tracking_link", None)
    if not actual_template:
        return

    from gofix.www.track_repair.index import generate_tracking_url
    tracking_url = generate_tracking_url(service_request_name)

    try:
        from ch_item_master.ch_core.whatsapp import send_template_message

        send_template_message(
            phone=phone,
            template_name=actual_template,
            body_values={
                "1": doc.customer_name or "Customer",
                "2": doc.name,
                "3": doc.device_item_name or "",
                "4": tracking_url,
            },
            customer_name=doc.customer_name,
            ref_doctype="Service Request",
            ref_name=doc.name,
        )
    except Exception:
        frappe.log_error(frappe.get_traceback(), f"Tracking link WhatsApp failed for {service_request_name}")


def _fallback_sms(phone, template_name, params):
    """Send a plain SMS if WhatsApp is not available."""
    try:
        from frappe.core.doctype.sms_settings.sms_settings import send_sms

        sr = params.get("sr_name", "")
        amount = params.get("estimate_amount", "")
        msg = f"GoFix: Your repair request {sr} has an update."
        if "estimate" in template_name.lower():
            msg = f"GoFix: Estimate of {amount} for {sr}. Please approve at {params.get('tracking_url', 'our store')}."

        send_sms([phone], msg)
    except Exception:
        pass
