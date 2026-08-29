# Copyright (c) 2026, GoFix and contributors
# Technician Assignment Intelligence
#
# Scores technicians based on:
#   - Skill match (issue category experience)
#   - Current workload (active jobs)
#   - Past performance (completion rate, rework rate)
#   - Availability (active/present)
#
# Called from Ops Hub assign stage as a recommendation layer.

import frappe
from frappe import _
from frappe.utils import add_days, cint, flt, nowdate

from gofix.config import get_int_setting, is_privileged_user, require_role_setting
from gofix.security import assert_service_request_access
from gofix.scope_guard import assert_warehouse


@frappe.whitelist()
def get_recommended_technicians(
	service_request=None,
	issue_category=None,
	minimum_grade=None,
	limit=5,
	company=None,
	warehouse=None,
) -> list:
	"""Return ranked technicians with scores for a given SR or issue category.

	Each technician gets a composite score (0–100) based on:
	  - skill_score (0–40): experience with this issue category
	  - workload_score (0–30): fewer active jobs = higher score
	  - performance_score (0–30): completion rate, low rework, speed
	"""
	for doctype in ("Service Request", "Employee", "Job Assignment", "Technician Grade"):
		frappe.has_permission(doctype, ptype="read", throw=True)

	if service_request:
		sr = assert_service_request_access(service_request, permission_type="read")
		sr_company = sr.get("company")
		sr_warehouse = (
			sr.get("current_processing_location")
			or sr.get("transferred_to_store")
			or sr.get("current_location")
			or sr.get("source_warehouse")
		)
		if company and company != sr_company:
			frappe.throw(_("Company does not match the Service Request."), frappe.PermissionError)
		if warehouse and warehouse != sr_warehouse:
			frappe.throw(_("Warehouse does not match the Service Request."), frappe.PermissionError)
		if issue_category and sr.get("issue_category") and issue_category != sr.issue_category:
			frappe.throw(_("Issue category does not match the Service Request."), frappe.PermissionError)
		company = sr_company
		warehouse = sr_warehouse
		issue_category = sr.get("issue_category") or issue_category
	elif not is_privileged_user() and (not company or not warehouse):
		frappe.throw(
			_("A Service Request or an explicit company and warehouse is required."),
			frappe.PermissionError,
		)

	if company:
		company_doc = frappe.get_doc("Company", company)
		company_doc.check_permission("read")
	if warehouse:
		warehouse_doc = frappe.get_doc("Warehouse", warehouse)
		warehouse_doc.check_permission("read")
		if company and warehouse_doc.company != company:
			frappe.throw(_("Warehouse does not belong to the selected company."), frappe.PermissionError)
		company = company or warehouse_doc.company
		assert_warehouse(warehouse=warehouse, company=company)

	if issue_category:
		issue_doc = frappe.get_doc("Issue Category", issue_category)
		issue_doc.check_permission("read")
	if minimum_grade:
		grade_doc = frappe.get_doc("Technician Grade", minimum_grade)
		grade_doc.check_permission("read")

	filters = {"status": "Active"}
	if company:
		filters["company"] = company
	has_warehouse_field = frappe.db.has_column("Employee", "gofix_service_warehouse")
	if warehouse and has_warehouse_field:
		filters["gofix_service_warehouse"] = warehouse
	elif warehouse and not is_privileged_user():
		return []

	candidate_limit = min(get_int_setting("technician_candidate_limit", 200), 500)
	employee_fields = [
		"name", "employee_name", "technician_grade", "designation", "default_shift",
	]
	if has_warehouse_field:
		employee_fields.append("gofix_service_warehouse")
	employees = frappe.get_list(
		"Employee",
		filters=filters,
		fields=employee_fields,
		order_by="employee_name, name",
		limit_page_length=candidate_limit,
	)

	if not employees:
		return []

	emp_names = tuple(e.name for e in employees)
	workload_map = _get_workload_map(emp_names, company=company, warehouse=warehouse)
	performance_window_days = get_int_setting("technician_performance_window_days", 90)
	skill_window_days = get_int_setting("technician_skill_window_days", 180)
	perf_map = _get_performance_map(
		emp_names,
		company=company,
		warehouse=warehouse,
		days=performance_window_days,
	)
	skill_map = (
		_get_skill_map(
			emp_names,
			issue_category,
			company=company,
			warehouse=warehouse,
			days=skill_window_days,
		)
		if issue_category else {}
	)

	grade_names = {employee.technician_grade for employee in employees if employee.technician_grade}
	if minimum_grade:
		grade_names.add(minimum_grade)
	grade_rows = frappe.get_list(
		"Technician Grade",
		filters={"name": ("in", tuple(grade_names))},
		fields=["name", "grade_name", "grade_level"],
		limit_page_length=len(grade_names),
	) if grade_names else []
	grade_by_name = {grade.name: grade for grade in grade_rows}
	req_level = cint((grade_by_name.get(minimum_grade) or {}).get("grade_level"))

	results = []
	for emp in employees:
		grade = grade_by_name.get(emp.technician_grade)
		emp_level = cint(grade.grade_level if grade else 0)

		if req_level and emp_level < req_level:
			continue

		# Skill score (0–40)
		skill_data = skill_map.get(emp.name, {})
		category_jobs = skill_data.get("category_jobs", 0)
		category_success = skill_data.get("category_success", 0)
		if category_jobs >= 10:
			skill_score = 40.0
		elif category_jobs >= 5:
			skill_score = 30.0
		elif category_jobs >= 1:
			skill_score = 20.0
		else:
			skill_score = 5.0  # Unknown but available

		# Bonus for high success rate on this category
		if category_jobs > 0:
			success_rate = category_success / category_jobs
			skill_score = min(40, skill_score + (success_rate * 10))

		# Workload score (0–30): 0 active = 30, 1 = 25, 2 = 20, ... 6+ = 0
		active_jobs = workload_map.get(emp.name, 0)
		workload_score = max(0, 30 - (active_jobs * 5))

		# Performance score (0–30)
		perf = perf_map.get(emp.name, {})
		total_done = perf.get("completed", 0)
		rework_count = perf.get("rework", 0)
		avg_hours = perf.get("avg_hours", 0)

		perf_score = 15.0  # base
		if total_done > 0:
			# Completion volume bonus (up to +5)
			perf_score += min(5, total_done / 10)
			# Low rework bonus (up to +5)
			rework_rate = rework_count / total_done if total_done else 0
			perf_score += max(0, 5 - (rework_rate * 25))
			# Speed bonus — lower avg hours = better (up to +5)
			if avg_hours > 0:
				perf_score += max(0, 5 - (avg_hours / 4))

		perf_score = min(30, max(0, perf_score))

		composite = round(skill_score + workload_score + perf_score, 1)

		grade_display = (
			f"L{grade.grade_level} — {grade.grade_name}"
			if grade else (emp.technician_grade or "Ungraded")
		)

		results.append({
			"employee": emp.name,
			"employee_name": emp.employee_name,
			"technician_grade": emp.technician_grade,
			"grade_display": grade_display,
			"active_jobs": active_jobs,
			"score": composite,
			"skill_score": round(skill_score, 1),
			"workload_score": round(workload_score, 1),
			"performance_score": round(perf_score, 1),
			"category_experience": category_jobs,
			"total_completed_window": total_done,
			"rework_count_window": rework_count,
			"performance_window_days": performance_window_days,
			"recommendation": _get_recommendation_label(composite),
		})

	results.sort(key=lambda row: (-row["score"], row["employee"]))
	result_limit = min(
		max(cint(limit) or 5, 1),
		min(get_int_setting("technician_recommendation_limit", 25), 100),
	)
	return results[:result_limit]


