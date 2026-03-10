import frappe

def execute():
    if not frappe.db.exists("Custom Field", {"dt": "Service Order", "fieldname": "workflow_state"}):
        frappe.get_doc({
            "doctype": "Custom Field",
            "dt": "Service Order",
            "fieldname": "workflow_state",
            "fieldtype": "Link",
            "options": "Service Order State",
            "label": "Workflow State",
            "insert_after": "status",
        }).insert(ignore_permissions=True)
        frappe.db.commit()
        print("Custom field 'workflow_state' created for Service Order.")
    else:
        print("Custom field 'workflow_state' already exists in Service Order.")
