import frappe

SERVICE_ROLES = {"Service Manager", "Service Engineer", "Service Viewer"}


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


def _can_access_service_requests(user=None):
    user = user or frappe.session.user
    if not user or user == "Administrator":
        return True
    try:
        roles = set(frappe.get_roles(user))
    except Exception:
        roles = set()
    if "System Manager" in roles or roles.intersection(SERVICE_ROLES):
        return True

    allowed_companies = get_user_allowed_companies(user) or []
    return any(_is_service_company(company) for company in allowed_companies)


def get_service_request_query(user=None):
    user = user or frappe.session.user
    if not _can_access_service_requests(user):
        return "1=0"

    allowed_companies = get_user_allowed_companies(user)
    if not allowed_companies:
        return None

    service_companies = [company for company in allowed_companies if _is_service_company(company)]
    if not service_companies:
        return "1=0"

    escaped = ", ".join(frappe.db.escape(company) for company in service_companies)
    return f"`tabService Request`.`company` in ({escaped})"


def has_service_request_permission(doc=None, user=None, permission_type=None):
    user = user or frappe.session.user
    if not _can_access_service_requests(user):
        return False

    allowed_companies = get_user_allowed_companies(user)
    if not allowed_companies or doc is None:
        return True

    service_companies = {company for company in allowed_companies if _is_service_company(company)}
    if not service_companies:
        return False

    company = doc.get("company") if hasattr(doc, "get") else None
    if not company:
        return True

    return company in service_companies
