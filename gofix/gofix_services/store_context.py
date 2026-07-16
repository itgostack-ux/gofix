"""Shared GoFix company/store context helpers for pages and reports."""

from __future__ import annotations

import frappe
from frappe.utils import cint


def active_company(company: str | None = None) -> str:
	"""Resolve active company from explicit page/report state, then defaults."""
	return (
		(company or "").strip()
		or frappe.defaults.get_user_default("Company")
		or frappe.defaults.get_user_default("company")
		or frappe.db.get_single_value("Global Defaults", "default_company")
		or ""
	)


def _user_scope():
	try:
		from gofix.scope_guard import user_scope
	except ImportError:
		return set(), set(), True
	return user_scope()


def get_store_options(company: str | None = None) -> list[dict]:
	"""Return clean business stores for the selected company.

	The value is always the actual Warehouse used by transactions, but the
	option is sourced from CH Store so internal bins such as Buyback, Demo and
	Damaged do not show up as selectable stores.
	"""
	company = active_company(company)
	allowed_wh, allowed_co, bypass = _user_scope()

	if not bypass:
		if company and allowed_co and company not in allowed_co:
			return []
		if not allowed_wh:
			return []

	if frappe.db.table_exists("CH Store"):
		filters = {"disabled": 0}
		if company:
			filters["company"] = company
		if not bypass:
			filters["warehouse"] = ["in", list(allowed_wh)]
		if frappe.db.has_column("CH Store", "store_status"):
			filters["store_status"] = ["!=", "Closed"]

		stores = frappe.get_all(
			"CH Store",
			filters=filters,
			fields=["name", "store_code", "store_name", "company", "warehouse", "city"],
			order_by="store_code asc, store_name asc",
		)
		options = [_store_option(row) for row in stores if row.warehouse]
		if options:
			return options

	wh_filters = {"is_group": 0, "disabled": 0}
	if company:
		wh_filters["company"] = company
	if not bypass:
		wh_filters["name"] = ["in", list(allowed_wh)]

	warehouses = frappe.get_all(
		"Warehouse",
		filters=wh_filters,
		fields=["name", "warehouse_name", "company"],
		order_by="name",
	)
	sellable = [wh for wh in warehouses if "-Sellable" in wh.name]
	if sellable:
		warehouses = sellable

	return [
		{
			"value": wh.name,
			"warehouse": wh.name,
			"store": "",
			"store_code": wh.name.split(" - ")[0],
			"store_name": wh.warehouse_name or wh.name.split(" - ")[0],
			"label": wh.name.split(" - ")[0],
			"company": wh.company,
			"city": "",
		}
		for wh in warehouses
	]


def _store_option(row) -> dict:
	store_code = row.store_code or row.name
	store_name = row.store_name or store_code
	return {
		"value": row.warehouse,
		"warehouse": row.warehouse,
		"store": row.name,
		"store_code": store_code,
		"store_name": store_name,
		"label": store_code,
		"company": row.company,
		"city": row.city,
	}


def build_store_context(company: str | None = None, prefer_first: bool = False) -> dict:
	company = active_company(company)
	stores = get_store_options(company)
	warehouses = [row["warehouse"] for row in stores]
	default_warehouse = frappe.defaults.get_user_default("warehouse") or ""
	if default_warehouse not in warehouses:
		default_warehouse = warehouses[0] if prefer_first and warehouses else ""

	return {
		"user": frappe.session.user,
		"user_fullname": frappe.utils.get_fullname(frappe.session.user),
		"company": company,
		"default_warehouse": default_warehouse,
		"stores": stores,
		"warehouses": warehouses,
	}


@frappe.whitelist()
def get_store_context(company: str | None = None, prefer_first: int | str = 0) -> dict:
	return build_store_context(company=company, prefer_first=bool(cint(prefer_first)))


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def warehouse_query(doctype, txt, searchfield, start, page_len, filters):
	"""Link query returning only clean store warehouses for the active company."""
	filters = filters or {}
	company = filters.get("company")
	query = (txt or "").strip().lower()
	options = get_store_options(company)

	matches = []
	for row in options:
		haystack = " ".join(
			str(row.get(k) or "")
			for k in ("warehouse", "store_code", "store_name", "label")
		).lower()
		if query and query not in haystack:
			continue
		matches.append((row["warehouse"], row.get("store_code") or "", row.get("store_name") or ""))

	start = cint(start)
	page_len = cint(page_len) or 20
	return matches[start:start + page_len]
