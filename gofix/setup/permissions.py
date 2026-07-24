import frappe
from frappe.utils import cint

# Transactional documents the GoFix flows create on behalf of operational
# roles (guarded by frappe.has_permission checks in service_request.py,
# spare_parts_usage.py, orchestration.py, purchase_api.py, api.py and the
# Ops Hub page). The app must provision the DocPerms its own gates assume.
MANAGER_TRANSACTION_GRANTS = {
    "Sales Order": {"create", "read", "write", "submit"},
    "Sales Invoice": {"create", "read", "write", "submit"},
    "Stock Entry": {"create", "read", "write", "submit"},
    "Payment Entry": {"create", "read", "write", "submit"},
    "Serial No": {"create", "read", "write"},
}

INTAKE_TRANSACTION_GRANTS = {
    "Serial No": {"create", "read", "write"},
    "Sales Order": {"read"},
    "Sales Invoice": {"read"},
}

_EXCLUDED_ROLES = frozenset({"System Manager", "Administrator", "Guest", "All"})


def _settings_default_roles(fieldname: str) -> tuple[str, ...]:
    """Read the shipped default of a GoFix Settings role field (single source of truth)."""
    try:
        field = frappe.get_meta("GoFix Settings").get_field(fieldname)
        default = (field and field.default) or ""
    except Exception:
        default = ""
    return tuple(role.strip() for role in default.split("\n") if role.strip())


def _configured_roles(fieldname: str, fallback: tuple[str, ...] = ()) -> set[str]:
    """Resolve the live role list for a settings field, minus privileged roles."""
    from gofix.config import get_role_setting

    roles = get_role_setting(fieldname, _settings_default_roles(fieldname) or fallback)
    return {role for role in roles if role not in _EXCLUDED_ROLES}


def _operational_docperm_specs() -> dict[str, dict[str, set[str]]]:
    """Build DocType -> role -> ptypes from the same role registry the gates use."""
    manager_roles = _configured_roles(
        "job_assignment_manager_roles", ("Service Manager", "GoFix Floor Manager")
    )
    intake_roles = _configured_roles(
        "token_transition_roles", ("Store Manager", "Store Executive")
    ) - manager_roles

    specs: dict[str, dict[str, set[str]]] = {}
    for doctype, ptypes in MANAGER_TRANSACTION_GRANTS.items():
        for role in manager_roles:
            specs.setdefault(doctype, {})[role] = set(ptypes)
    for doctype, ptypes in INTAKE_TRANSACTION_GRANTS.items():
        for role in intake_roles:
            specs.setdefault(doctype, {}).setdefault(role, set(ptypes))
    return specs


def _grant_missing_permission_types(specs: dict[str, dict[str, set[str]]]) -> int:
    """Additively raise existing app-managed Custom DocPerm rows to the spec.

    seed_default_docperms only inserts missing rows; rows the app seeded earlier
    (e.g. read-only Sales Order rows) must gain the newly required ptypes too.
    Only sets bits — never clears — so administrator additions survive.
    """
    from frappe.core.doctype.doctype.doctype import validate_permissions_for_doctype

    changed = 0
    touched = set()
    for doctype, role_map in specs.items():
        if not frappe.db.exists("DocType", doctype):
            continue
        for role, permission_types in role_map.items():
            if not frappe.db.exists("Role", role):
                continue
            name = frappe.db.get_value(
                "Custom DocPerm",
                {"parent": doctype, "role": role, "permlevel": 0, "if_owner": 0},
                "name",
            )
            if not name:
                continue
            row = frappe.get_doc("Custom DocPerm", name)
            missing = {ptype for ptype in permission_types if not cint(row.get(ptype))}
            if not missing:
                continue
            for ptype in missing:
                row.set(ptype, 1)
            row.save(ignore_permissions=True)
            changed += 1
            touched.add(doctype)
    for doctype in touched:
        validate_permissions_for_doctype(doctype)
        frappe.clear_cache(doctype=doctype)
    return changed


def seed_operational_docperms() -> int:
    """Provision the DocPerms the app's permission gates assume (idempotent)."""
    from ch_erp15.ch_erp15.default_permissions import seed_default_docperms

    specs = _operational_docperm_specs()
    created = seed_default_docperms(specs)
    updated = _grant_missing_permission_types(specs)
    return created + updated


def ensure_default_permissions():
    if not frappe.db.exists("Role", "GoFix Floor Manager"):
        frappe.get_doc({
            "doctype": "Role",
            "role_name": "GoFix Floor Manager",
            "desk_access": 1,
        }).insert(ignore_permissions=True)

    from ch_erp15.ch_erp15.default_permissions import seed_default_docperms

    seed_default_docperms({
        "Employee": {
            "Service Manager": {"read"},
            "Service Engineer": {"read"},
            "GoFix Floor Manager": {"read"},
        },
        "Sales Order": {
            "Service Manager": {"read"},
            "Service Engineer": {"read"},
            "GoFix Floor Manager": {"read"},
        },
    })

    seed_operational_docperms()
