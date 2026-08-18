# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt

"""GoFix Ticket Stage Time.

Where does a ticket spend its life? One row per ticket, one column per
ops-hub stage with the hours spent there (from GoFix Status Log — the same
data the ticket's Status Timeline shows), the still-ticking time of the
current stage included — PLUS the spare-procurement chain (MR → PO →
Receipt) and the device's store↔hub logistics leg, so the full end-to-end
timeline is one row. Bottleneck flags the slowest leg per ticket; the
chart shows average hours per leg across the filtered set."""

import frappe
from ch_erp15.ch_erp15.report_scope import get_scoped_warehouses_or_none
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

# Parallel legs: spare procurement (MR raised → PO placed → goods received)
# and the device's store↔hub transfer. They overlap the Repair stage, so
# they are shown alongside it, not added into Total TAT twice.
EXTRA_LEGS = [
	("spare_mr_po", _("MR → PO (h)")),
	("spare_po_receipt", _("PO → Receipt (h)")),
	("device_logistics", _("Logistics (h)")),
]
_EXTRA_LABEL = {
	"spare_mr_po": "Spare MR → PO",
	"spare_po_receipt": "Spare PO → Receipt",
	"device_logistics": "Device Logistics",
}


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
	for fieldname, label in EXTRA_LEGS:
		cols.append({"label": label, "fieldname": fieldname, "fieldtype": "Float", "width": 100, "precision": 1})
	cols += [
		{"label": _("Total TAT (h)"), "fieldname": "total_hours", "fieldtype": "Float", "width": 100, "precision": 1},
		{"label": _("Bottleneck"), "fieldname": "bottleneck", "fieldtype": "Data", "width": 170},
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

	# Row-level scope. This report reads through frappe.get_all rather than raw
	# SQL, so the scope is applied by narrowing the filter dict. Fail closed:
	# an in-scope-but-empty set, or an explicitly requested out-of-scope store,
	# both yield no rows rather than the unscoped whole.
	scoped = get_scoped_warehouses_or_none()
	if scoped is not None:
		requested = sr_filters.get("source_warehouse")
		if requested:
			if requested not in scoped:
				return []
		elif scoped:
			sr_filters["source_warehouse"] = ("in", sorted(scoped))
		else:
			return []

	tickets = frappe.get_all(
		"Service Request",
		filters=sr_filters,
		fields=["name", "customer_name", "device_item_name", "device_item",
			"source_warehouse", "creation", "decision",
			"transfer_date", "transfer_received_date", "transfer_status"],
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

	procurement = _procurement_times(names)

	stage_fields = {key: frappe.scrub(key) for key, _l in STAGES}
	out = []
	for t in tickets:
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

		extras = dict(procurement.get(t.name) or {})
		extras["device_logistics"] = _logistics_hours(t)

		# Bottleneck across ops stages AND the parallel legs
		all_legs = dict(hours)
		for field, val in extras.items():
			if val:
				all_legs[_EXTRA_LABEL[field]] = val
		bottleneck = max(all_legs, key=all_legs.get) if any(all_legs.values()) else ""

		rec = {
			"ticket": t.name,
			"customer_name": t.customer_name,
			"device": t.device_item_name or t.device_item,
			"store": t.source_warehouse,
			"technicians": ", ".join(sorted(tech_map.get(t.name, []))) or "—",
			"current_stage": current,
			"total_hours": round(total, 1),
			"bottleneck": f"{bottleneck} ({round(all_legs[bottleneck], 1)}h)" if bottleneck else "",
		}
		for key, field in stage_fields.items():
			rec[field] = round(hours[key], 1)
		for field, _l in EXTRA_LEGS:
			rec[field] = round(flt(extras.get(field)), 1)
		out.append(rec)
	return out


def _procurement_times(names):
	"""Per SR: hours MR→PO and PO→Receipt for spares requested on the ticket.
	Open-ended legs keep ticking (no PO yet → MR→PO = since MR was raised)."""
	mrs = frappe.get_all(
		"Material Request",
		filters={"service_request": ("in", names), "docstatus": ("<", 2)},
		fields=["name", "service_request", "creation", "status"],
	)
	if not mrs:
		return {}
	mr_names = [m.name for m in mrs]

	def _first_by_mr(doctype, item_doctype):
		rows = frappe.db.sql(
			f"""
			SELECT i.material_request AS mr, MIN(p.creation) AS at
			FROM `tab{doctype}` p
			JOIN `tab{item_doctype}` i ON i.parent = p.name
			WHERE i.material_request IN %(mrs)s AND p.docstatus = 1
			GROUP BY i.material_request
			""",
			{"mrs": mr_names},
			as_dict=True,
		)
		return {r.mr: r.at for r in rows}

	po_at = _first_by_mr("Purchase Order", "Purchase Order Item")
	pr_at = _first_by_mr("Purchase Receipt", "Purchase Receipt Item")

	out = {}
	for m in mrs:
		rec = out.setdefault(m.service_request, {"spare_mr_po": 0.0, "spare_po_receipt": 0.0})
		po = po_at.get(m.name)
		pr = pr_at.get(m.name)
		if po:
			rec["spare_mr_po"] = max(rec["spare_mr_po"], flt(time_diff_in_hours(po, m.creation)))
			end = pr or (now_datetime() if m.status not in ("Received", "Stopped", "Cancelled") else None)
			if end:
				rec["spare_po_receipt"] = max(rec["spare_po_receipt"], flt(time_diff_in_hours(end, po)))
		elif m.status not in ("Received", "Stopped", "Cancelled"):
			# MR raised, purchase hasn't acted yet — still waiting
			rec["spare_mr_po"] = max(rec["spare_mr_po"], flt(time_diff_in_hours(now_datetime(), m.creation)))
	return out


def _logistics_hours(t):
	"""Device transfer leg: store → hub (and still-in-transit keeps ticking)."""
	if not t.get("transfer_date"):
		return 0.0
	end = t.get("transfer_received_date")
	if not end and t.get("transfer_status") == "In Transit":
		end = now_datetime()
	if not end:
		return 0.0
	return max(flt(time_diff_in_hours(end, t.transfer_date)), 0.0)


def _all_leg_fields():
	legs = [(frappe.scrub(key), str(label).replace(" (h)", "")) for key, label in STAGES]
	legs += [(field, _EXTRA_LABEL[field]) for field, _l in EXTRA_LEGS]
	return legs


def get_chart(data):
	if not data:
		return None
	labels, values = [], []
	for field, label in _all_leg_fields():
		vals = [r[field] for r in data if r.get(field)]
		labels.append(label)
		values.append(round(sum(vals) / len(vals), 1) if vals else 0)
	return {
		"data": {"labels": labels, "datasets": [{"name": _("Avg hours in leg"), "values": values}]},
		"type": "bar",
		"colors": ["#7c3aed"],
	}


def get_summary(data):
	if not data:
		return []
	avg_tat = round(sum(r["total_hours"] for r in data) / len(data), 1)
	leg_totals = {}
	for field, label in _all_leg_fields():
		leg_totals[label] = sum(r.get(field) or 0 for r in data)
	slowest = max(leg_totals, key=leg_totals.get)
	return [
		{"label": _("Tickets"), "value": len(data), "datatype": "Int"},
		{"label": _("Avg TAT (h)"), "value": avg_tat, "datatype": "Float"},
		{"label": _("Slowest Leg (overall)"), "value": f"{slowest} — {round(leg_totals[slowest], 1)}h", "datatype": "Data", "indicator": "Orange"},
	]
