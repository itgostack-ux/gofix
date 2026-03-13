"""Create Frappe Notification records for GoFix repair lifecycle SMS/Email."""

import frappe


NOTIFICATIONS = [
    {
        "name": "GoFix - Device Received",
        "subject": "Your device has been received - {{ doc.name }}",
        "document_type": "Service Request",
        "event": "Value Change",
        "value_changed": "decision",
        "condition": 'doc.decision == "Accepted"',
        "message": (
            "Dear {{ doc.customer_name }},\n\n"
            "Your device has been received at our service centre.\n\n"
            "Service Request: {{ doc.name }}\n"
            "Device: {{ doc.device_item_name or '' }}\n"
            "Estimated Cost: ₹{{ doc.estimated_cost or 0 }}\n"
            "Expected Completion: {{ doc.expected_completion_date or 'TBD' }}\n\n"
            "Thank you for choosing GoFix."
        ),
        "channel": "Email",
        "send_to_all_assignees": 0,
        "recipients": [{"receiver_by_document_field": "email"}],
    },
    {
        "name": "GoFix - Repair Complete Pickup",
        "subject": "Your device is ready for pickup - {{ doc.name }}",
        "document_type": "Service Request",
        "event": "Value Change",
        "value_changed": "decision",
        "condition": 'doc.decision == "Completed"',
        "message": (
            "Dear {{ doc.customer_name }},\n\n"
            "Great news! Your device repair is complete and ready for pickup.\n\n"
            "Service Request: {{ doc.name }}\n"
            "Device: {{ doc.device_item_name or '' }}\n\n"
            "Please collect your device at your earliest convenience.\n\n"
            "Thank you for choosing GoFix."
        ),
        "channel": "Email",
        "send_to_all_assignees": 0,
        "recipients": [{"receiver_by_document_field": "email"}],
    },
    {
        "name": "GoFix - Unclaimed Device Reminder",
        "subject": "Reminder: Please collect your device - {{ doc.name }}",
        "document_type": "Service Request",
        "event": "Value Change",
        "value_changed": "unclaimed_flag",
        "condition": "doc.unclaimed_flag == 1",
        "message": (
            "Dear {{ doc.customer_name }},\n\n"
            "This is a reminder that your device is still awaiting collection "
            "at our service centre.\n\n"
            "Service Request: {{ doc.name }}\n"
            "Device: {{ doc.device_item_name or '' }}\n\n"
            "Please collect your device as soon as possible. Devices unclaimed "
            "for extended periods may be subject to storage policies.\n\n"
            "Thank you,\nGoFix Services"
        ),
        "channel": "Email",
        "send_to_all_assignees": 0,
        "recipients": [{"receiver_by_document_field": "email"}],
    },
]


def create_notifications():
    """Create/update GoFix notification records.  Safe to run on every migrate."""
    for notif in NOTIFICATIONS:
        if frappe.db.exists("Notification", notif["name"]):
            continue

        recipients = notif.pop("recipients", [])
        doc = frappe.new_doc("Notification")
        doc.update(notif)
        doc.enabled = 1
        for r in recipients:
            doc.append("recipients", r)
        doc.insert(ignore_permissions=True)
        print(f"Created Notification: {notif['name']}")

    frappe.db.commit()
