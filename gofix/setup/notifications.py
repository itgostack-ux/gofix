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
    {
        "name": "GoFix - Pickup Scheduled",
        "subject": "Pickup scheduled for your device - {{ doc.name }}",
        "document_type": "Service Request",
        "event": "Value Change",
        "value_changed": "pickup_scheduled_datetime",
        "condition": "doc.pickup_scheduled_datetime",
        "message": (
            "Dear {{ doc.customer_name }},\n\n"
            "Your device pickup has been scheduled.\n\n"
            "Service Request: {{ doc.name }}\n"
            "Pickup Date & Time: {{ doc.pickup_scheduled_datetime }}\n"
            "Pickup Address: {{ doc.pickup_address or '' }}\n\n"
            "Our team will contact you if any coordination is needed."
        ),
        "channel": "Email",
        "send_to_all_assignees": 0,
        "recipients": [{"receiver_by_document_field": "email"}],
    },
    {
        "name": "GoFix - Return Dispatched",
        "subject": "Your repaired device is on the way - {{ doc.name }}",
        "document_type": "Service Request",
        "event": "Value Change",
        "value_changed": "return_dispatched_date",
        "condition": "doc.return_dispatched_date",
        "message": (
            "Dear {{ doc.customer_name }},\n\n"
            "Your repaired device has been dispatched.\n\n"
            "Service Request: {{ doc.name }}\n"
            "Courier: {{ doc.return_courier_name or 'Store Delivery' }}\n"
            "Tracking: {{ doc.return_tracking_number or 'Will be shared shortly' }}\n\n"
            "Thank you for choosing GoFix."
        ),
        "channel": "Email",
        "send_to_all_assignees": 0,
        "recipients": [{"receiver_by_document_field": "email"}],
    },
    {
        "name": "GoFix - Return Delivered",
        "subject": "Delivery confirmed - {{ doc.name }}",
        "document_type": "Service Request",
        "event": "Value Change",
        "value_changed": "return_delivered_date",
        "condition": "doc.return_delivered_date",
        "message": (
            "Dear {{ doc.customer_name }},\n\n"
            "We have marked your service request as delivered successfully.\n\n"
            "Service Request: {{ doc.name }}\n"
            "Delivered On: {{ doc.return_delivered_date }}\n\n"
            "If you need any further help, simply reply to this email or visit your nearest GoFix store."
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
