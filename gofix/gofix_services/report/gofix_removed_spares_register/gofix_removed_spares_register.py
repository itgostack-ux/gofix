# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt

"""GoFix Removed Spares Register.

Every part taken OUT of a customer device (KBB genealogy): where it was
removed, its serial and condition, and where it physically sits now —
sliceable by location, zone and spare category. Rows with missing details
are flagged so ops can chase them (the close-gate blocks those tickets
anyway)."""

import frappe
from frappe import _


def execute(filters=None):
	filters = frappe._dict(filters or {})
	data = get_data(filters)
	return get_columns(), data, None, get_chart(data), get_summary(data)


def get_columns():
	return [
		{"label": _("Removed At"), "fieldname": "removed_at_location", "fieldtype": "Link", "options": "Warehouse", "width": 190},
		{"label": _("Zone"), "fieldname": "zone", "fieldtype": "Data", "width": 100},
		{"label": _("Ticket"), "fieldname": "service_request", "fieldtype": "Link", "options": "Service Request", "width": 150},
		{"label": _("Date"), "fieldname": "date", "fieldtype": "Date", "width": 95},
		{"label": _("Spare Category"), "fieldname": "spare_category", "fieldtype": "Data", "width": 120},
		{"label": _("Replacement Part"), "fieldname": "spare_item_name", "fieldtype": "Data", "width": 190},
		{"label": _("Removed Serial (Old)"), "fieldname": "removed_part_serial", "fieldtype": "Data", "width": 150},
		{"label": _("Condition"), "fieldname": "removed_part_condition", "fieldtype": "Data", "width": 90},
		{"label": _("Installed Serial (New)"), "fieldname": "installed_part_serial", "fieldtype": "Data", "width": 150},
		{"label": _("Current Location"), "fieldname": "current_location", "fieldtype": "Data", "width": 190},
		{"label": _("Device"), "fieldname": "device", "fieldtype": "Data", "width": 170},
		{"label": _("Customer"), "fieldname": "customer_name", "fieldtype": "Data", "width": 130},
	]


def _effective_warehouse(row):
	if row.transfer_status in ("In Transit", "Received at Service Center") and row.transferred_to_store:
		return row.transferred_to_store
	return row.current_location or row.source_warehouse


def get_data(filters):
	conditions = ["sl.status = 'Consumed'"]
	values = {}
	if filters.get("company"):
		conditions.append("sr.company = %(company)s")
		values["company"] = filters.company
	if filters.get("from_date"):
		conditions.append("DATE(sl.modified) >= %(from_date)s")
		values["from_date"] = filters.from_date
	if filters.get("to_date"):
		conditions.append("DATE(sl.modified) <= %(to_date)s")
		values["to_date"] = filters.to_date
	if filters.get("condition"):
		conditions.append("sl.removed_part_condition = %(condition)s")
		values["condition"] = filters.condition
	if filters.get("missing_only"):
		conditions.append(
			"(IFNULL(sl.removed_part_serial, '') = '' OR IFNULL(sl.removed_part_condition, '') = '')"
		)

	rows = frappe.db.sql(
		f"""
		SELECT
			sl.name AS line, sl.spare_item, sl.item_name AS spare_item_name,
			sl.removed_part_serial, sl.installed_part_serial, sl.removed_part_condition,
			DATE(sl.modified) AS date,
			sr.name AS service_request, sr.customer_name,
			COALESCE(NULLIF(sr.device_item_name, ''), sr.device_item) AS device,
			sr.source_warehouse, sr.current_location, sr.transferred_to_store,
			sr.transfer_status, sr.company,
			i.ch_category AS spare_category
		FROM `tabSR Spare Line` sl
		JOIN `tabService Request` sr ON sr.name = sl.parent AND sl.parenttype = 'Service Request'
		LEFT JOIN `tabItem` i ON i.name = sl.spare_item
		WHERE {" AND ".join(conditions)}
		ORDER BY sr.source_warehouse, sl.modified DESC
		""",
		values,
		as_dict=True,
	)

	store_cache = {}

	def store_info(warehouse):
		if warehouse not in store_cache:
			store_cache[warehouse] = frappe.db.get_value(
				"CH Store", {"warehouse": warehouse}, ["zone", "city"], as_dict=True
			) or frappe._dict()
		return store_cache[warehouse]

	out = []
	for r in rows:
		removed_at = _effective_warehouse(r)
		info = store_info(r.source_warehouse)
		r.removed_at_location = removed_at
		r.zone = info.get("zone") or ""
		# The pulled part travels with the ticket: it sits wherever the
		# repair is now happening until recovery/return disposition.
		if r.removed_part_condition == "Scrap":
			r.current_location = _("Scrapped")
		elif r.removed_part_condition == "Faulty":
			r.current_location = f"{removed_at} — {_('awaiting supplier return')}"
		else:
			r.current_location = removed_at
		if not (r.removed_part_serial or "").strip():
			r.removed_part_serial = f"⚠ {_('missing')}"
		if not (r.removed_part_condition or "").strip():
			r.removed_part_condition = f"⚠ {_('missing')}"
		if filters.get("location") and removed_at != filters.location:
			continue
		if filters.get("zone") and r.zone != filters.zone:
			continue
		if filters.get("spare_category") and r.spare_category != filters.spare_category:
			continue
		out.append(r)
	return out


def get_summary(data):
	missing = sum(1 for r in data if "⚠" in (r.removed_part_serial or "") or "⚠" in (r.removed_part_condition or ""))
	faulty = sum(1 for r in data if r.removed_part_condition == "Faulty")
	return [
		{"label": _("Removed Parts"), "value": len(data), "datatype": "Int"},
		{"label": _("Details Missing"), "value": missing, "datatype": "Int", "indicator": "Red" if missing else "Green"},
		{"label": _("Faulty (Supplier Return Due)"), "value": faulty, "datatype": "Int", "indicator": "Orange"},
	]


def get_chart(data):
	by_loc = {}
	for r in data:
		by_loc[r.removed_at_location or "—"] = by_loc.get(r.removed_at_location or "—", 0) + 1
	labels = sorted(by_loc)
	return {
		"data": {"labels": labels, "datasets": [{"name": _("Removed Parts"), "values": [by_loc[l] for l in labels]}]},
		"type": "bar",
	}
