# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt

"""Company fields for explicitly configured GoFix operations.

Two fields are managed here:

* ``gofix_enabled`` (Check) — flags a tenant Company as a GoFix operator so
  the ``/gofix-token`` self-serve tablet, GoFix Token DocType and dashboard
  endpoints will accept its stores.

* ``store_code_prefix`` (Data, 2 chars) — the configured brand prefix that
  CH Store autoname stamps onto new store codes. When set, a new store auto-names to
  ``<PREFIX>-<SLUG_OF_STORE_NAME>``. When blank, falls back to the generic
  ``STO-<ABBR>-<CITY>-####`` scheme.
"""

from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


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
					"tablet and token queue. Enable this explicitly for each operating company."
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
					"(for example, 'SV' → 'SV-CENTRAL'). "
					"Leave blank to fall back to the generic "
					"STO-<ABBR>-<CITY>-#### scheme."
				),
			},
			{
				"fieldname": "gofix_default_service_item",
				"label": "Default Repair Service Item",
				"fieldtype": "Link",
				"options": "Item",
				"insert_after": "store_code_prefix",
				"description": (
					"Item the Service Order bills a repair against when the "
					"Service Request names no service item of its own. Must be a "
					"non-stock sales Item that is not a variant template."
				),
			},
		]
	}
	create_custom_fields(custom_fields, update=True)
