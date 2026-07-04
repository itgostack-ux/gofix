# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt

"""Custom fields for Company that scope brand-level conventions.

Two fields are managed here:

* ``gofix_enabled`` (Check) — flags a tenant Company as a GoFix operator so
  the ``/gofix-token`` self-serve tablet, GoFix Token DocType and dashboard
  endpoints will accept its stores. Retained for backwards compatibility
  with existing seed / patch logic that keys off this flag.

* ``store_code_prefix`` (Data, 2 chars) — the brand prefix that CH Store
  autoname stamps onto new store codes for this company. Mirrors the
  existing conventions in production:

    * BestBuy Mobiles (abbr BM, ex-BMPL) → ``GG`` (Gogizmo brand)
    * GoFix Solutions  (abbr GF, ex-GSPL) → ``GF``

  When set, a new CH Store under this company auto-names to
  ``<PREFIX>-<SLUG_OF_STORE_NAME>``. When blank, falls back to the generic
  ``STO-<ABBR>-<CITY>-####`` scheme.
"""

from __future__ import annotations

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


# Historical (pre-abbr-shortening) values are kept alongside their new
# 2-char forms so that auto-detection works both before and after the
# ``rename_company_abbrs_to_2char`` patch has run on a given site.
_GOFIX_ABBR = {"GSPL", "GF"}
_GOFIX_NAME_HINT = "GOFIX"
_BMPL_ABBR = {"BMPL", "BM"}
_BMPL_NAME_HINT = "BESTBUY"


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
			{
				"fieldname": "store_code_prefix",
				"label": "Store Code Prefix",
				"fieldtype": "Data",
				"insert_after": "gofix_enabled",
				"length": 2,
				"description": (
					"Exactly 2 uppercase letters used as the brand prefix when "
					"CH Store auto-names a new store for this company "
					"(e.g. 'GF' → 'GF-ANNANAGAR', 'GG' → 'GG-ASHOKNAGAR'). "
					"Leave blank to fall back to the generic "
					"STO-<ABBR>-<CITY>-#### scheme."
				),
			},
		]
	}
	create_custom_fields(custom_fields, update=True)
	_auto_enable_gofix_companies()
	_backfill_store_code_prefix()


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


def _backfill_store_code_prefix() -> None:
	"""Seed ``store_code_prefix`` for the canonical production companies.

	Only writes when the field is currently empty, so any manual override
	from ops sticks. Matches by both the historical 4-char abbr and the
	new 2-char abbr so it works on sites that haven't run the abbr rename
	patch yet.
	"""

	if not frappe.db.has_column("Company", "store_code_prefix"):
		return

	rows = frappe.get_all(
		"Company",
		fields=["name", "abbr", "store_code_prefix"],
	)
	for row in rows:
		if (row.get("store_code_prefix") or "").strip():
			continue
		abbr_up = (row.get("abbr") or "").upper()
		name_up = (row.get("name") or "").upper()
		prefix = None
		if abbr_up in _GOFIX_ABBR or _GOFIX_NAME_HINT in name_up:
			prefix = "GF"
		elif abbr_up in _BMPL_ABBR or _BMPL_NAME_HINT in name_up:
			prefix = "GG"
		if not prefix:
			continue
		frappe.db.set_value(
			"Company",
			row["name"],
			"store_code_prefix",
			prefix,
			update_modified=False,
		)
		frappe.logger("gofix").info(
			f"[store_code_prefix] set '{prefix}' for Company {row['name']}"
		)
