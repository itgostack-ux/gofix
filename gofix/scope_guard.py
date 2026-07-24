"""Import-safe access to ch_erp15 store scope for GoFix service APIs.

GoFix records are anchored to a Warehouse (``source_warehouse``). These
helpers let whitelisted intake/search APIs constrain customer PII, serial,
and service-history reads to the caller's allowed warehouses.

When ch_erp15 (the scope authority) is unavailable, only Administrator/System
Manager bypass; every other user is denied because store scope cannot be proven.
"""

from __future__ import annotations


def user_scope():
    """Return ``(warehouses:set, companies:set, bypass:bool)``.

    ``bypass`` is True only for unrestricted users.
    """
    try:
        from ch_erp15.ch_erp15.scope import get_user_scope
    except ImportError:
        from gofix.config import is_privileged_user

        return set(), set(), is_privileged_user()
    scope = get_user_scope()
    if scope.get("bypass"):
        return set(), set(), True
    return (scope.get("warehouses") or set()), (scope.get("companies") or set()), False


def assert_warehouse(warehouse=None, company=None, msg=None):
    """Raise ``frappe.PermissionError`` if the warehouse/company is out of scope."""
    try:
        from ch_erp15.ch_erp15.scope import assert_user_has_store_scope
    except ImportError:
        import frappe
        from frappe import _
        from gofix.config import is_privileged_user

        if is_privileged_user():
            return
        frappe.throw(
            msg or _("Store scope cannot be verified because the scope authority is unavailable."),
            frappe.PermissionError,
        )
    assert_user_has_store_scope(warehouse=warehouse, company=company, msg=msg)
