import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def create_sales_invoice_custom_fields():
    """Create custom fields on Sales Invoice for GoFix Service linkage."""
    if not frappe.db.exists("DocType", "Service Request"):
        return

    custom_fields = {
        "Sales Invoice": [
            {
                "fieldname": "gofix_service_section",
                "label": "GoFix Service Details",
                "fieldtype": "Section Break",
                "insert_after": "custom_repair_intake",
                "collapsible": 1,
                "depends_on": "eval:doc.custom_gofix_service_request",
            },
            {
                "fieldname": "custom_gofix_service_request",
                "label": "Service Request",
                "fieldtype": "Link",
                "options": "Service Request",
                "insert_after": "gofix_service_section",
                "read_only": 1,
                "in_standard_filter": 1,
            },
            {
                "fieldname": "gofix_service_col_break",
                "fieldtype": "Column Break",
                "insert_after": "custom_gofix_service_request",
            },
            {
                "fieldname": "custom_gofix_service_order",
                "label": "Service Order",
                "fieldtype": "Link",
                "options": "Sales Order",
                "insert_after": "gofix_service_col_break",
                "read_only": 1,
                "in_standard_filter": 1,
            },
        ],
    }

    create_custom_fields(custom_fields, update=True)
