# Copyright (c) 2026, GoFix and contributors
# Device 360 / Service History Report
#
# Full lifecycle view per IMEI/serial:
#   - All past repairs
#   - Warranty history
#   - Parts replaced
#   - Repeat issue detection
#   - Risk score

import frappe
from frappe import _
from frappe.utils import flt, getdate, nowdate, date_diff

from ch_erp15.ch_erp15.report_scope import scope_where_clause


def execute(filters=None):
	columns = get_columns()
	data = get_data(filters)
	chart = get_chart(data)
	report_summary = get_summary(data)
	return columns, data, None, chart, report_summary


def get_columns():
	return [
		{"fieldname": "serial_no", "label": _("Serial No / IMEI"), "fieldtype": "Link", "options": "Serial No", "width": 160},
		{"fieldname": "device_item", "label": _("Device"), "fieldtype": "Link", "options": "Item", "width": 180},
		{"fieldname": "brand", "label": _("Brand"), "fieldtype": "Data", "width": 100},
		{"fieldname": "customer", "label": _("Customer"), "fieldtype": "Link", "options": "Customer", "width": 150},
		{"fieldname": "total_repairs", "label": _("Total Repairs"), "fieldtype": "Int", "width": 100},
		{"fieldname": "total_cost", "label": _("Total Repair Cost"), "fieldtype": "Currency", "width": 130},
		{"fieldname": "total_spares", "label": _("Total Spares Used"), "fieldtype": "Int", "width": 110},
		{"fieldname": "repeat_issues", "label": _("Repeat Issues"), "fieldtype": "Int", "width": 100},
		{"fieldname": "last_repair_date", "label": _("Last Repair"), "fieldtype": "Date", "width": 110},
		{"fieldname": "warranty_status", "label": _("Warranty"), "fieldtype": "Data", "width": 120},
		{"fieldname": "risk_score", "label": _("Risk Score"), "fieldtype": "Data", "width": 100},
		{"fieldname": "repair_history", "label": _("Repair History"), "fieldtype": "Small Text", "width": 300},
	]


