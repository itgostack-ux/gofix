import frappe
from frappe import _

from gofix.config import get_role_setting, has_role_setting, is_privileged_user


try:
    from ch_item_master.security import get_user_allowed_companies
except (ImportError, ModuleNotFoundError):
    def get_user_allowed_companies(user=None):
        user = user or frappe.session.user
        if is_privileged_user(user):
            return None
        companies = set()
        if frappe.db.exists("DocType", "POS Executive"):
            try:
                companies.update(filter(None, frappe.get_all(
                    "POS Executive",
                    filters={"user": user, "is_active": 1},
                    pluck="company",
                )))
            except Exception:
                frappe.log_error(
                    frappe.get_traceback(),
                    "GoFix fallback company-scope resolution failed",
                )
                return []
        return sorted(companies)


def _get_roles(user):
    try:
        return set(frappe.get_roles(user))
    except Exception:
        return set()


def _is_service_manager(user=None):
    """Return True if the user holds a manager-level GoFix role."""
    user = user or frappe.session.user
    manager_roles = get_role_setting("service_manager_roles")
    return is_privileged_user(user) or bool(_get_roles(user).intersection(manager_roles))


def _user_employee(user=None):
    """Employee record linked to this user (technician login), if any."""
    user = user or frappe.session.user
    if not user or user in ("Administrator", "Guest"):
        return None
    return frappe.db.get_value("Employee", {"user_id": user}, "name")


def _can_access_service_requests(user=None):
    user = user or frappe.session.user
    if not user or user == "Guest":
        return False
    return has_role_setting("service_access_roles", user=user)


def _get_user_service_scope(user=None):
    """Return explicit company/warehouse scope; ``None`` means bypass."""
    user = user or frappe.session.user
    if is_privileged_user(user):
        return None

    companies = set()
    warehouses = set()
    try:
        from ch_erp15.ch_erp15.scope import get_user_scope
    except (ImportError, ModuleNotFoundError):
        get_user_scope = None
    if get_user_scope:
        try:
            scope = get_user_scope(user)
            if scope.get("bypass"):
                return None
            companies.update(filter(None, scope.get("companies") or set()))
            warehouses.update(filter(None, scope.get("warehouses") or set()))
        except Exception:
            frappe.log_error(
                frappe.get_traceback(),
                "GoFix authoritative user-scope resolution failed",
            )
            return {"companies": set(), "warehouses": set()}

    store_names = set()
    for doctype in ("POS Executive", "CH POS User Allocation"):
        if not frappe.db.exists("DocType", doctype):
            continue
        try:
            rows = frappe.get_all(
                doctype,
                filters={"user": user, "is_active": 1},
                fields=["company", "store"],
            )
        except Exception:
            frappe.log_error(
                frappe.get_traceback(),
                f"GoFix {doctype} scope resolution failed",
            )
            return {"companies": set(), "warehouses": set()}
        companies.update(row.company for row in rows if row.company)
        store_names.update(row.store for row in rows if row.store)

    if store_names and frappe.db.exists("DocType", "CH Store"):
        warehouses.update(filter(None, frappe.get_all(
            "CH Store",
            filters={"name": ("in", list(store_names)), "disabled": 0},
            pluck="warehouse",
        )))

    mapped_companies = get_user_allowed_companies(user)
    if mapped_companies is None:
        return None
    if mapped_companies:
        mapped_companies = set(mapped_companies)
        companies = companies.intersection(mapped_companies) if companies else mapped_companies
    else:
        companies.clear()

    return {"companies": companies, "warehouses": warehouses}


def get_user_service_scope(user=None):
    """Public, fail-closed resolver for GoFix operational location scope."""
    return _get_user_service_scope(user)


def _service_request_anchors(doc) -> set[str]:
    return {
        value for value in (
            doc.get("source_warehouse"),
            doc.get("current_location"),
            doc.get("current_processing_location"),
            doc.get("transferred_to_store"),
        ) if value
    }


