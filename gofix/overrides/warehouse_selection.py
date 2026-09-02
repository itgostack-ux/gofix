# Copyright (c) 2026, GoFix and contributors

"""Keep Customer Device custody bins out of every warehouse picker.

A customer's handset under repair sits in a per-store ``Customer Device``
bin (see gofix.customer_device_stock). That bin is plumbing: intake,
transfers and release write to it server-side, but no human should ever
*choose* it on a document — selecting it on a Stock Entry, Delivery Note
or Material Request would mix custody stock into retail movements.

Two server-side choke points cover the Desk link fields:

1. ``standard_queries["Warehouse"]`` (hooks.py) routes every Warehouse
   link field that has no explicit ``query`` through
   :func:`warehouse_link_query` — the stock link search minus custody
   bins.

2. Fields that pass ``query: "erpnext.controllers.queries.warehouse_query"``
   (every stock items grid once an item is chosen, the serial/batch
   selector) bypass standard_queries, and link search also bypasses the
   ``override_whitelisted_methods`` hook — so :func:`ensure_patched`
   (wired to ``before_request``) wraps that function in place with the
   same exclusion.

The exclusion filters by name (``%-CustomerDevice%``), the same test
``is_customer_device_bin()`` applies at runtime; every custody bin also
carries ``ch_bin_type = 'Customer Device'``, but a name match is
NULL-safe in SQL where a ``!=`` on the field is not.
"""

from __future__ import annotations

import frappe
from frappe.utils import cint
from frappe.utils.data import make_filter_tuple

from gofix.customer_device_stock import BIN_SUFFIX

CUSTODY_NAME_LIKE = f"%-{BIN_SUFFIX}%"

_SEARCHABLE_FIELDTYPES = {
	"Autocomplete",
	"Data",
	"Text",
	"Small Text",
	"Long Text",
	"Link",
	"Select",
	"Read Only",
	"Text Editor",
}


def custody_exclusion_row(doctype: str = "Warehouse") -> list:
	"""One filter row that removes custody bins from a Warehouse query."""
	return [doctype, "name", "not like", CUSTODY_NAME_LIKE]


def _as_filter_rows(doctype: str, filters) -> list:
	"""Normalize client filters (dict, list, JSON string, None) to rows."""
	if isinstance(filters, str):
		filters = frappe.parse_json(filters)
	if isinstance(filters, dict):
		return [make_filter_tuple(doctype, key, value) for key, value in filters.items()]
	return list(filters or [])


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def warehouse_link_query(doctype, txt, searchfield, start, page_len, filters, as_dict=False):
	"""The default Warehouse link search, minus custody bins.

	Mirrors frappe.desk.search's non-query path: honors the caller's
	filters, matches txt against name / title / search fields, hides
	disabled warehouses, and enforces read permission via get_list.
	"""
	doctype = "Warehouse"
	meta = frappe.get_meta(doctype)

	filter_rows = _as_filter_rows(doctype, filters)
	filter_rows.append(custody_exclusion_row(doctype))
	filter_rows.append([doctype, "disabled", "!=", 1])

	or_filters = []
	txt = txt or ""
	if txt:
		search_fields = ["name"]
		if meta.title_field:
			search_fields.append(meta.title_field)
		if meta.search_fields:
			search_fields.extend(meta.get_search_fields())
		for field in dict.fromkeys(f.strip() for f in search_fields):
			fmeta = meta.get_field(field)
			if field == "name" or (fmeta and fmeta.fieldtype in _SEARCHABLE_FIELDTYPES):
				or_filters.append([doctype, field, "like", f"%{txt}%"])

	fields = ["name"]
	if meta.show_title_field_in_link and meta.title_field:
		fields.append(f"{meta.title_field} as label")

	return frappe.get_list(
		doctype,
		filters=filter_rows,
		or_filters=or_filters,
		fields=fields,
		limit_start=cint(start),
		limit_page_length=cint(page_len) or 10,
		order_by="idx desc, name asc",
		as_list=not as_dict,
	)


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def warehouse_query_sans_custody(doctype, txt, searchfield, start, page_len, filters):
	"""erpnext.controllers.queries.warehouse_query with custody bins removed."""
	ensure_patched()
	filter_rows = _as_filter_rows("Warehouse", filters)
	filter_rows.append(custody_exclusion_row("Warehouse"))
	return _original_warehouse_query(doctype, txt, searchfield, start, page_len, filter_rows)


_original_warehouse_query = None


def ensure_patched():
	"""Idempotently swap erpnext's warehouse_query for the filtered one.

	Wired to ``before_request``: link search resolves the query string
	with frappe.get_attr, which consults neither standard_queries nor
	override_whitelisted_methods, so an in-place module patch is the only
	server-side seam. Runs once per process; a no-op afterwards.
	"""
	global _original_warehouse_query
	if _original_warehouse_query is not None:
		return
	try:
		from erpnext.controllers import queries as erpnext_queries
	except Exception:
		return
	_original_warehouse_query = erpnext_queries.warehouse_query
	erpnext_queries.warehouse_query = warehouse_query_sans_custody