def _get_workload_map(emp_names, company=None, warehouse=None):
	"""Count active (Open/In Progress) Job Assignments per technician."""
	if not emp_names:
		return {}

	data = frappe.db.sql("""
		SELECT ja.service_engineer, COUNT(*) AS cnt
		FROM `tabJob Assignment` ja
		INNER JOIN `tabService Request` sr ON sr.name = ja.service_request
		WHERE ja.service_engineer IN %(names)s
			AND ja.docstatus = 1
			AND ja.assignment_status IN ('Open', 'In Progress')
			AND (%(company)s = '' OR sr.company = %(company)s)
			AND (
				%(warehouse)s = ''
				OR sr.source_warehouse = %(warehouse)s
				OR sr.current_location = %(warehouse)s
				OR sr.transferred_to_store = %(warehouse)s
			)
		GROUP BY ja.service_engineer
	""", {
		"names": emp_names,
		"company": company or "",
		"warehouse": warehouse or "",
	}, as_dict=True)

	return {r.service_engineer: r.cnt for r in data}


def _get_performance_map(emp_names, company=None, warehouse=None, days=90):
	"""Get completion stats per technician over the last N days."""
	if not emp_names:
		return {}

	cutoff = add_days(nowdate(), -days)

	data = frappe.db.sql("""
		SELECT
			ja.service_engineer,
			COUNT(*) as completed,
			SUM(CASE WHEN ja.assignment_type = 'Rework' THEN 1 ELSE 0 END) as rework,
			AVG(TIMESTAMPDIFF(HOUR, ja.creation, ja.modified)) as avg_hours
		FROM `tabJob Assignment` ja
		INNER JOIN `tabService Request` sr ON sr.name = ja.service_request
		WHERE ja.service_engineer IN %(names)s
			AND ja.docstatus = 1
			AND ja.assignment_status = 'Completed'
			AND ja.creation >= %(cutoff)s
			AND (%(company)s = '' OR sr.company = %(company)s)
			AND (
				%(warehouse)s = ''
				OR sr.source_warehouse = %(warehouse)s
				OR sr.current_location = %(warehouse)s
				OR sr.transferred_to_store = %(warehouse)s
			)
		GROUP BY ja.service_engineer
	""", {
		"names": emp_names,
		"cutoff": cutoff,
		"company": company or "",
		"warehouse": warehouse or "",
	}, as_dict=True)

	result = {}
	for r in data:
		result[r.service_engineer] = {
			"completed": r.completed or 0,
			"rework": r.rework or 0,
			"avg_hours": flt(r.avg_hours),
		}
	return result