def _scope_clauses(user=None) -> list[str] | None:
    scope = _get_user_service_scope(user)
    if scope is None:
        return None
    companies = scope["companies"]
    warehouses = scope["warehouses"]
    if not companies or not warehouses:
        return ["1=0"]
    escaped_companies = ", ".join(frappe.db.escape(value) for value in sorted(companies))
    escaped_warehouses = ", ".join(frappe.db.escape(value) for value in sorted(warehouses))
    return [
        f"`tabService Request`.`company` IN ({escaped_companies})",
        "("
        f"`tabService Request`.`source_warehouse` IN ({escaped_warehouses}) OR "
        f"`tabService Request`.`current_location` IN ({escaped_warehouses}) OR "
        f"`tabService Request`.`transferred_to_store` IN ({escaped_warehouses})"
        ")",
    ]


def assert_service_request_access(service_request, permission_type="read", user=None):
    """Load and authorize one named Service Request, including store scope."""
    user = user or frappe.session.user
    doc = service_request if hasattr(service_request, "get") else frappe.get_doc("Service Request", service_request)
    if not frappe.has_permission("Service Request", ptype=permission_type, doc=doc, user=user):
        frappe.throw(_("You do not have permission to access this Service Request."), frappe.PermissionError)
    if not has_service_request_permission(doc=doc, user=user, permission_type=permission_type):
        frappe.throw(_("This Service Request is outside your company or store scope."), frappe.PermissionError)
    return doc


# ──────────────────────────────────────────────────────────────────────────────
# Service Request permissions
# ──────────────────────────────────────────────────────────────────────────────

def get_service_request_query(user=None):
    """
    List-view filter for Service Request.
    Configured manager roles can read records inside their assigned scope.
    Other configured service roles can read only records they own or service.
    """
    user = user or frappe.session.user
    if not _can_access_service_requests(user):
        return "1=0"

    scope_clauses = _scope_clauses(user)
    if scope_clauses is None:
        return None
    if scope_clauses == ["1=0"]:
        return "1=0"

    if _is_service_manager(user):
        # Managers see all records in allowed companies
        return " AND ".join(f"({clause})" for clause in scope_clauses)

    # Non-managers (Service Engineer / Service User): own records only —
    # owner/assignee/receiver, or tickets where they are the technician on a
    # solution line / Job Assignment (multi-technician split tickets).
    ue = frappe.db.escape(user)
    own_parts = [
        f"`tabService Request`.`owner` = {ue}",
        f"`tabService Request`.`assigned_to_user` = {ue}",
        f"`tabService Request`.`received_by` = {ue}",
    ]
    employee = _user_employee(user)
    if employee:
        emp = frappe.db.escape(employee)
        own_parts.append(
            "EXISTS (SELECT 1 FROM `tabSR Solution Line` sl "
            "WHERE sl.parent = `tabService Request`.`name` "
            f"AND sl.parenttype = 'Service Request' AND sl.technician = {emp})"
        )
        own_parts.append(
            "EXISTS (SELECT 1 FROM `tabJob Assignment` ja "
            "WHERE ja.service_request = `tabService Request`.`name` "
            f"AND ja.service_engineer = {emp} AND ja.docstatus < 2)"
        )
    own_clause = "(" + " OR ".join(own_parts) + ")"
    return " AND ".join([*(f"({clause})" for clause in scope_clauses), own_clause])


def has_service_request_permission(doc=None, user=None, permission_type=None):
    user = user or frappe.session.user
    if not _can_access_service_requests(user):
        return False

    scope = _get_user_service_scope(user)
    if scope is None:
        return True
    if not scope["companies"] or not scope["warehouses"]:
        return False

    if doc is None:
        return True

    if isinstance(doc, str):
        doc = frappe.get_doc("Service Request", doc)

    doc_company = doc.get("company")
    if not doc_company or doc_company not in scope["companies"]:
        return False
    anchors = _service_request_anchors(doc)
    if not anchors or not anchors.intersection(scope["warehouses"]):
        return False

    if _is_service_manager(user):
        return True

    # Non-manager: must be owner, assignee, receiver — or the technician on a
    # solution line / Job Assignment of this ticket.
    owner = doc.get("owner") if hasattr(doc, "get") else None
    assigned = doc.get("assigned_to_user") if hasattr(doc, "get") else None
    received = doc.get("received_by") if hasattr(doc, "get") else None
    if user in {owner, assigned, received}:
        return True

    sr_name = doc.get("name") if hasattr(doc, "get") else None
    employee = _user_employee(user)
    if not sr_name or not employee:
        return False
    if frappe.db.exists(
        "SR Solution Line",
        {"parent": sr_name, "parenttype": "Service Request", "technician": employee},
    ):
        return True
    return bool(
        frappe.db.exists(
            "Job Assignment",
            {"service_request": sr_name, "service_engineer": employee, "docstatus": ("<", 2)},
        )
    )


