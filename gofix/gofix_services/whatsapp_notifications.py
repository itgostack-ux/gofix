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
    elif new_decision == "Completed":
        _notify_repair_completed(doc, phone, customer_name)
    elif new_decision == "Delivered":
        _notify_ready_for_delivery(doc, phone, customer_name)


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
