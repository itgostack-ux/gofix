"""GoFix management dashboard entrypoint.

Thin redirect to the shared ``/queue-mgmt`` page in ``ch_pos``. Walk-in
tokens from the self check-in tablet and the counter are one POS Kiosk
Token doctype, so there is a single backend and nothing to toggle.

Accessible at ``/gofix-mgmt`` (with optional ``?store=<CH Store name>``).
Requires login — the dashboard talks to FDE/store-manager endpoints.
"""

from __future__ import annotations

from urllib.parse import urlencode

import frappe


no_cache = 1


def get_context(context):
	if frappe.session.user == "Guest":
		frappe.throw(
			"You must be logged in to access the GoFix management dashboard.",
			frappe.PermissionError,
		)
	params = {}
	store = (frappe.form_dict.get("store") or "").strip()
	if store:
		params["store"] = store
	frappe.local.flags.redirect_location = "/queue-mgmt" + ("?" + urlencode(params) if params else "")
	raise frappe.Redirect
