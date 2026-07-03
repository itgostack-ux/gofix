"""GoFix Token — Self-Serve Customer Tablet page context.

Accessible at ``/gofix-token?store=<CH Store name | store_code | POS Profile |
Warehouse>``. No login required — the create_token API is guest-allowed and
rate-limited per store.
"""

from __future__ import annotations

import frappe

no_cache = 1


def get_context(context):
	context.no_cache = 1
	context.no_breadcrumbs = 1
	context.title = "GoFix — Self Check-In"
	try:
		from frappe.sessions import get_csrf_token

		context.csrf_token = get_csrf_token()
	except Exception:
		context.csrf_token = "fetch"
	# Pre-resolve the store server-side so the tablet fails fast (blank screen
	# with a clear message) instead of loading and then erroring on the first
	# XHR. Keeps the page usable even if the API is temporarily unreachable.
	store_param = (frappe.form_dict.get("store") or "").strip()
	context.store_param = store_param
	context.store_resolved = None
	if store_param:
		try:
			from gofix.api.token_api import _resolve_store

			context.store_resolved = _resolve_store(store_param)
		except Exception:
			context.store_resolved = None
