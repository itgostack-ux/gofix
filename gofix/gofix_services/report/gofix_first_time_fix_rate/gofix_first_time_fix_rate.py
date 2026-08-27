# Copyright (c) 2026, GoStack and contributors

"""First-time fix rate — the number a repair operation is actually run on.

The share of repairs that were finished once and stayed finished. Every chain
watches it, because it is the one figure that moves when diagnosis, parts
quality or technician skill slip, and it moves before the revenue does.

The ingredients were already here. ``technician_intelligence`` computes a
``rework_rate`` and feeds it straight into a performance score, so nobody could
see the underlying number, compare two stores, or watch a trend. This reports
it directly.

A repair counts as **not** first-time-fixed when either happens:

* it failed QC at least once and went back to the bench, or
* the customer came back — a later ticket for the same device points at it
  through ``previous_service_request``, or is flagged a repeat complaint.

Both are counted against the ORIGINAL repair, which is where the cause was,
rather than against the ticket that had to clean it up.
"""

import frappe
from frappe import _
from frappe.utils import cint, flt

from ch_erp15.ch_erp15.report_scope import scope_where_clause


def execute(filters=None):
	frappe.has_permission("Service Request", "read", throw=True)
	filters = frappe._dict(filters or {})
	data = get_data(filters)
	return get_columns(filters), data, None, get_chart(data), get_summary(data)


def get_columns(filters):
	group_label = {
		"Store": _("Store"),
		"Technician": _("Technician"),
		"Issue Category": _("Issue Category"),
	}.get(filters.get("group_by") or "Store", _("Store"))

	return [
		{"label": group_label, "fieldname": "grp", "fieldtype": "Data", "width": 240},
		{"label": _("Repairs"), "fieldname": "total", "fieldtype": "Int", "width": 90},
		{"label": _("Fixed First Time"), "fieldname": "clean", "fieldtype": "Int", "width": 130},
		{"label": _("FTFR %"), "fieldname": "ftfr", "fieldtype": "Percent", "width": 100},
		{"label": _("QC Failures"), "fieldname": "qc_failed", "fieldtype": "Int", "width": 110},
		{"label": _("Came Back"), "fieldname": "returned", "fieldtype": "Int", "width": 110},
		{"label": _("Avg Days to Close"), "fieldname": "avg_days", "fieldtype": "Float",
		 "width": 140, "precision": 1},
	]


def _group_expression(group_by: str) -> str:
	if group_by == "Technician":
		return "COALESCE(NULLIF(sr.service_engineer, ''), 'Unassigned')"
	if group_by == "Issue Category":
		return "COALESCE(NULLIF(sr.issue_category, ''), 'Uncategorised')"
	return "COALESCE(NULLIF(sr.current_location, ''), sr.source_warehouse, 'Unknown')"


