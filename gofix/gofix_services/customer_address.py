# Copyright (c) 2026, GoFix and contributors
# Server-side helpers for CH Customer Address management.

import frappe
from frappe import _


def validate_single_active_address(doc, method=None):
	"""Enforce single active address per type (Billing / Shipping / Both) on Customer save.

	If two rows of the same type are both marked is_active=1 this raises a clear error
	rather than silently accepting invalid state.
	"""
	billing_addresses = doc.get("billing_addresses") or []
	if not billing_addresses:
		return

	active_billing = []
	active_shipping = []

	for row in billing_addresses:
		if not row.get("is_active"):
			continue
		rtype = row.get("address_type") or "Billing"
		if rtype in ("Billing", "Both"):
			active_billing.append(row.address_line1 or row.name)
		if rtype in ("Shipping", "Both"):
			active_shipping.append(row.address_line1 or row.name)

	if len(active_billing) > 1:
		frappe.throw(
			_("Only one Billing address can be marked Active at a time. "
			  "Currently active: {0}").format(", ".join(active_billing)),
			title=_("Duplicate Active Billing Address"),
		)

	if len(active_shipping) > 1:
		frappe.throw(
			_("Only one Shipping address can be marked Active at a time. "
			  "Currently active: {0}").format(", ".join(active_shipping)),
			title=_("Duplicate Active Shipping Address"),
		)
