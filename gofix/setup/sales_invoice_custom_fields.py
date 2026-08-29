import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


# Both invoice doctypes get the same pair. POS settles a completed repair by
# putting a `service_request` on the cart row, and pos_api guards that write
# with hasattr(inv, "custom_gofix_service_request") — so on POS Invoice, where
# the field did not exist, the repair was billed but the link back to the
# ticket was silently dropped.
_SERVICE_LINK_FIELDS = [
    {
        "fieldname": "gofix_service_section",
        "label": "GoFix Service Details",
        "fieldtype": "Section Break",
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
]


def _pos_invoice_service_fields():
    """The same link pair for POS Invoice, anchored to a field it actually has."""
    import copy

    fields = copy.deepcopy(_SERVICE_LINK_FIELDS)
    fields[0]["insert_after"] = "customer_name"
    return fields


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
                "insert_after": "custom_guided_session",
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

    if frappe.db.exists("DocType", "POS Invoice"):
        custom_fields["POS Invoice"] = _pos_invoice_service_fields()

    create_custom_fields(custom_fields, update=True)
