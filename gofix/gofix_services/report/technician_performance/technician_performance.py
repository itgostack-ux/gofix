# Copyright (c) 2025, GoStack and contributors
# Technician Performance — Script Report

import frappe
from frappe import _

from ch_erp15.ch_erp15.report_scope import scope_where_clause
from gofix.config import get_int_setting, require_role_setting


def execute(filters=None):
	require_role_setting("service_dashboard_roles", action=_("view technician performance"))
	frappe.has_permission("Job Assignment", "read", throw=True)
	frappe.has_permission("Sales Order", "read", throw=True)
	columns = get_columns()
	data = get_data(filters)
	chart = get_chart(data)
	return columns, data, None, chart


def get_columns():
	return [
		{"label": _("Technician"), "fieldname": "technician", "fieldtype": "Data", "width": 200},
		{"label": _("Jobs Completed"), "fieldname": "jobs_completed", "fieldtype": "Int", "width": 120},
		{"label": _("Jobs Open"), "fieldname": "jobs_open", "fieldtype": "Int", "width": 100},
		{"label": _("Avg Hours/Job"), "fieldname": "avg_hours", "fieldtype": "Float", "width": 120, "precision": 1},
		{"label": _("Total Hours"), "fieldname": "total_hours", "fieldtype": "Float", "width": 110, "precision": 1},
		{"label": _("QC Pass Rate %"), "fieldname": "qc_pass_rate", "fieldtype": "Percent", "width": 120},
		{"label": _("Rework Jobs"), "fieldname": "rework_jobs", "fieldtype": "Int", "width": 110},
		{"label": _("Not Repairable"), "fieldname": "not_repairable", "fieldtype": "Int", "width": 120},
	]


def get_data(filters):
	conditions = ""
	params = {}

	if filters and filters.get("from_date"):
		conditions += " AND ja.assignment_date >= %(from_date)s"
		params["from_date"] = filters["from_date"]
	if filters and filters.get("to_date"):
		conditions += " AND ja.assignment_date <= %(to_date)s"
		params["to_date"] = filters["to_date"]
	company = filters.get("company") if filters else None
	if company:
		conditions += " AND COALESCE(sr.company, so.company) = %(company)s"
		params["company"] = company

	scope = scope_where_clause(warehouse_field="so.set_warehouse")
	scope_sql = f" AND {scope}" if scope else ""
	row_limit = min(get_int_setting("interactive_report_row_limit", 2000), 10000)
	params["result_limit"] = row_limit + 1

	query = f"""
		WITH scoped_assignments AS (
			SELECT
				COALESCE(emp.employee_name, ja.service_engineer, ja.user, 'Unassigned') AS technician,
				COALESCE(ja.service_engineer, ja.user, 'Unassigned') AS technician_id,
				ja.assignment_status,
				ja.actual_hours,
				ja.repair_outcome,
				ja.service_order,
				so.qc_status,
				so.rework_count,
				so.is_service_order
			FROM `tabJob Assignment` ja
			LEFT JOIN `tabEmployee` emp ON emp.name = ja.service_engineer
			LEFT JOIN `tabSales Order` so ON so.name = ja.service_order
			LEFT JOIN `tabService Request` sr ON sr.name = ja.service_request
			WHERE ja.docstatus < 2
			{conditions}{scope_sql}
		),
		assignment_metrics AS (
			SELECT
				technician,
				technician_id,
				SUM(CASE WHEN assignment_status IN ('Completed', 'Closed') THEN 1 ELSE 0 END) AS jobs_completed,
				SUM(CASE WHEN assignment_status IN ('Open', 'In Progress') THEN 1 ELSE 0 END) AS jobs_open,
				AVG(actual_hours) AS avg_hours,
				SUM(COALESCE(actual_hours, 0)) AS total_hours,
				SUM(CASE WHEN repair_outcome = 'Not Repairable' THEN 1 ELSE 0 END) AS not_repairable
			FROM scoped_assignments
			GROUP BY technician_id, technician
		),
		completed_service_orders AS (
			SELECT DISTINCT technician_id, service_order, qc_status, rework_count
			FROM scoped_assignments
			WHERE assignment_status IN ('Completed', 'Closed')
			  AND service_order IS NOT NULL
			  AND is_service_order = 1
		),
		service_metrics AS (
			SELECT
				technician_id,
				COUNT(*) AS qc_total,
				SUM(CASE WHEN qc_status = 'Pass' THEN 1 ELSE 0 END) AS qc_passes,
				SUM(COALESCE(rework_count, 0)) AS rework_jobs
			FROM completed_service_orders
			GROUP BY technician_id
		)
		SELECT
			metrics.technician,
			metrics.technician_id,
			metrics.jobs_completed,
			metrics.jobs_open,
			metrics.avg_hours,
			metrics.total_hours,
			metrics.not_repairable,
			COALESCE(100.0 * service.qc_passes / NULLIF(service.qc_total, 0), 0) AS qc_pass_rate,
			COALESCE(service.rework_jobs, 0) AS rework_jobs
		FROM assignment_metrics metrics
		LEFT JOIN service_metrics service ON service.technician_id = metrics.technician_id
		ORDER BY metrics.jobs_completed DESC, metrics.technician_id ASC
		LIMIT %(result_limit)s
	"""

	data = frappe.db.sql(query, params, as_dict=True)
	if len(data) > row_limit:
		frappe.throw(
			_("Technician Performance exceeds the configured limit of {0} rows. Narrow the filters.").format(
				row_limit
			),
			frappe.ValidationError,
		)

	return data


def get_chart(data):
	labels = [d.technician for d in data[:10]]
	values = [d.jobs_completed for d in data[:10]]
	return {
		"data": {"labels": labels, "datasets": [{"name": _("Completed Jobs"), "values": values}]},
		"type": "bar",
		"colors": ["#36b37e"],
	}