def _get_skill_map(emp_names, issue_category, company=None, warehouse=None, days=180):
	"""Count how many jobs each technician has done for this issue category."""
	if not emp_names or not issue_category:
		return {}

	cutoff = add_days(nowdate(), -days)

	data = frappe.db.sql("""
		SELECT
			ja.service_engineer,
			COUNT(*) as category_jobs,
			SUM(CASE WHEN ja.assignment_status = 'Completed' THEN 1 ELSE 0 END) as category_success
		FROM `tabJob Assignment` ja
		INNER JOIN `tabService Request` sr ON sr.name = ja.service_request
		WHERE ja.service_engineer IN %(names)s
			AND ja.docstatus = 1
			AND sr.issue_category = %(category)s
			AND ja.creation >= %(cutoff)s
			AND (%(company)s = '' OR sr.company = %(company)s)
			AND (
				%(warehouse)s = ''
				OR sr.source_warehouse = %(warehouse)s
				OR sr.current_location = %(warehouse)s
				OR sr.transferred_to_store = %(warehouse)s
			)
		GROUP BY ja.service_engineer
	""", {
		"names": emp_names,
		"category": issue_category,
		"cutoff": cutoff,
		"company": company or "",
		"warehouse": warehouse or "",
	}, as_dict=True)

	result = {}
	for r in data:
		result[r.service_engineer] = {
			"category_jobs": r.category_jobs or 0,
			"category_success": r.category_success or 0,
		}
	return result


def _get_recommendation_label(score):
	"""Human-readable recommendation label."""
	high_score = get_int_setting("technician_recommendation_high_score", 70)
	recommended_score = get_int_setting("technician_recommendation_score", 50)
	available_score = get_int_setting("technician_available_score", 30)
	if score >= high_score:
		return "Highly Recommended"
	elif score >= recommended_score:
		return "Recommended"
	elif score >= available_score:
		return "Available"
	else:
		return "Low Match"