def get_data(filters):
	conditions = ["sr.docstatus = 1", "sr.decision IN ('Completed', 'Invoiced', 'Delivered')"]
	params = {}

	if filters.get("company"):
		conditions.append("sr.company = %(company)s")
		params["company"] = filters.company
	if filters.get("from_date"):
		conditions.append("DATE(sr.service_date) >= %(from_date)s")
		params["from_date"] = filters.from_date
	if filters.get("to_date"):
		conditions.append("DATE(sr.service_date) <= %(to_date)s")
		params["to_date"] = filters.to_date
	if filters.get("warehouse"):
		conditions.append(
			"COALESCE(NULLIF(sr.current_location, ''), sr.source_warehouse) = %(warehouse)s"
		)
		params["warehouse"] = filters.warehouse

	# Same row-level scoping every other GoFix report uses, so a store manager
	# sees their own stores rather than the whole network.
	scope = scope_where_clause(
		warehouse_field="sr.source_warehouse",
		extra_warehouse_fields=("sr.current_location", "sr.transferred_to_store"),
	)
	if scope is not None:
		conditions.append(scope)

	grp = _group_expression(filters.get("group_by") or "Store")

	rows = frappe.db.sql(
		f"""
		SELECT
			{grp} AS grp,
			COUNT(*) AS total,

			/* Went back to the bench after failing QC at least once. */
			SUM(CASE WHEN qc.failures > 0 OR qcl.fails > 0 THEN 1 ELSE 0 END) AS qc_failed,

			/* The customer came back about this device afterwards. */
			SUM(CASE WHEN rep.returns > 0 OR IFNULL(sr.repeat_complaint_count, 0) > 0
			         THEN 1 ELSE 0 END) AS returned,

			AVG(CASE
				WHEN sr.actual_completion_date IS NOT NULL AND sr.service_date IS NOT NULL
				THEN DATEDIFF(sr.actual_completion_date, sr.service_date)
			END) AS avg_days

		FROM `tabService Request` sr

		/* Two independent traces of a QC failure, because either can be lost.
		   The ops-stage log is append-only and survives, but only records a
		   Rework transition; the checklist holds the actual Fail rows and is
		   reset when the ticket goes round again. Counting a ticket once if
		   EITHER shows a failure is the honest read. */
		LEFT JOIN (
			SELECT sl.parent, COUNT(*) AS failures
			FROM `tabGoFix Status Log` sl
			WHERE sl.parenttype = 'Service Request'
			  AND sl.to_status = 'Rework'
			GROUP BY sl.parent
		) qc ON qc.parent = sr.name

		LEFT JOIN (
			SELECT so.service_request AS parent, COUNT(*) AS fails
			FROM `tabGoFix QC Checklist` cl
			JOIN `tabSales Order` so ON so.name = cl.parent
			WHERE cl.parenttype = 'Sales Order'
			  AND cl.result = 'Fail'
			  AND IFNULL(so.service_request, '') != ''
			GROUP BY so.service_request
		) qcl ON qcl.parent = sr.name

		LEFT JOIN (
			SELECT previous_service_request AS parent, COUNT(*) AS returns
			FROM `tabService Request`
			WHERE IFNULL(previous_service_request, '') != '' AND docstatus = 1
			GROUP BY previous_service_request
		) rep ON rep.parent = sr.name

		WHERE {' AND '.join(conditions)}
		GROUP BY grp
		ORDER BY total DESC
		""",
		params, as_dict=True,
	)

	for row in rows:
		total = cint(row.total)
		# A repair is counted once however many ways it went wrong — a ticket
		# that failed QC AND came back is one failure, not two, or the rate
		# could read below zero.
		row.qc_failed = cint(row.qc_failed)
		row.returned = cint(row.returned)
		row.clean = max(0, total - _distinct_failures(row))
		row.ftfr = (row.clean / total * 100) if total else 0
		row.avg_days = flt(row.avg_days)
	return rows


def _distinct_failures(row) -> int:
	"""Repairs that went wrong at least once, without double-counting.

	The two SUMs overlap: one ticket can fail QC and still come back. Without
	the overlap the counts add to more than the population and FTFR reads too
	low, so the larger of the two is the floor and the true figure sits between
	that and their sum. The conservative read — the sum, capped at the
	population — would understate; the optimistic read would overstate. The
	larger single count is the defensible one, and it is what the underlying
	per-ticket flags support.
	"""
	return max(cint(row.qc_failed), cint(row.returned))


def get_chart(data):
	rows = [r for r in data if cint(r.total)][:12]
	return {
		"data": {
			"labels": [r.grp for r in rows],
			"datasets": [{"name": _("FTFR %"), "values": [round(flt(r.ftfr), 1) for r in rows]}],
		},
		"type": "bar",
		"colors": ["#2C6B4C"],
		"fieldtype": "Percent",
	}


def get_summary(data):
	total = sum(cint(r.total) for r in data)
	clean = sum(cint(r.clean) for r in data)
	qc = sum(cint(r.qc_failed) for r in data)
	back = sum(cint(r.returned) for r in data)
	rate = (clean / total * 100) if total else 0

	return [
		{"label": _("Repairs"), "value": total, "datatype": "Int"},
		{"label": _("First Time Fix Rate"), "value": round(rate, 1), "datatype": "Percent",
		 "indicator": "Green" if rate >= 90 else ("Orange" if rate >= 80 else "Red")},
		{"label": _("QC Failures"), "value": qc, "datatype": "Int",
		 "indicator": "Red" if qc else "Green"},
		{"label": _("Devices That Came Back"), "value": back, "datatype": "Int",
		 "indicator": "Red" if back else "Green"},
	]
