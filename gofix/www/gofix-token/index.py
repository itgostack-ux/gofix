"""GoFix self check-in tablet page context.

Accessible at ``/gofix-token?store=<CH Store name | store_code | POS Profile |
Warehouse>``. No login required — the create_tablet_token API in ch_pos is
guest-allowed and rate-limited per store. The token it creates is the same
POS Kiosk Token the counter logs, so tablet and counter share one queue.
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
			from ch_pos.api.token_api import (
				_company_is_gofix_enabled,
				_resolve_pos_profile,
				_store_identity,
			)

			profile = _resolve_pos_profile(store_param)
			if profile and _company_is_gofix_enabled(profile.company):
				code, name = _store_identity(profile)
				context.store_resolved = {
					"warehouse": profile.warehouse,
					"company": profile.company,
					"store_code": code,
					"store_name": name,
					"pos_profile": profile.name,
				}
		except Exception:
			context.store_resolved = None
