#!/usr/bin/env python3
"""
Setup script for GoFix permissions and roles
Run this after installation or when updating permissions
"""

import frappe
from frappe import _

def setup_gofix_permissions():
    """Setup permissions for all GoFix DocTypes"""
    
    # Define DocTypes and their permissions
    doctypes_permissions = {
        "Service Request": [
            {"role": "System Manager", "full": True},
            {"role": "Sales Manager", "full": True},
            {"role": "Sales User", "create": True, "read": True, "write": True, "submit": True},
            {"role": "Customer", "read": True, "export": True, "print": True}
        ],
        "Job Assignment": [
            {"role": "System Manager", "full": True},
            {"role": "Sales Manager", "full": True},
            {"role": "Sales User", "create": True, "read": True, "write": True, "submit": True}
        ],
        "Spare Parts Usage": [
            {"role": "System Manager", "full": True},
            {"role": "Sales Manager", "full": True},
            {"role": "Sales User", "create": True, "read": True, "write": True, "submit": True},
            {"role": "Stock User", "read": True}
        ],
        "Walkin Source": [
            {"role": "System Manager", "full": True},
            {"role": "Sales Manager", "full": True},
            {"role": "Sales User", "read": True}
        ],
        "Issue Category": [
            {"role": "System Manager", "full": True},
            {"role": "Sales Manager", "full": True},
            {"role": "Sales User", "read": True}
        ],
        "Withdrawal Reason": [
            {"role": "System Manager", "full": True},
            {"role": "Sales Manager", "full": True},
            {"role": "Sales User", "read": True}
        ]
    }
    
    for doctype_name, permissions in doctypes_permissions.items():
        if not frappe.db.exists("DocType", doctype_name):
            print(f"DocType {doctype_name} does not exist, skipping...")
            continue
            
        doc = frappe.get_doc("DocType", doctype_name)
        
        # Clear existing permissions (optional - comment out if you want to keep existing)
        # doc.permissions = []
        
        # Get existing roles to avoid duplicates
        existing_roles = [p.role for p in doc.permissions]
        
        for perm in permissions:
            role = perm["role"]
            
            # Skip if role already has permissions
            if role in existing_roles:
                print(f"  - {role} already has permissions on {doctype_name}, skipping...")
                continue
            
            # Full permissions
            if perm.get("full"):
                doc.append("permissions", {
                    "role": role,
                    "read": 1,
                    "write": 1,
                    "create": 1,
                    "delete": 1,
                    "submit": 1,
                    "cancel": 1,
                    "amend": 1,
                    "report": 1,
                    "export": 1,
                    "print": 1,
                    "email": 1,
                    "share": 1
                })
            else:
                # Specific permissions
                doc.append("permissions", {
                    "role": role,
                    "read": perm.get("read", 0),
                    "write": perm.get("write", 0),
                    "create": perm.get("create", 0),
                    "delete": perm.get("delete", 0),
                    "submit": perm.get("submit", 0),
                    "cancel": perm.get("cancel", 0),
                    "amend": perm.get("amend", 0),
                    "report": perm.get("report", 0),
                    "export": perm.get("export", 0),
                    "print": perm.get("print", 0),
                    "email": perm.get("email", 0),
                    "share": perm.get("share", 0)
                })
            
            print(f"  ✓ Added {role} permissions to {doctype_name}")
        
        doc.save()
    
    frappe.db.commit()
    print("\n✅ Permissions setup complete!")


def setup_page_permissions():
    """Setup permissions for custom pages"""
    
    # Job Tracker page permissions
    page_permissions = {
        "job-tracker": ["System Manager", "Sales Manager", "Sales User"]
    }
    
    for page_name, roles in page_permissions.items():
        if not frappe.db.exists("Page", page_name):
            print(f"Page {page_name} does not exist, skipping...")
            continue
        
        page = frappe.get_doc("Page", page_name)
        
        # Clear existing roles
        page.roles = []
        
        for role in roles:
            page.append("roles", {"role": role})
            print(f"  ✓ Added {role} to page {page_name}")
        
        page.save()
    
    frappe.db.commit()
    print("\n✅ Page permissions setup complete!")


if __name__ == "__main__":
    frappe.connect()
    
    print("=" * 60)
    print("GoFix Permissions Setup")
    print("=" * 60)
    print()
    
    print("Setting up DocType permissions...")
    setup_gofix_permissions()
    
    print("\nSetting up Page permissions...")
    setup_page_permissions()
    
    print("\n" + "=" * 60)
    print("All permissions have been configured successfully!")
    print("=" * 60)
