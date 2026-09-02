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
    # The Ops Hub creates AND immediately submits these on the floor manager's
    # behalf (assign_diagnosis_technician, spare consumption). Every live role
    # row carried submit=0, so technician assignment worked only for
    # Administrator -- the flow's first step was dead for real users.
    "Job Assignment": {"create", "read", "write", "submit", "cancel"},
    "Spare Parts Usage": {"create", "read", "write", "submit", "cancel"},
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


def _configured_roles(fieldname: str) -> set[str]:
    """Resolve the live role list for a settings field, minus privileged roles."""
    from gofix.config import get_role_setting

    roles = get_role_setting(fieldname)
    return {role for role in roles if role not in _EXCLUDED_ROLES}


def _operational_docperm_specs() -> dict[str, dict[str, set[str]]]:
    """Build DocType -> role -> ptypes from the same role registry the gates use."""
    manager_roles = _configured_roles(
        "job_assignment_manager_roles"
    )
    intake_roles = _configured_roles(
        "token_transition_roles"
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
    # Frappe refuses cancel without submit, and Custom DocPerm's own on_update
    # re-validates the WHOLE doctype on every row save -- so a historically
    # inconsistent row (System Manager on Job Assignment) makes every later
    # grant explode. Heal those rows before touching anything.
    for doctype in specs:
        if not frappe.db.exists("DocType", doctype):
            continue
        for name in frappe.get_all(
            "Custom DocPerm",
            filters={"parent": doctype, "cancel": 1, "submit": 0},
            pluck="name",
        ):
            frappe.db.set_value("Custom DocPerm", name, "submit", 1, update_modified=False)
            changed += 1
            touched.add(doctype)
    for doctype, role_map in specs.items():
        if not frappe.db.exists("DocType", doctype):
            continue
        for role, permission_types in role_map.items():
            if not frappe.db.exists("Role", role):
                continue
            name = frappe.db.get_value(
                "Custom DocPerm",
                {"parent": doctype, "role": role, "permlevel": 0, "if_owner": 0},
                "name")
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


# Warehouse link fields on Service Request that must NOT be governed by
# Warehouse User Permissions. Scope for this doctype is decided in one place —
# gofix.security.get_service_request_query — and it deliberately ORs across
# these fields, because a device transferred to a hub is still the origin
# store's ticket.
_SR_UNGOVERNED_WAREHOUSE_FIELDS = (
    "source_warehouse",
    "current_location",
    "current_processing_location",
    "billing_location",
    "transferred_to_store",
)


def ignore_user_permissions_on_service_locations():
    """Stop Warehouse User Permissions from hiding ordinary tickets.

    A CH User Scope issues one Warehouse User Permission per store with
    apply_to_all_doctypes set, and Frappe then ANDs a match condition for EVERY
    Warehouse link field on the doctype. A Service Request carries five of them,
    and `transferred_to_store` is empty on any ticket that was never moved — so
    the AND failed and a scoped user saw nothing but transferred devices.

    The custom permission query already implements the intended rule (an OR
    across the same fields, inside the user's companies), so the User Permission
    layer here is both redundant and wrong. Turning it off for these fields
    leaves exactly one authority on who sees which ticket.

    Property Setters, so this is configuration rather than a schema edit, and
    re-running it is a no-op.
    """
    if not frappe.db.exists("DocType", "Service Request"):
        return

    meta = frappe.get_meta("Service Request")
    changed = []
    for fieldname in _SR_UNGOVERNED_WAREHOUSE_FIELDS:
        df = meta.get_field(fieldname)
        if not df or df.fieldtype != "Link":
            continue
        if cint(df.ignore_user_permissions):
            continue
        frappe.make_property_setter(
            {
                "doctype": "Service Request",
                "fieldname": fieldname,
                "property": "ignore_user_permissions",
                "value": 1,
                "property_type": "Check",
            },
            is_system_generated=True,
        )
        changed.append(fieldname)

    if changed:
        frappe.clear_cache(doctype="Service Request")
        frappe.logger("gofix").info(
            f"GoFix: user permissions no longer gate {', '.join(changed)}"
        )
    return changed

