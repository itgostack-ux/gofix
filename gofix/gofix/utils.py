import frappe


def has_app_permission(user=None):
    """Check if user has permission to access GoFix app"""
    from gofix.config import has_role_setting

    user = user or frappe.session.user
    return has_role_setting("app_access_roles", user=user)
