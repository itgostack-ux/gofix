import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def create_material_request_custom_fields():
    """Add service_request link on Material Request for GoFix traceability."""
    custom_fields = {
        "Material Request": [
            {
                "fieldname": "service_request",
                "label": "Service Request",
                "fieldtype": "Link",
                "options": "Service Request",
                "insert_after": "material_request_type",
                "read_only": 1,
                "in_standard_filter": 1,
                "description": "Linked GoFix Service Request (auto-populated)",
            },
        ],
    }
    create_custom_fields(custom_fields, update=True)
