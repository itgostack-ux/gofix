# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt

"""GoFix Ticket Stage Time.

Where does a ticket spend its life? One row per ticket, one column per
ops-hub stage with the hours spent there (from GoFix Status Log — the same
data the ticket's Status Timeline shows), the still-ticking time of the
current stage included. Bottleneck column flags the slowest stage per
ticket; the chart shows average hours per stage across the filtered set."""

import frappe
from frappe import _
from frappe.utils import flt, now_datetime, time_diff_in_hours

# Canonical stage order (labels as written by _log_ops_stage)
STAGES = [
	("Draft", _("Intake (h)")),
	("Analysis", _("Analysis (h)")),
	("Customer Confirmation", _("Confirm (h)")),
	("Solution Assignment", _("Solutions (h)")),
	("Technician Assignment", _("Assign (h)")),
	("Repair", _("Repair (h)")),
	("Quality Control", _("QC (h)")),
	("Rework", _("Rework (h)")),
	("Invoice", _("Invoice (h)")),
]
_ALIAS = {"draft": "Draft", "intake": "Draft"}


def execute(filters=None):
	filters = frappe._dict(filters or {})
	data = get_data(filters)
	return get_columns(), data, None, get_chart(data), get_summary(data)


def get_columns():
	cols = [
		{"label": _("Ticket"), "fieldname": "ticket", "fieldtype": "Link", "options": "Service Request", "width": 150},
		{"label": _("Customer"), "fieldname": "customer_name", "fieldtype": "Data", "width": 130},
		{"label": _("Device"), "fieldname": "device", "fieldtype": "Data", "width": 160},
		{"label": _("Store"), "fieldname": "store", "fieldtype": "Link", "options": "Warehouse", "width": 150},
		{"label": _("Technicians"), "fieldname": "technicians", "fieldtype": "Data", "width": 160},
		{"label": _("Current Stage"), "fieldname": "current_stage", "fieldtype": "Data", "width": 110},
	]
	for key, label in STAGES:
		cols.append({"label": label, "fieldname": frappe.scrub(key), "fieldtype": "Float", "width": 90, "precision": 1})
	cols += [
		{"label": _("Total TAT (h)"), "fieldname": "total_hours", "fieldtype": "Float", "width": 100, "precision": 1},
		{"label": _("Bottleneck"), "fieldname": "bottleneck", "fieldtype": "Data", "width": 150},
	]
	return cols


def _stage_key(label):
	label = (label or "").strip()
	return _ALIAS.get(label.lower(), label)


def get_data(filters):
	sr_filters = {"docstatus": 1}
	if filters.get("service_request"):
		# An explicit SR number wins — don't let the date-range default hide it
		sr_filters["name"] = filters.service_request
	else:
		if filters.get("company"):
			sr_filters["company"] = filters.company
		if filters.get("store"):
			sr_filters["source_warehouse"] = filters.store
		if filters.get("from_date"):
			sr_filters["service_date"] = (">=", filters.from_date)
		if filters.get("to_date") and filters.get("from_date"):
			sr_filters["service_date"] = ("between", [filters.from_date, filters.to_date])
		elif filters.get("to_date"):
			sr_filters["service_date"] = ("<=", filters.to_date)
		if filters.get("open_only"):
			sr_filters["decision"] = ("not in", ["Closed", "Cancelled", "Rejected", "Expired"])

	tickets = frappe.get_all(
		"Service Request",
		filters=sr_filters,
		fields=["name", "customer_name", "device_item_name", "device_item",
			"source_warehouse", "creation", "decision"],
		order_by="service_date desc",
		limit=500,
	)
	if not tickets:
		return []
	names = [t.name for t in tickets]

	logs = frappe.get_all(
		"GoFix Status Log",
		filters={"parent": ("in", names), "parenttype": "Service Request"},
		fields=["parent", "from_status", "to_status", "changed_at", "time_in_previous_status_hours"],
		order_by="changed_at asc",
	)
	log_map = {}
	for row in logs:
		log_map.setdefault(row.parent, []).append(row)

	techs = frappe.get_all(
		"SR Solution Line",
		filters={"parent": ("in", names), "technician": ("!=", "")},
		fields=["parent", "technician_name", "technician"],
	)
	tech_map = {}
	for row in techs:
		tech_map.setdefault(row.parent, set()).add(row.technician_name or row.technician)

	stage_fields = {key: frappe.scrub(key) for key, _l in STAGES}
	out = []
	for t in tickets:
		if filters.get("technician"):
			tech_emp = frappe.db.get_value("Employee", filters.technician, "employee_name")
			if not {tech_emp, filters.technician} & tech_map.get(t.name, set()):
				continue
		rows = log_map.get(t.name, [])
		hours = {key: 0.0 for key in stage_fields}
		# Time before the first transition = intake/draft
		if rows:
			hours["Draft"] += max(flt(time_diff_in_hours(rows[0].changed_at, t.creation)), 0)
		for row in rows:
			key = _stage_key(row.from_status)
			if key in hours and flt(row.time_in_previous_status_hours) > 0:
				hours[key] += flt(row.time_in_previous_status_hours)
		# The current stage is still ticking
		current = _stage_key(rows[-1].to_status) if rows else "Draft"
		if current in hours and t.decision not in ("Closed", "Cancelled"):
			last_at = rows[-1].changed_at if rows else t.creation
			hours[current] += max(flt(time_diff_in_hours(now_datetime(), last_at)), 0)

		total = sum(hours.values())
		bottleneck = max(hours, key=hours.get) if total else ""
		rec = {
			"ticket": t.name,
			"customer_name": t.customer_name,
			"device": t.device_item_name or t.device_item,
			"store": t.source_warehouse,
			"technicians": ", ".join(sorted(tech_map.get(t.name, []))) or "—",
			"current_stage": current,
			"total_hours": round(total, 1),
			"bottleneck": f"{bottleneck} ({round(hours[bottleneck], 1)}h)" if bottleneck else "",
		}
		for key, field in stage_fields.items():
			rec[field] = round(hours[key], 1)
		out.append(rec)
	return out


def get_chart(data):
	if not data:
		return None
	labels, values = [], []
	for key, label in STAGES:
		field = frappe.scrub(key)
		vals = [r[field] for r in data if r.get(field)]
		labels.append(str(label).replace(" (h)", ""))
		values.append(round(sum(vals) / len(vals), 1) if vals else 0)
	return {
		"data": {"labels": labels, "datasets": [{"name": _("Avg hours in stage"), "values": values}]},
		"type": "bar",
		"colors": ["#7c3aed"],
	}


def get_summary(data):
	if not data:
		return []
	avg_tat = round(sum(r["total_hours"] for r in data) / len(data), 1)
	stage_totals = {}
	for key, _l in STAGES:
		field = frappe.scrub(key)
		stage_totals[key] = sum(r.get(field) or 0 for r in data)
	slowest = max(stage_totals, key=stage_totals.get)
	return [
		{"label": _("Tickets"), "value": len(data), "datatype": "Int"},
		{"label": _("Avg TAT (h)"), "value": avg_tat, "datatype": "Float"},
		{"label": _("Slowest Stage (overall)"), "value": f"{slowest} — {round(stage_totals[slowest], 1)}h", "datatype": "Data", "indicator": "Orange"},
	]
