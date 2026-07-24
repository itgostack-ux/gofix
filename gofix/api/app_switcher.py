"""GoGizmo App Switcher — server-side role check.

Returns the list of custom page names the current user is allowed to access.
"""

import frappe

from gofix.config import get_user_roles, is_privileged_user


# Pages managed by the app switcher
SWITCHER_PAGES = [
	"ch-pos-app",
	"gofix-ops-hub",
	"purchase-hub",
	"stock-hub",
	"logistics-control-tower",
    "scheme-hub",
	"ceo-command-center",
	"finance-dashboard",
	"operations-dashboard",
	"compliance-dashboard",
	"store-manager-dashboard",
	"category-manager-dashboard",
	"ch-customer-dashboard",
]


@frappe.whitelist()
def get_allowed_pages() -> dict:
	"""Return page names the current user has permission to view."""
	user_roles = get_user_roles()

	# Fetch all page-role mappings in one query
	page_roles = frappe.get_all(
		"Has Role",
		filters={
			"parenttype": "Page",
			"parent": ["in", SWITCHER_PAGES],
		},
		fields=["parent", "role"],
	)

	# Build page → required roles mapping
	page_role_map = {}
	for pr in page_roles:
		page_role_map.setdefault(pr.parent, set()).add(pr.role)

	allowed = []
	privileged = is_privileged_user()
	for page_name in SWITCHER_PAGES:
		required = page_role_map.get(page_name, set())
		if privileged or (required and user_roles & required):
			allowed.append(page_name)

	return allowed