# ──────────────────────────────────────────────────────────────────────────────
# GoFix Token permissions
# ──────────────────────────────────────────────────────────────────────────────

_TOKEN_ROLES = ("System Manager", "Service Manager", "Store Manager", "Store Executive")


def _can_access_tokens(user=None):
    user = user or frappe.session.user
    return bool(
        user
        and user != "Guest"
        and has_role_setting("token_transition_roles", _TOKEN_ROLES, user=user)
    )


def get_gofix_token_query(user=None):
    """Limit Desk/list access to tokens in every configured company/store anchor."""
    user = user or frappe.session.user
    if not _can_access_tokens(user):
        return "1=0"
    scope = _get_user_service_scope(user)
    if scope is None:
        return None
    companies = set(scope.get("companies") or ())
    warehouses = set(scope.get("warehouses") or ())
    if not companies or not warehouses:
        return "1=0"
    company_sql = ", ".join(frappe.db.escape(value) for value in sorted(companies))
    warehouse_sql = ", ".join(frappe.db.escape(value) for value in sorted(warehouses))
    return (
        f"`tabGoFix Token`.`company` IN ({company_sql}) AND "
        f"`tabGoFix Token`.`store` IN ({warehouse_sql})"
    )


def has_gofix_token_permission(doc=None, user=None, permission_type=None):
    """Deny named-token access unless both company and store are in scope."""
    user = user or frappe.session.user
    if user == "Guest":
        return bool(
            permission_type == "create"
            and frappe.flags.get("gofix_guest_token_creation")
            and doc is not None
            and doc.get("source") == "Tablet"
            and doc.get("company")
            and doc.get("store")
        )
    if not _can_access_tokens(user):
        return False
    scope = _get_user_service_scope(user)
    if scope is None:
        return True
    if doc is None:
        return True
    return bool(
        doc.get("company") in set(scope.get("companies") or ())
        and doc.get("store") in set(scope.get("warehouses") or ())
    )


# ──────────────────────────────────────────────────────────────────────────────
# Job Assignment permissions
# ──────────────────────────────────────────────────────────────────────────────

def get_job_assignment_query(user=None):
    """
    List-view filter for Job Assignment.
    - Service Manager → all assignments linked to their company Service Requests
    - Service Engineer / Service User → only assignments where they are the assigned user or owner
    """
    user = user or frappe.session.user
    if not _can_access_service_requests(user):
        return "1=0"

    sr_query = get_service_request_query(user)
    if _is_service_manager(user):
        if sr_query is None:
            return None
        return (
            "`tabJob Assignment`.`service_request` IN "
            f"(SELECT `name` FROM `tabService Request` WHERE {sr_query})"
        )

    # Non-manager: own assignments only
    ue = frappe.db.escape(user)
    own_clause = (
        f"(`tabJob Assignment`.`owner` = {ue} "
        f"OR `tabJob Assignment`.`user` = {ue})"
    )
    if sr_query is None:
        return own_clause
    return (
        f"{own_clause} AND `tabJob Assignment`.`service_request` IN "
        f"(SELECT `name` FROM `tabService Request` WHERE {sr_query})"
    )


def has_job_assignment_permission(doc=None, user=None, permission_type=None):
    user = user or frappe.session.user
    if not _can_access_service_requests(user):
        return False

    if doc is None:
        scope = _get_user_service_scope(user)
        return scope is None or bool(scope["companies"] and scope["warehouses"])

    if isinstance(doc, str):
        doc = frappe.get_doc("Job Assignment", doc)

    service_request = doc.get("service_request")
    if not service_request:
        return False
    sr = frappe.get_doc("Service Request", service_request)
    if not has_service_request_permission(sr, user=user, permission_type=permission_type):
        return False

    if _is_service_manager(user):
        return True

    owner = doc.get("owner") if hasattr(doc, "get") else None
    assigned_user = doc.get("user") if hasattr(doc, "get") else None
    return user in {owner, assigned_user}
