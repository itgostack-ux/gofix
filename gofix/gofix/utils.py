import frappe

def has_app_permission(user=None):
    """Check if user has permission to access GoFix app"""
    if not user:
        user = frappe.session.user
    
    # Allow all users for now, can be customized based on roles
    return True
