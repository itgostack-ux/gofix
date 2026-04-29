"""GoGizmo App Switcher — server-side role check.

Returns the list of custom page names the current user is allowed to access.
"""

import frappe


# Pages managed by the app switcher
SWITCHER_PAGES = [
	"ch-pos-app",
	"gofix-ops-hub",
	"purchase-hub",
	"stock-hub",
	"logistics-hub",
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
	user_roles = set(frappe.get_roles())

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
	for page_name in SWITCHER_PAGES:
		required = page_role_map.get(page_name, set())
		# Page with no roles defined = accessible to all
		# Page with roles = user must have at least one matching role
		if not required or user_roles & required:
			allowed.append(page_name)

	return allowed
