"""GoFix Token — Management Dashboard entrypoint.

Thin redirect that opens the shared ``/queue-mgmt`` page with the GoFix
backend toggle. The HTML/CSS/JS lives in ``ch_pos`` (single source of
truth) and reads ``?system=gofix`` to route API calls to
``gofix.api.token_api`` instead of ``ch_pos.api.token_api``.

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
	params = {"system": "gofix"}
	store = (frappe.form_dict.get("store") or "").strip()
	if store:
		params["store"] = store
	frappe.local.flags.redirect_location = "/queue-mgmt?" + urlencode(params)
	raise frappe.Redirect
