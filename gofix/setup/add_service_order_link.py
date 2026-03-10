"""
Add Service Order list view link to GoFix workspace
This creates a shortcut to view all Service Orders (Sales Orders with is_service_order=1)
"""

import frappe
from frappe import _

def add_service_order_to_workspace():
    """Add Service Order shortcut to GoFix workspace"""
    
    # Check if workspace exists
    if not frappe.db.exists("Workspace", "GoFix"):
        print("❌ GoFix workspace not found")
        return
    
    workspace = frappe.get_doc("Workspace", "GoFix")
    
    # Check if Service Order link already exists
    existing_links = [link.label for link in workspace.links]
    if "Service Orders" in existing_links:
        print("✅ Service Orders link already exists in workspace")
        return
    
    # Find the Services section
    services_section = None
    for link in workspace.links:
        if link.type == "Card Break" and link.label == "Services":
            services_section = link
            break
    
    if not services_section:
        print("❌ Services section not found in workspace")
        return
    
    # Add Service Order link after Service Request
    sr_idx = None
    for idx, link in enumerate(workspace.links):
        if link.link_to == "Service Request":
            sr_idx = idx
            break
    
    if sr_idx is None:
        print("❌ Service Request link not found")
        return
    
    # Insert Service Order link
    workspace.insert(sr_idx + 1, "links", {
        "type": "Link",
        "link_type": "DocType",
        "link_to": "Sales Order",
        "label": "Service Orders",
        "icon": "tool",
        "description": "View all Service Orders",
        "only_for": "",
        "onboard": 0,
        "is_query_report": 0,
        "format": ""
    })
    
    workspace.save(ignore_permissions=True)
    frappe.db.commit()
    
    print("✅ Service Orders link added to GoFix workspace")


if __name__ == "__main__":
    add_service_order_to_workspace()
