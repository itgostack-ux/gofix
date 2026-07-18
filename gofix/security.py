import frappe

# Roles that can access GoFix (any of these = can see GoFix docs)
SERVICE_ROLES = {"Service Manager", "Service Engineer", "Service Viewer", "Service User"}

# Manager-level roles: can see ALL records in their allowed companies
SERVICE_MANAGER_ROLES = {"Service Manager", "Service Viewer", "System Manager"}

# Engineer/User level: can only see their OWN records
SERVICE_USER_ROLES = {"Service Engineer", "Service User"}


try:
    from ch_item_master.security import get_user_allowed_companies
except Exception:
    def get_user_allowed_companies(user=None):
        user = user or frappe.session.user
        if not user or user == "Administrator":
            return None
        try:
            if "System Manager" in set(frappe.get_roles(user)):
                return None
        except Exception:
            pass

        companies = set()
        if frappe.db.exists("DocType", "POS Executive"):
            try:
                companies.update(filter(None, frappe.get_all(
                    "POS Executive",
                    filters={"user": user, "is_active": 1},
                    pluck="company",
                )))
            except Exception:
                pass
        return sorted(companies)


def _is_service_company(company):
    lc = (company or "").lower()
    return "gofix" in lc or "service" in lc


def _get_roles(user):
    try:
        return set(frappe.get_roles(user))
    except Exception:
        return set()


def _is_service_manager(user=None):
    """Return True if the user holds a manager-level GoFix role."""
    user = user or frappe.session.user
    return bool(_get_roles(user).intersection(SERVICE_MANAGER_ROLES))


def _user_employee(user=None):
    """Employee record linked to this user (technician login), if any."""
    user = user or frappe.session.user
    if not user or user in ("Administrator", "Guest"):
        return None
    return frappe.db.get_value("Employee", {"user_id": user}, "name")


def _can_access_service_requests(user=None):
    user = user or frappe.session.user
    if not user or user == "Administrator":
        return True
    roles = _get_roles(user)
    if "System Manager" in roles or roles.intersection(SERVICE_ROLES):
        return True

    allowed_companies = get_user_allowed_companies(user) or []
    return any(_is_service_company(company) for company in allowed_companies)


# ──────────────────────────────────────────────────────────────────────────────
# Service Request permissions
# ──────────────────────────────────────────────────────────────────────────────

def get_service_request_query(user=None):
    """
    List-view filter for Service Request.
    - System Manager / Service Manager / Service Viewer → all records in their company/companies
    - Service Engineer / Service User → only records where they are owner, assignee, or receiver
    """
    user = user or frappe.session.user
    if not _can_access_service_requests(user):
        return "1=0"

    allowed_companies = get_user_allowed_companies(user)

    # Build company clause
    if allowed_companies:
        service_companies = [c for c in allowed_companies if _is_service_company(c)]
        if not service_companies:
            return "1=0"
        escaped_companies = ", ".join(frappe.db.escape(c) for c in service_companies)
        company_clause = f"`tabService Request`.`company` IN ({escaped_companies})"
    else:
        company_clause = None  # no restriction (admin / all companies)

    if _is_service_manager(user):
        # Managers see all records in allowed companies
        return company_clause

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
    if company_clause:
        return f"({company_clause}) AND {own_clause}"
    return own_clause


def has_service_request_permission(doc=None, user=None, permission_type=None):
    user = user or frappe.session.user
    if not _can_access_service_requests(user):
        return False

    if doc is None:
        return True

    # Company check
    allowed_companies = get_user_allowed_companies(user)
    if allowed_companies:
        service_companies = {c for c in allowed_companies if _is_service_company(c)}
        doc_company = doc.get("company") if hasattr(doc, "get") else None
        if doc_company and doc_company not in service_companies:
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

    if _is_service_manager(user):
        allowed_companies = get_user_allowed_companies(user)
        if not allowed_companies:
            return None  # all
        service_companies = [c for c in allowed_companies if _is_service_company(c)]
        if not service_companies:
            return "1=0"
        escaped_companies = ", ".join(frappe.db.escape(c) for c in service_companies)
        # Filter via service_request's company
        return (
            f"`tabJob Assignment`.`service_request` IN "
            f"(SELECT `name` FROM `tabService Request` WHERE `company` IN ({escaped_companies}))"
        )

    # Non-manager: own assignments only
    ue = frappe.db.escape(user)
    return (
        f"(`tabJob Assignment`.`owner` = {ue} "
        f"OR `tabJob Assignment`.`user` = {ue})"
    )


def has_job_assignment_permission(doc=None, user=None, permission_type=None):
    user = user or frappe.session.user
    if not _can_access_service_requests(user):
        return False

    if _is_service_manager(user):
        # For managers, company-scope is enforced via query filter
        return True

    if doc is None:
        return True

    owner = doc.get("owner") if hasattr(doc, "get") else None
    assigned_user = doc.get("user") if hasattr(doc, "get") else None
    return user in {owner, assigned_user}
