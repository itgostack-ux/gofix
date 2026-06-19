# Copyright (c) 2026, GoFix and contributors
# "Track My Repair" — public customer-facing page, no login required.
# Accessed via /track-repair?token=<stored-token> or /track-repair?sr=<SR-NAME>&phone=<last4>

import frappe

from gofix.tracking import _get_by_phone, _get_by_token

no_cache = 1
sitemap = 0


def get_context(context):
	"""Render the tracking page based on token or SR+phone verification."""
	context.no_cache = 1
	context.show_sidebar = False
	context.title = "Track My Repair — GoFix"

	token = frappe.form_dict.get("token")
	sr_name = frappe.form_dict.get("sr")
	phone_last4 = frappe.form_dict.get("phone")

	tracking_data = None

	if token:
		tracking_data = _get_by_token(token)
	elif sr_name and phone_last4:
		tracking_data = _get_by_phone(sr_name, phone_last4)

	context.tracking_data = tracking_data
	context.sr_name = sr_name or (tracking_data.get("name") if tracking_data else "")
	context.error = None if tracking_data else "not_found"
