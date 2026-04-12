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
from frappe.utils import flt, cint, nowdate, add_days, getdate


@frappe.whitelist()
def get_recommended_technicians(service_request=None, issue_category=None, minimum_grade=None, limit=5) -> list:
	"""Return ranked technicians with scores for a given SR or issue category.

	Each technician gets a composite score (0–100) based on:
	  - skill_score (0–40): experience with this issue category
	  - workload_score (0–30): fewer active jobs = higher score
	  - performance_score (0–30): completion rate, low rework, speed
	"""
	limit = cint(limit) or 5

	# Resolve issue category from SR if not provided
	if service_request and not issue_category:
		issue_category = frappe.db.get_value("Service Request", service_request, "issue_category")

	company = None
	warehouse = None
	if service_request:
		sr = frappe.db.get_value(
			"Service Request", service_request,
			["company", "source_warehouse", "current_processing_location"],
			as_dict=True,
		)
		if sr:
			company = sr.company
			warehouse = sr.current_processing_location or sr.source_warehouse

	# Get all active technician employees
	filters = {"status": "Active"}
	if company:
		filters["company"] = company

	employees = frappe.get_all(
		"Employee",
		filters=filters,
		fields=["name", "employee_name", "technician_grade", "designation", "default_shift"],
	)

	if not employees:
		return []

	# Build scoring context
	emp_names = [e.name for e in employees]
	workload_map = _get_workload_map(emp_names)
	perf_map = _get_performance_map(emp_names, days=90)
	skill_map = _get_skill_map(emp_names, issue_category, days=180) if issue_category else {}

	# Grade filtering
	req_level = 0
	if minimum_grade:
		req_level = cint(frappe.db.get_value("Technician Grade", minimum_grade, "grade_level") or 0)

	results = []
	for emp in employees:
		emp_level = 0
		if emp.technician_grade:
			emp_level = cint(frappe.db.get_value("Technician Grade", emp.technician_grade, "grade_level") or 0)

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

		grade_display = ""
		if emp.technician_grade:
			grade = frappe.db.get_value(
				"Technician Grade", emp.technician_grade,
				["grade_name", "grade_level"], as_dict=True,
			)
			grade_display = f"L{grade.grade_level} — {grade.grade_name}" if grade else emp.technician_grade
		else:
			grade_display = "Ungraded"

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
			"total_completed_90d": total_done,
			"rework_count_90d": rework_count,
			"recommendation": _get_recommendation_label(composite),
		})

	# Sort by score descending
	results.sort(key=lambda x: x["score"], reverse=True)
	return results[:cint(limit)]


def _get_workload_map(emp_names):
	"""Count active (Open/In Progress) Job Assignments per technician."""
	if not emp_names:
		return {}

	data = frappe.db.sql("""
		SELECT service_engineer, COUNT(*) as cnt
		FROM `tabJob Assignment`
		WHERE service_engineer IN %(names)s
			AND docstatus = 1
			AND assignment_status IN ('Open', 'In Progress')
		GROUP BY service_engineer
	""", {"names": emp_names}, as_dict=True)

	return {r.service_engineer: r.cnt for r in data}


def _get_performance_map(emp_names, days=90):
	"""Get completion stats per technician over the last N days."""
	if not emp_names:
		return {}

	cutoff = add_days(nowdate(), -days)

	data = frappe.db.sql("""
		SELECT
			service_engineer,
			COUNT(*) as completed,
			SUM(CASE WHEN assignment_type = 'Rework' THEN 1 ELSE 0 END) as rework,
			AVG(TIMESTAMPDIFF(HOUR, creation, modified)) as avg_hours
		FROM `tabJob Assignment`
		WHERE service_engineer IN %(names)s
			AND docstatus = 1
			AND assignment_status = 'Completed'
			AND creation >= %(cutoff)s
		GROUP BY service_engineer
	""", {"names": emp_names, "cutoff": cutoff}, as_dict=True)

	result = {}
	for r in data:
		result[r.service_engineer] = {
			"completed": r.completed or 0,
			"rework": r.rework or 0,
			"avg_hours": flt(r.avg_hours),
		}
	return result


def _get_skill_map(emp_names, issue_category, days=180):
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
		GROUP BY ja.service_engineer
	""", {"names": emp_names, "category": issue_category, "cutoff": cutoff}, as_dict=True)

	result = {}
	for r in data:
		result[r.service_engineer] = {
			"category_jobs": r.category_jobs or 0,
			"category_success": r.category_success or 0,
		}
	return result


def _get_recommendation_label(score):
	"""Human-readable recommendation label."""
	if score >= 70:
		return "Highly Recommended"
	elif score >= 50:
		return "Recommended"
	elif score >= 30:
		return "Available"
	else:
		return "Low Match"