def get_data(filters):
	conditions = []
	values = {}

	if filters.get("serial_no"):
		conditions.append("sr.serial_no = %(serial_no)s")
		values["serial_no"] = filters["serial_no"]
	if filters.get("customer"):
		conditions.append("sr.customer = %(customer)s")
		values["customer"] = filters["customer"]
	if filters.get("device_item"):
		conditions.append("sr.device_item = %(device_item)s")
		values["device_item"] = filters["device_item"]
	if filters.get("company"):
		conditions.append("sr.company = %(company)s")
		values["company"] = filters["company"]
	if filters.get("from_date"):
		conditions.append("sr.service_date >= %(from_date)s")
		values["from_date"] = filters["from_date"]
	if filters.get("to_date"):
		conditions.append("sr.service_date <= %(to_date)s")
		values["to_date"] = filters["to_date"]

	# Tier 4: fail-closed scope on either Service Request warehouse endpoint.
	scope = scope_where_clause(
		warehouse_field="sr.source_warehouse",
		extra_warehouse_fields=("sr.transferred_to_store",),
	)
	if scope is not None:
		conditions.append(scope)

	where = " AND ".join(conditions) if conditions else "1=1"

	# All service requests grouped by serial_no
	sr_data = frappe.db.sql(
		"""
		SELECT
			sr.serial_no,
			sr.device_item,
			sr.device_item_name,
			sr.brand,
			sr.customer,
			sr.customer_name,
			sr.name as sr_name,
			sr.service_date,
			sr.decision,
			sr.status,
			sr.issue_category,
			sr.estimated_cost,
			sr.warranty_status,
			sr.is_repeat_complaint
		FROM `tabService Request` sr
		WHERE sr.serial_no IS NOT NULL
			AND sr.serial_no != ''
			AND sr.docstatus < 2
			AND """
		+ where
		+ " ORDER BY sr.serial_no, sr.service_date DESC",
		values,
		as_dict=True,
	)

	if not sr_data:
		return []

	# Get spare parts count per SR
	sr_names = list({r.sr_name for r in sr_data})
	spare_counts = {}
	if sr_names:
		spare_data = frappe.db.sql("""
			SELECT parent, COUNT(*) as cnt
			FROM `tabService Request Spare Part`
			WHERE parent IN %(names)s
			GROUP BY parent
		""", {"names": sr_names}, as_dict=True)
		spare_counts = {r.parent: r.cnt for r in spare_data}

	# Also get Spare Parts Usage counts
	spu_counts = {}
	spu_data = frappe.db.sql("""
		SELECT service_request, COUNT(*) as cnt
		FROM `tabSpare Parts Usage`
		WHERE service_request IN %(names)s AND deleted = 0
		GROUP BY service_request
	""", {"names": sr_names}, as_dict=True)
	spu_counts = {r.service_request: r.cnt for r in spu_data}

	# Group by serial
	serial_map = {}
	for r in sr_data:
		sn = r.serial_no
		if sn not in serial_map:
			serial_map[sn] = {
				"serial_no": sn,
				"device_item": r.device_item,
				"device_item_name": r.device_item_name,
				"brand": r.brand,
				"customer": r.customer,
				"customer_name": r.customer_name,
				"repairs": [],
				"warranty_status": r.warranty_status,
			}
		serial_map[sn]["repairs"].append(r)

	data = []
	for sn, info in serial_map.items():
		repairs = info["repairs"]
		total_cost = sum(flt(r.estimated_cost) for r in repairs)
		total_spares = sum(
			spare_counts.get(r.sr_name, 0) + spu_counts.get(r.sr_name, 0)
			for r in repairs
		)
		repeat_issues = sum(1 for r in repairs if r.is_repeat_complaint)

		# Repair history summary
		history_lines = []
		for r in repairs[:5]:  # last 5
			history_lines.append(
				f"{r.service_date} | {r.sr_name} | {r.issue_category or '-'} | {r.decision}"
			)

		# Risk score: higher = more problematic device
		risk = 0
		risk += len(repairs) * 10  # each repair adds 10
		risk += repeat_issues * 25  # repeats are high risk
		if len(repairs) >= 3:
			risk += 20  # frequent flyer penalty
		# Recency: if last repair was recent, risk is higher
		if repairs:
			days_since = date_diff(nowdate(), str(repairs[0].service_date))
			if days_since < 30:
				risk += 15
			elif days_since < 90:
				risk += 5

		risk_label = "Low" if risk < 20 else "Medium" if risk < 50 else "High" if risk < 80 else "Critical"

		data.append({
			"serial_no": sn,
			"device_item": info["device_item"],
			"brand": info["brand"],
			"customer": info["customer"],
			"total_repairs": len(repairs),
			"total_cost": total_cost,
			"total_spares": total_spares,
			"repeat_issues": repeat_issues,
			"last_repair_date": str(repairs[0].service_date) if repairs else "",
			"warranty_status": info["warranty_status"] or "",
			"risk_score": f"{risk_label} ({risk})",
			"repair_history": "\n".join(history_lines),
		})

	# Sort by risk score descending
	data.sort(key=lambda x: int(x["risk_score"].split("(")[1].rstrip(")")), reverse=True)
	return data


def get_chart(data):
	if not data:
		return None

	risk_counts = {"Low": 0, "Medium": 0, "High": 0, "Critical": 0}
	for row in data:
		label = row["risk_score"].split(" (")[0]
		risk_counts[label] = risk_counts.get(label, 0) + 1

	return {
		"data": {
			"labels": list(risk_counts.keys()),
			"datasets": [{"name": "Devices", "values": list(risk_counts.values())}],
		},
		"type": "bar",
		"colors": ["#28a745", "#ffc107", "#fd7e14", "#dc3545"],
	}


def get_summary(data):
	if not data:
		return []

	total_devices = len(data)
	total_repairs = sum(r["total_repairs"] for r in data)
	total_cost = sum(r["total_cost"] for r in data)
	repeat_devices = sum(1 for r in data if r["repeat_issues"] > 0)

	return [
		{"value": total_devices, "label": _("Unique Devices"), "datatype": "Int"},
		{"value": total_repairs, "label": _("Total Repairs"), "datatype": "Int"},
		{"value": total_cost, "label": _("Total Cost"), "datatype": "Currency", "currency": "INR"},
		{"value": repeat_devices, "label": _("Repeat Issue Devices"), "datatype": "Int", "indicator": "red" if repeat_devices else "green"},
	]
