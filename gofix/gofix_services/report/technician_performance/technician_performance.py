# Copyright (c) 2025, GoStack and contributors
# Technician Performance — Script Report

import frappe
from frappe import _
from frappe.utils import flt

from ch_erp15.ch_erp15.report_scope import scope_where_clause


def execute(filters=None):
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

	# Tier 4: Job Assignment has no store/warehouse of its own — reach scope
	# through the linked Sales Order (service order). LEFT JOIN keeps rows
	# whose SO is missing only when the caller is a bypass user (scope is None);
	# for scoped users, an absent SO fails the IN check and drops — fail-closed.
	scope = scope_where_clause(warehouse_field="so.set_warehouse")
	needs_so_join = bool(scope or company)
	needs_sr_join = bool(company)
	so_join = "LEFT JOIN `tabSales Order` so ON so.name = ja.service_order" if needs_so_join else ""
	sr_join = "LEFT JOIN `tabService Request` sr ON sr.name = ja.service_request" if needs_sr_join else ""
	scope_sql = f" AND {scope}" if scope else ""

	query = f"""
		SELECT
			COALESCE(ja.service_engineer, ja.user, 'Unassigned') as technician,
			SUM(CASE WHEN ja.assignment_status IN ('Completed', 'Closed') THEN 1 ELSE 0 END) as jobs_completed,
			SUM(CASE WHEN ja.assignment_status IN ('Open', 'In Progress') THEN 1 ELSE 0 END) as jobs_open,
			AVG(ja.actual_hours) as avg_hours,
			SUM(COALESCE(ja.actual_hours, 0)) as total_hours,
			SUM(CASE WHEN ja.repair_outcome = 'Not Repairable' THEN 1 ELSE 0 END) as not_repairable
		FROM `tabJob Assignment` ja
		{so_join}
		{sr_join}
		WHERE ja.docstatus < 2
		{conditions}{scope_sql}
		GROUP BY COALESCE(ja.service_engineer, ja.user, 'Unassigned')
		ORDER BY jobs_completed DESC
	"""

	data = frappe.db.sql(query, params, as_dict=True)

	# Calculate QC pass rate and rework from Sales Orders
	for row in data:
		tech = row.technician
		if tech and tech != "Unassigned":
			# Get SO names for this technician's completed jobs
			extra = ""
			tech_params = {"tech": tech}
			if company:
				extra += " AND COALESCE(sr.company, so.company) = %(company)s"
				tech_params["company"] = company
			if scope:
				extra += f" AND {scope}"

			so_names = frappe.db.sql(f"""
				SELECT DISTINCT ja.service_order
				FROM `tabJob Assignment` ja
				LEFT JOIN `tabSales Order` so ON so.name = ja.service_order
				LEFT JOIN `tabService Request` sr ON sr.name = ja.service_request
				WHERE (ja.service_engineer = %(tech)s OR ja.user = %(tech)s)
				  AND ja.assignment_status IN ('Completed', 'Closed')
				  AND ja.service_order IS NOT NULL
				  {extra}
			""", tech_params, as_dict=True)

			if so_names:
				so_list = [s.service_order for s in so_names if s.service_order]
				if so_list:
					qc_data = frappe.db.sql("""
						SELECT
							SUM(CASE WHEN qc_status = 'Pass' THEN 1 ELSE 0 END) as passes,
							SUM(COALESCE(rework_count, 0)) as reworks,
							COUNT(*) as total
						FROM `tabSales Order`
						WHERE name IN %s AND is_service_order = 1
					""", [so_list], as_dict=True)

					if qc_data and qc_data[0].total:
						row.qc_pass_rate = flt(qc_data[0].passes) / flt(qc_data[0].total) * 100
						row.rework_jobs = qc_data[0].reworks or 0
					else:
						row.qc_pass_rate = 0
						row.rework_jobs = 0
				else:
					row.qc_pass_rate = 0
					row.rework_jobs = 0
			else:
				row.qc_pass_rate = 0
				row.rework_jobs = 0
		else:
			row.qc_pass_rate = 0
			row.rework_jobs = 0

	return data


def get_chart(data):
	labels = [d.technician for d in data[:10]]
	values = [d.jobs_completed for d in data[:10]]
	return {
		"data": {"labels": labels, "datasets": [{"name": _("Completed Jobs"), "values": values}]},
		"type": "bar",
		"colors": ["#36b37e"],
	}
