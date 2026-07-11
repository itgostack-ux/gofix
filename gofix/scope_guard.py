"""Import-safe access to ch_erp15 store scope for GoFix service APIs.

GoFix records are anchored to a Warehouse (``source_warehouse``). These
helpers let whitelisted intake/search APIs constrain customer PII, serial,
and service-history reads to the caller's allowed warehouses.

When ch_erp15 (the scope authority) is not installed — e.g. GoFix running
standalone in a unit-test env — ``user_scope`` reports ``bypass=True`` and the
guards become no-ops, mirroring the resilience pattern used elsewhere.
"""

from __future__ import annotations


def user_scope():
    """Return ``(warehouses:set, companies:set, bypass:bool)``.

    ``bypass`` is True for unrestricted users (System Manager / Administrator)
    or when the scope module is unavailable.
    """
    try:
        from ch_erp15.ch_erp15.scope import get_user_scope
    except ImportError:
        return set(), set(), True
    scope = get_user_scope()
    if scope.get("bypass"):
        return set(), set(), True
    return (scope.get("warehouses") or set()), (scope.get("companies") or set()), False


def assert_warehouse(warehouse=None, company=None, msg=None):
    """Raise ``frappe.PermissionError`` if the warehouse/company is out of scope."""
    try:
        from ch_erp15.ch_erp15.scope import assert_user_has_store_scope
    except ImportError:
        return
    assert_user_has_store_scope(warehouse=warehouse, company=company, msg=msg)
