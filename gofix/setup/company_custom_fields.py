# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt

"""Custom fields for Company that scope GoFix Token to specific tenants.

Adds a single ``gofix_enabled`` Check field. When set, that company's
warehouses (via CH Store or POS Profile) can be used as the ``store``
parameter for the GoFix tablet at ``/gofix-token`` and its APIs.

Also seeds the flag automatically for any company whose name or abbr
matches the GoFix pattern so a fresh install works without a manual
toggle.
"""

from __future__ import annotations

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


_GOFIX_ABBR = {"GSPL"}
_GOFIX_NAME_HINT = "GOFIX"


def create_company_custom_fields() -> None:
	"""Idempotent — safe to run on every ``after_migrate``."""

	custom_fields = {
		"Company": [
			{
				"fieldname": "gofix_enabled",
				"label": "GoFix Token Enabled",
				"fieldtype": "Check",
				"insert_after": "abbr",
				"default": "0",
				"description": (
					"Allow this company's stores to be used for the GoFix self-serve "
					"tablet and token queue. Auto-enabled for the GoFix operating "
					"company; other companies default to disabled."
				),
			},
		]
	}
	create_custom_fields(custom_fields, update=True)
	_auto_enable_gofix_companies()


def _auto_enable_gofix_companies() -> None:
	"""Turn on gofix_enabled for companies whose abbr or name look GoFix.

	Ops can override in either direction after this runs — the patch only
	writes when the value is currently unset or 0, so a deliberate disable
	sticks.
	"""

	if not frappe.db.has_column("Company", "gofix_enabled"):
		return
	rows = frappe.get_all(
		"Company",
		fields=["name", "abbr"],
	)
	for row in rows:
		if (row.get("abbr") or "").upper() in _GOFIX_ABBR or _GOFIX_NAME_HINT in (
			row.get("name") or ""
		).upper():
			current = frappe.db.get_value("Company", row["name"], "gofix_enabled")
			if not current:
				frappe.db.set_value("Company", row["name"], "gofix_enabled", 1)
				frappe.logger("gofix").info(
					f"[gofix_enabled] auto-enabled for Company {row['name']}"
				)
