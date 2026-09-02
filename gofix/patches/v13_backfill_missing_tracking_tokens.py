# Copyright (c) 2026, GoStack and contributors

"""Mint the tracking tokens the permlevel wipe silently ate.

``tracking_token``/``tracking_token_salt`` are permlevel-1 fields; core resets
permlevel-1 values on insert for any creator without a manager role, so every
Service Request keyed in by an ordinary front-desk user was born with neither —
and the customer's public /track link (sent on WhatsApp) never worked. The
controller now mints both via ``db_set``; this heals the rows created while the
wipe was live. Idempotent — rows that already carry a token are untouched.
"""

import frappe


def execute():
	if not frappe.db.has_column("Service Request", "tracking_token"):
		return
	from gofix.tracking import derive_tracking_token, make_tracking_salt, tracking_token_digest

	names = frappe.get_all(
		"Service Request",
		filters={"tracking_token": ("in", ("", None)), "docstatus": ("<", 2)},
		pluck="name",
	)
	for name in names:
		salt = frappe.db.get_value("Service Request", name, "tracking_token_salt")
		if not salt:
			salt = make_tracking_salt()
			frappe.db.set_value("Service Request", name, "tracking_token_salt", salt,
			                    update_modified=False)
		frappe.db.set_value(
			"Service Request", name, "tracking_token",
			tracking_token_digest(derive_tracking_token(name, salt)),
			update_modified=False,
		)
	frappe.db.commit()
	frappe.logger("gofix").info(f"GoFix: minted tracking tokens for {len(names)} service request(s)")
