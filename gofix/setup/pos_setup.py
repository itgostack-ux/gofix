# Copyright (c) 2026, GoFix and contributors

"""Idempotent POS operating-date setup for GoFix-enabled companies."""

import frappe
from frappe.utils import nowdate


def ensure_gofix_business_dates() -> None:
	"""Open an initial business date for every configured active GoFix POS store."""
	from ch_pos.pos_core.doctype.ch_business_date.ch_business_date import (
		advance_business_date,
	)

	stores = frappe.db.sql(
		"""
		SELECT s.name
		  FROM `tabCH Store` s
		  JOIN `tabCompany` c ON c.name = s.company AND c.gofix_enabled = 1
		  JOIN `tabPOS Profile` pp ON pp.name = s.pos_profile
		 WHERE s.disabled = 0 AND s.store_status = 'Active'
		   AND COALESCE(s.warehouse, '') != ''
		 ORDER BY s.name
		""",
		pluck=True,
	)
	for store in stores:
		if not frappe.db.exists("CH Business Date", store):
			advance_business_date(
				store,
				nowdate(),
				reason="Initial GoFix POS production setup",
				manager_user="Administrator",
			)
