"""Create Frappe Notification records for GoFix repair lifecycle SMS/Email."""

import frappe


NOTIFICATIONS = [
    {
        "name": "GoFix - Device Received",
        "subject": "GoFix | Device Received | {{ doc.name }}",
        "document_type": "Service Request",
        "event": "Value Change",
        "value_changed": "decision",
        "condition": 'doc.decision == "Accepted"',
        "message": (
            "<div style='font-family:Segoe UI,Arial,sans-serif;max-width:680px;border:1px solid #e5e7eb;border-radius:10px;overflow:hidden'>"
            "<div style='background:#0f172a;color:#fff;padding:12px 16px;font-weight:600'>GoFix Services</div>"
            "<div style='padding:16px'>"
            "<p>Dear {{ doc.customer_name }},</p>"
            "<p>Your device has been received at our service centre.</p>"
            "<p><b>Service Request:</b> {{ doc.name }}<br>"
            "<b>Device:</b> {{ doc.device_item_name or '' }}<br>"
            "<b>Estimated Cost:</b> ₹{{ doc.estimated_cost or 0 }}<br>"
            "<b>Expected Completion:</b> {{ doc.expected_completion_date or 'TBD' }}</p>"
            "<p><a href='{{ frappe.utils.get_url_to_form(\"Service Request\", doc.name) }}' style='background:#0b57d0;color:#fff;text-decoration:none;padding:10px 14px;border-radius:6px;display:inline-block;font-weight:600'>Open Service Request</a></p>"
            "</div></div>"
        ),
        "channel": "Email",
        "send_to_all_assignees": 0,
        "recipients": [{"receiver_by_document_field": "email"}],
    },
    {
        "name": "GoFix - Repair Complete Pickup",
        "subject": "GoFix | Repair Complete | {{ doc.name }}",
        "document_type": "Service Request",
        "event": "Value Change",
        "value_changed": "decision",
        "condition": 'doc.decision == "Completed"',
        "message": (
            "<div style='font-family:Segoe UI,Arial,sans-serif;max-width:680px;border:1px solid #e5e7eb;border-radius:10px;overflow:hidden'>"
            "<div style='background:#0f172a;color:#fff;padding:12px 16px;font-weight:600'>GoFix Services</div>"
            "<div style='padding:16px'>"
            "<p>Dear {{ doc.customer_name }},</p>"
            "<p>Great news. Your device repair is complete and ready for pickup.</p>"
            "<p><b>Service Request:</b> {{ doc.name }}<br><b>Device:</b> {{ doc.device_item_name or '' }}</p>"
            "<p><a href='{{ frappe.utils.get_url_to_form(\"Service Request\", doc.name) }}' style='background:#0b57d0;color:#fff;text-decoration:none;padding:10px 14px;border-radius:6px;display:inline-block;font-weight:600'>Open Service Request</a></p>"
            "</div></div>"
        ),
        "channel": "Email",
        "send_to_all_assignees": 0,
        "recipients": [{"receiver_by_document_field": "email"}],
    },
    {
        "name": "GoFix - Unclaimed Device Reminder",
        "subject": "GoFix | Reminder: Device Awaiting Pickup | {{ doc.name }}",
        "document_type": "Service Request",
        "event": "Value Change",
        "value_changed": "unclaimed_flag",
        "condition": "doc.unclaimed_flag == 1",
        "message": (
            "<div style='font-family:Segoe UI,Arial,sans-serif;max-width:680px;border:1px solid #e5e7eb;border-radius:10px;overflow:hidden'>"
            "<div style='background:#0f172a;color:#fff;padding:12px 16px;font-weight:600'>GoFix Services</div>"
            "<div style='padding:16px'>"
            "<p>Dear {{ doc.customer_name }},</p>"
            "<p>This is a reminder that your device is still awaiting collection at our service centre.</p>"
            "<p><b>Service Request:</b> {{ doc.name }}<br><b>Device:</b> {{ doc.device_item_name or '' }}</p>"
            "<p><a href='{{ frappe.utils.get_url_to_form(\"Service Request\", doc.name) }}' style='background:#0b57d0;color:#fff;text-decoration:none;padding:10px 14px;border-radius:6px;display:inline-block;font-weight:600'>Open Service Request</a></p>"
            "</div></div>"
        ),
        "channel": "Email",
        "send_to_all_assignees": 0,
        "recipients": [{"receiver_by_document_field": "email"}],
    },
    {
        "name": "GoFix - Pickup Scheduled",
        "subject": "GoFix | Pickup Scheduled | {{ doc.name }}",
        "document_type": "Service Request",
        "event": "Value Change",
        "value_changed": "pickup_scheduled_datetime",
        "condition": "doc.pickup_scheduled_datetime",
        "message": (
            "<div style='font-family:Segoe UI,Arial,sans-serif;max-width:680px;border:1px solid #e5e7eb;border-radius:10px;overflow:hidden'>"
            "<div style='background:#0f172a;color:#fff;padding:12px 16px;font-weight:600'>GoFix Services</div>"
            "<div style='padding:16px'>"
            "<p>Dear {{ doc.customer_name }},</p>"
            "<p>Your device pickup has been scheduled.</p>"
            "<p><b>Service Request:</b> {{ doc.name }}<br>"
            "<b>Pickup Date & Time:</b> {{ doc.pickup_scheduled_datetime }}<br>"
            "<b>Pickup Address:</b> {{ doc.pickup_address or '' }}</p>"
            "<p><a href='{{ frappe.utils.get_url_to_form(\"Service Request\", doc.name) }}' style='background:#0b57d0;color:#fff;text-decoration:none;padding:10px 14px;border-radius:6px;display:inline-block;font-weight:600'>Open Service Request</a></p>"
            "</div></div>"
        ),
        "channel": "Email",
        "send_to_all_assignees": 0,
        "recipients": [{"receiver_by_document_field": "email"}],
    },
    {
        "name": "GoFix - Return Dispatched",
        "subject": "GoFix | Return Dispatched | {{ doc.name }}",
        "document_type": "Service Request",
        "event": "Value Change",
        "value_changed": "return_dispatched_date",
        "condition": "doc.return_dispatched_date",
        "message": (
            "<div style='font-family:Segoe UI,Arial,sans-serif;max-width:680px;border:1px solid #e5e7eb;border-radius:10px;overflow:hidden'>"
            "<div style='background:#0f172a;color:#fff;padding:12px 16px;font-weight:600'>GoFix Services</div>"
            "<div style='padding:16px'>"
            "<p>Dear {{ doc.customer_name }},</p>"
            "<p>Your repaired device has been dispatched.</p>"
            "<p><b>Service Request:</b> {{ doc.name }}<br>"
            "<b>Courier:</b> {{ doc.return_courier_name or 'Store Delivery' }}<br>"
            "<b>Tracking:</b> {{ doc.return_tracking_number or 'Will be shared shortly' }}</p>"
            "<p><a href='{{ frappe.utils.get_url_to_form(\"Service Request\", doc.name) }}' style='background:#0b57d0;color:#fff;text-decoration:none;padding:10px 14px;border-radius:6px;display:inline-block;font-weight:600'>Open Service Request</a></p>"
            "</div></div>"
        ),
        "channel": "Email",
        "send_to_all_assignees": 0,
        "recipients": [{"receiver_by_document_field": "email"}],
    },
    {
        "name": "GoFix - Return Delivered",
        "subject": "GoFix | Delivery Confirmed | {{ doc.name }}",
        "document_type": "Service Request",
        "event": "Value Change",
        "value_changed": "return_delivered_date",
        "condition": "doc.return_delivered_date",
        "message": (
            "<div style='font-family:Segoe UI,Arial,sans-serif;max-width:680px;border:1px solid #e5e7eb;border-radius:10px;overflow:hidden'>"
            "<div style='background:#0f172a;color:#fff;padding:12px 16px;font-weight:600'>GoFix Services</div>"
            "<div style='padding:16px'>"
            "<p>Dear {{ doc.customer_name }},</p>"
            "<p>We have marked your service request as delivered successfully.</p>"
            "<p><b>Service Request:</b> {{ doc.name }}<br><b>Delivered On:</b> {{ doc.return_delivered_date }}</p>"
            "<p><a href='{{ frappe.utils.get_url_to_form(\"Service Request\", doc.name) }}' style='background:#0b57d0;color:#fff;text-decoration:none;padding:10px 14px;border-radius:6px;display:inline-block;font-weight:600'>Open Service Request</a></p>"
            "</div></div>"
        ),
        "channel": "Email",
        "send_to_all_assignees": 0,
        "recipients": [{"receiver_by_document_field": "email"}],
    },
]


def create_notifications():
    """Create/update GoFix notification records.  Safe to run on every migrate."""
    for notif in NOTIFICATIONS:
        recipients = notif.get("recipients", [])
        notif_fields = dict(notif)
        notif_fields.pop("recipients", None)

        existing = frappe.db.exists("Notification", notif["name"])
        if existing:
            doc = frappe.get_doc("Notification", notif["name"])
            doc.update(notif_fields)
            doc.set("recipients", [])
            for r in recipients:
                doc.append("recipients", r)
            doc.enabled = 1
            doc.save(ignore_permissions=True)
            print(f"Updated Notification: {notif['name']}")
            continue

        doc = frappe.new_doc("Notification")
        doc.update(notif_fields)
        doc.enabled = 1
        for r in recipients:
            doc.append("recipients", r)
        doc.insert(ignore_permissions=True)
        print(f"Created Notification: {notif['name']}")

    frappe.db.commit()
