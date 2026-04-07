"""
GoFix Ops Hub — Backend API

Step-by-step repair operations workflow for GoFix service tickets.
Stages: Analysis → Customer Confirmation → Solution Assignment → Technician Assignment → Repair → QC → Invoice/Rework

Custom fields required on Service Request:
  - analysis_confirmed  (Check)
  - customer_confirmed  (Check)
  - confirmation_sent_at (Datetime, read-only)
"""

import json

import frappe
from frappe import _
from frappe.utils import add_days, cint, flt, now_datetime, nowdate

no_cache = 1

STAGE_LABELS = {
	"analysis": "Analysis",
	"confirm": "Customer Confirmation",
	"solutions": "Solution Assignment",
	"assign": "Technician Assignment",
	"repair": "Repair",
	"qc": "Quality Control",
	"invoice": "Invoice",
	"rework": "Rework",
	"done": "Completed",
}


def _log_ops_stage(sr_name, from_stage, to_stage):
	"""Append a GoFix Status Log row for ops-hub stage transitions."""
	from frappe.utils import time_diff_in_hours

	sr = frappe.get_doc("Service Request", sr_name)
	sr.flags.ignore_validate_update_after_submit = True

	prev_at = None
	if sr.get("status_log"):
		prev_at = sr.status_log[-1].changed_at
	elapsed = round(time_diff_in_hours(now_datetime(), prev_at), 2) if prev_at else 0

	sr.append("status_log", {
		"from_status": STAGE_LABELS.get(from_stage, from_stage),
		"to_status": STAGE_LABELS.get(to_stage, to_stage),
		"changed_by": frappe.session.user,
		"changed_at": now_datetime(),
		"time_in_previous_status_hours": elapsed,
	})
	sr.save()


def get_context(context):
	context.no_cache = 1
	context.show_sidebar = False


# ── Context ───────────────────────────────────────────────────────────────────

@frappe.whitelist()
def get_ops_context():
	"""Return user context for toolbar initialization."""
	user = frappe.session.user
	roles = frappe.get_roles(user)
	company = frappe.defaults.get_user_default("Company") or ""

	wh_filters = {"is_group": 0, "disabled": 0}
	if company:
		wh_filters["company"] = company

	warehouses = frappe.get_all(
		"Warehouse",
		filters=wh_filters,
		pluck="name",
		order_by="name",
	)

	is_manager = any(r in roles for r in ["Service Manager", "System Manager", "Sales Manager"])

	return {
		"user": user,
		"user_fullname": frappe.utils.get_fullname(user),
		"roles": roles,
		"company": company,
		"warehouses": warehouses,
		"is_manager": is_manager,
	}


# ── Ticket Queue ──────────────────────────────────────────────────────────────

@frappe.whitelist()
def get_ticket_queue(warehouse=None, search=None, date_from=None, date_to=None, stage_filter="active"):
	"""Return annotated SR list for the sidebar ticket queue."""
	frappe.has_permission("Service Request", "read", throw=True)

	if not date_from:
		date_from = add_days(nowdate(), -60)
	if not date_to:
		date_to = nowdate()

	filters = [
		["service_date", ">=", date_from],
		["service_date", "<=", date_to],
		["service_order", "is", "set"],
	]

	if stage_filter == "active":
		filters.append(["decision", "in", ["Accepted", "In Service", "Completed"]])
	elif stage_filter != "all":
		filters.append(["decision", "not in", ["Cancelled", "Rejected", "Expired"]])

	if warehouse:
		filters.append(["source_warehouse", "=", warehouse])

	if search:
		filters.append(["name", "like", f"%{search}%"])

	extra_fields = []
	# analysis_confirmed and customer_confirmed are custom fields
	for cf in ("analysis_confirmed", "customer_confirmed"):
		if frappe.db.has_column("Service Request", cf):
			extra_fields.append(cf)

	sr_list = frappe.get_list(
		"Service Request",
		filters=filters,
		fields=[
			"name", "customer_name", "customer", "contact_number",
			"device_item_name", "device_item", "serial_no", "brand",
			"issue_category", "decision", "priority",
			"service_date", "expected_completion_date",
			"source_warehouse", "service_order",
		] + extra_fields,
		order_by="service_date asc, priority desc",
		limit=150,
	)

	if not sr_list:
		return []

	sr_names = [r["name"] for r in sr_list]

	# Batch: issue line summaries
	issue_summary = frappe.db.sql(
		"""
		SELECT parent,
		       COUNT(*) AS total,
		       SUM(CASE WHEN status = 'Open' THEN 1 ELSE 0 END) AS open_count
		FROM `tabSR Issue Line`
		WHERE parent IN %(names)s
		GROUP BY parent
		""",
		{"names": sr_names},
		as_dict=True,
	)
	issue_map = {r.parent: r for r in issue_summary}

	# Batch: solution line counts
	sol_summary = frappe.db.sql(
		"SELECT parent, COUNT(*) AS total FROM `tabSR Solution Line` WHERE parent IN %(names)s GROUP BY parent",
		{"names": sr_names},
		as_dict=True,
	)
	sol_map = {r.parent: cint(r.total) for r in sol_summary}

	# Batch: active job assignment counts
	ja_summary = frappe.db.sql(
		"""
		SELECT service_request, COUNT(*) AS total
		FROM `tabJob Assignment`
		WHERE service_request IN %(names)s
		  AND docstatus = 1
		  AND assignment_status != 'Cancelled'
		GROUP BY service_request
		""",
		{"names": sr_names},
		as_dict=True,
	)
	ja_map = {r.service_request: cint(r.total) for r in ja_summary}

	# Batch: QC status from linked Sales Orders
	qc_map = {}
	srs_with_so = [r for r in sr_list if r.get("service_order")]
	if srs_with_so:
		so_names = [r["service_order"] for r in srs_with_so if r.get("service_order")]
		if so_names:
			qc_rows = frappe.db.sql(
				"SELECT name, qc_status FROM `tabSales Order` WHERE name IN %(names)s",
				{"names": so_names},
				as_dict=True,
			)
			qc_by_so = {r.name: r.qc_status or "" for r in qc_rows}
			for r in sr_list:
				if r.get("service_order"):
					qc_map[r["name"]] = qc_by_so.get(r["service_order"], "")

	# Batch: check if all solutions done (for Completed/Skipped detection)
	sol_done = frappe.db.sql(
		"""
		SELECT parent,
		       SUM(CASE WHEN status IN ('Completed', 'Skipped') THEN 1 ELSE 0 END) AS done_count,
		       COUNT(*) AS total
		FROM `tabSR Solution Line`
		WHERE parent IN %(names)s
		GROUP BY parent
		""",
		{"names": sr_names},
		as_dict=True,
	)
	sol_done_map = {r.parent: (cint(r.total) > 0 and cint(r.done_count) == cint(r.total)) for r in sol_done}

	for sr in sr_list:
		n = sr["name"]
		iss = issue_map.get(n) or {}
		sr["issue_count"] = cint(iss.get("total") or 0)
		sr["open_issue_count"] = cint(iss.get("open_count") or 0)
		sr["solution_count"] = sol_map.get(n, 0)
		sr["assignment_count"] = ja_map.get(n, 0)
		sr["qc_status"] = qc_map.get(n, "")
		sr["all_solutions_done"] = sol_done_map.get(n, False)
		sr["ops_stage"] = _derive_stage(sr)

	return sr_list


def _derive_stage(sr):
	"""Derive ops stage from an SR dict (used in queue annotation)."""
	decision = sr.get("decision", "")

	if decision in ("Invoiced", "Delivered"):
		return "done"
	if decision in ("Withdrawn", "Rejected", "Cancelled"):
		return "closed"
	if decision == "Draft":
		return "draft"

	# Check QC status from linked Service Order
	qc_status = sr.get("qc_status") or ""

	# Completed SR with QC Pass = ready for invoice
	if decision == "Completed" and qc_status == "Pass":
		return "invoice"

	# QC Fail = rework (floor manager reassigns)
	if qc_status == "Fail":
		return "rework"

	# QC Awaiting = in QC stage
	if qc_status == "Awaiting":
		return "qc"

	# Completed but QC not yet started
	if decision == "Completed":
		return "qc"

	# Accepted or In Service — normal progression
	if not cint(sr.get("analysis_confirmed")) or cint(sr.get("open_issue_count", 1)) > 0:
		return "analysis"
	if not cint(sr.get("customer_confirmed")):
		return "confirm"
	if not cint(sr.get("solution_count")):
		return "solutions"
	if not cint(sr.get("assignment_count")):
		return "assign"

	# Check if all solutions completed → auto QC
	all_solutions_done = sr.get("all_solutions_done", False)
	if all_solutions_done:
		return "qc"

	return "repair"


# ── Ticket Detail ─────────────────────────────────────────────────────────────

@frappe.whitelist()
def get_ticket_detail(sr_name):
	"""Return full SR data with child tables and computed ops_stage."""
	frappe.has_permission("Service Request", sr_name, "read", throw=True)

	sr = frappe.get_doc("Service Request", sr_name)

	if not sr.service_order:
		frappe.throw(_("This Service Request has no Service Order. It cannot be managed in the Ops Hub."))

	customer_info = {}
	if sr.customer:
		contacts = frappe.get_all(
			"Contact",
			filters={"link_doctype": "Customer", "link_name": sr.customer},
			fields=["email_id", "mobile_no"],
			limit=1,
		)
		customer_info = contacts[0] if contacts else {}

	issue_lines = [
		{
			"name": row.name,
			"issue_category": row.issue_category,
			"reported_by": row.reported_by,
			"description": row.description or "",
			"status": row.status,
			"deleted_reason": row.deleted_reason or "",
			"deleted_by": row.deleted_by or "",
			"deleted_at": str(row.deleted_at) if row.deleted_at else "",
		}
		for row in sr.get("issue_lines", [])
	]

	solution_lines = [
		{
			"name": row.name,
			"repair_solution": row.repair_solution,
			"issue_category": row.issue_category,
			"solution_code": row.solution_code or "",
			"estimated_minutes": row.estimated_minutes,
			"requires_spare": cint(row.requires_spare),
			"status": row.status,
			"technician_remarks": row.technician_remarks or "",
			"cancel_reason": row.get("cancel_reason") or "",
		}
		for row in sr.get("solution_lines", [])
	]

	spare_lines = [
		{
			"name": row.name,
			"spare_item": row.spare_item,
			"item_name": row.item_name or "",
			"qty": row.qty,
			"uom": row.uom or "Nos",
			"rate": row.rate,
			"amount": row.amount,
			"status": row.status,
			"repair_solution": row.repair_solution or "",
			"remarks": row.get("remarks") or "",
		}
		for row in sr.get("spare_lines", [])
	]

	assignments = frappe.get_all(
		"Job Assignment",
		filters={"service_request": sr_name, "docstatus": 1},
		fields=[
			"name", "service_engineer", "job_type", "assignment_status",
			"assignment_date", "priority", "estimated_hours", "actual_hours",
			"work_performed", "technician_remarks", "repair_outcome", "assignment_type",
		],
		order_by="assignment_date asc, creation asc",
	)
	for a in assignments:
		if a.service_engineer:
			a["engineer_display"] = frappe.db.get_value("Employee", a.service_engineer, "employee_name") or a.service_engineer
		else:
			a["engineer_display"] = ""

	# Fetch QC status from linked Service Order
	qc_status = ""
	qc_checked_by = ""
	qc_datetime = ""
	qc_checklist = []
	so_workflow_state = ""
	if sr.service_order:
		so_data = frappe.db.get_value(
			"Sales Order", sr.service_order,
			["qc_status", "qc_checked_by", "qc_datetime", "workflow_state"],
			as_dict=True,
		) or {}
		qc_status = so_data.get("qc_status") or ""
		qc_checked_by = so_data.get("qc_checked_by") or ""
		qc_datetime = str(so_data.get("qc_datetime") or "")
		so_workflow_state = so_data.get("workflow_state") or ""

		# Fetch QC checklist from SO
		qc_rows = frappe.get_all(
			"GoFix QC Checklist",
			filters={"parent": sr.service_order},
			fields=["name", "check_name", "result", "remarks"],
			order_by="idx asc",
		)
		qc_checklist = qc_rows

	# Fetch status log (timeline)
	status_log = [
		{
			"from_status": row.from_status,
			"to_status": row.to_status,
			"changed_by": row.changed_by,
			"changed_by_name": frappe.utils.get_fullname(row.changed_by) if row.changed_by else "",
			"changed_at": str(row.changed_at) if row.changed_at else "",
			"hours_in_prev": flt(row.get("time_in_previous_status_hours")),
		}
		for row in sr.get("status_log", [])
	]

	all_solutions_done = (
		len(solution_lines) > 0
		and all(s["status"] in ("Completed", "Skipped") for s in solution_lines)
	)

	sr_dict = {
		"decision": sr.decision,
		"analysis_confirmed": cint(sr.get("analysis_confirmed")),
		"customer_confirmed": cint(sr.get("customer_confirmed")),
		"open_issue_count": sum(1 for i in issue_lines if i["status"] == "Open"),
		"active_issue_count": sum(1 for i in issue_lines if i["status"] != "Deleted"),
		"solution_count": len(solution_lines),
		"assignment_count": len([a for a in assignments if a.assignment_status != "Cancelled"]),
		"qc_status": qc_status,
		"all_solutions_done": all_solutions_done,
	}

	return {
		"name": sr.name,
		"decision": sr.decision,
		"status": sr.status or sr.decision,
		"priority": sr.priority,
		"customer": sr.customer,
		"customer_name": sr.customer_name,
		"customer_type": sr.get("customer_type") or "",
		"contact_number": sr.contact_number or "",
		"alternate_contact": sr.get("alternate_contact") or "",
		"email": sr.get("email") or customer_info.get("email_id", ""),
		"device_item": sr.device_item,
		"device_item_name": sr.device_item_name or "",
		"serial_no": sr.serial_no or "",
		"brand": sr.brand or "",
		"device_condition": sr.device_condition or "",
		"actual_imei": sr.get("actual_imei") or "",
		"mode_of_service": sr.get("mode_of_service") or "",
		"issue_category": sr.issue_category or "",
		"issue_description": sr.issue_description or "",
		"warranty_status": sr.warranty_status or "",
		"warranty_plan_name": sr.get("warranty_plan_name") or "",
		"warranty_expiry_date": str(sr.get("warranty_expiry_date") or ""),
		"service_date": str(sr.service_date) if sr.service_date else "",
		"received_datetime": str(sr.get("received_datetime") or ""),
		"expected_completion_date": str(sr.expected_completion_date) if sr.expected_completion_date else "",
		"actual_completion_date": str(sr.get("actual_completion_date") or ""),
		"source_warehouse": sr.source_warehouse or "",
		"estimated_cost": flt(sr.estimated_cost),
		"total_estimated_cost": flt(sr.get("total_estimated_cost") or 0),
		"analysis_confirmed": cint(sr.get("analysis_confirmed")),
		"customer_confirmed": cint(sr.get("customer_confirmed")),
		"confirmation_sent_at": str(sr.get("confirmation_sent_at") or ""),
		"service_order": sr.service_order or "",
		"service_invoice": sr.get("service_invoice") or "",
		"service_engineer": sr.get("service_engineer") or "",
		"assigned_to_user": sr.get("assigned_to_user") or "",
		"is_repeat_complaint": cint(sr.get("is_repeat_complaint")),
		"previous_service_request": sr.get("previous_service_request") or "",
		"repeat_complaint_count": cint(sr.get("repeat_complaint_count")),
		"internal_remarks": sr.get("internal_remarks") or "",
		"customer_remarks": sr.get("customer_remarks") or "",
		"advance_amount": flt(sr.get("advance_amount")),
		"customer_info": customer_info,
		"issue_lines": issue_lines,
		"solution_lines": solution_lines,
		"spare_lines": spare_lines,
		"assignments": assignments,
		"status_log": status_log,
		"qc_status": qc_status,
		"qc_checked_by": qc_checked_by,
		"qc_datetime": qc_datetime,
		"qc_checklist": qc_checklist,
		"so_workflow_state": so_workflow_state,
		"all_solutions_done": all_solutions_done,
		"ops_stage": _derive_stage(sr_dict),
	}


# ── Step 1: Technical Analysis ────────────────────────────────────────────────

@frappe.whitelist()
def save_issue_lines(sr_name, issues_json):
	"""Save issue lines identified during technical analysis.
	Preserves soft-deleted rows — only replaces active (non-Deleted) rows.
	"""
	frappe.has_permission("Service Request", sr_name, "write", throw=True)

	issues = json.loads(issues_json) if isinstance(issues_json, str) else issues_json

	sr = frappe.get_doc("Service Request", sr_name)
	sr.flags.ignore_validate_update_after_submit = True
	sr.flags.ignore_mandatory = True

	# Preserve deleted rows
	deleted_rows = [row.as_dict() for row in sr.get("issue_lines", []) if row.status == "Deleted"]

	sr.set("issue_lines", [])
	# Re-add deleted rows first
	for drow in deleted_rows:
		sr.append("issue_lines", {
			"issue_category": drow.get("issue_category"),
			"reported_by": drow.get("reported_by", "Technician"),
			"description": drow.get("description", ""),
			"status": "Deleted",
			"deleted_reason": drow.get("deleted_reason", ""),
			"deleted_by": drow.get("deleted_by", ""),
			"deleted_at": drow.get("deleted_at"),
		})
	# Then add active rows
	for iss in issues:
		sr.append("issue_lines", {
			"issue_category": iss.get("issue_category"),
			"reported_by": iss.get("reported_by", "Technician"),
			"description": iss.get("description", ""),
			"status": iss.get("status", "Open"),
		})

	sr.save()
	frappe.db.commit()
	active_count = sum(1 for r in sr.issue_lines if r.status != "Deleted")
	return {"ok": True, "issue_count": active_count}


@frappe.whitelist()
def delete_issue_line(sr_name, issue_row_name, reason):
	"""Soft-delete an issue line — marks as Deleted with reason, user, and timestamp."""
	frappe.has_permission("Service Request", sr_name, "write", throw=True)

	if not reason or not reason.strip():
		frappe.throw(_("A reason is required to delete an issue."))

	sr = frappe.get_doc("Service Request", sr_name)
	sr.flags.ignore_validate_update_after_submit = True
	sr.flags.ignore_mandatory = True

	found = False
	for row in sr.get("issue_lines", []):
		if row.name == issue_row_name:
			row.status = "Deleted"
			row.deleted_reason = reason.strip()
			row.deleted_by = frappe.session.user
			row.deleted_at = frappe.utils.now_datetime()
			found = True
			break

	if not found:
		frappe.throw(_("Issue line {0} not found.").format(issue_row_name))

	sr.save()
	frappe.db.commit()
	return {"ok": True}


@frappe.whitelist()
def confirm_analysis(sr_name):
	"""Confirm the technical analysis — moves all Open issues to In Progress."""
	frappe.has_permission("Service Request", sr_name, "write", throw=True)

	sr = frappe.get_doc("Service Request", sr_name)
	active_issues = [r for r in sr.get("issue_lines", []) if r.status != "Deleted"]
	if not active_issues:
		frappe.throw(_("Add at least one issue before confirming analysis."))

	sr.flags.ignore_validate_update_after_submit = True
	sr.flags.ignore_mandatory = True
	for row in sr.issue_lines:
		if row.status == "Open":
			row.status = "In Progress"

	sr.save()
	frappe.db.commit()

	if frappe.db.has_column("Service Request", "analysis_confirmed"):
		frappe.db.set_value("Service Request", sr_name, "analysis_confirmed", 1, update_modified=False)
		frappe.db.commit()

	_log_ops_stage(sr_name, "analysis", "confirm")
	frappe.db.commit()

	return {"ok": True, "stage": "confirm"}


# ── Step 2: Customer Confirmation ─────────────────────────────────────────────

@frappe.whitelist()
def send_confirmation_whatsapp(sr_name):
	"""Send a WhatsApp confirmation message to the customer with issue summary & estimate."""
	frappe.has_permission("Service Request", sr_name, "write", throw=True)

	sr = frappe.get_doc("Service Request", sr_name)
	if not sr.contact_number:
		frappe.throw(_("No contact number on Service Request {0}.").format(sr_name))

	issue_text = ", ".join(
		i.issue_category
		for i in sr.get("issue_lines", [])
		if i.issue_category and i.status not in ("Cancelled", "Not Reproducible")
	) or sr.issue_category or _("Device issue")

	sent = False
	try:
		settings = None
		try:
			settings = frappe.get_cached_doc("CH WhatsApp Settings")
			if not settings.enabled:
				settings = None
		except frappe.DoesNotExistError:
			pass

		if settings:
			from ch_item_master.ch_core.whatsapp import send_template_message

			send_template_message(
				phone=sr.contact_number,
				template_name=getattr(settings, "gofix_estimate_approval", "") or "gofix_estimate_approval",
				body_values={
					"1": sr.customer_name or "Customer",
					"2": sr.name,
					"3": sr.device_item_name or "",
					"4": issue_text,
					"5": str(flt(sr.estimated_cost or sr.get("total_estimated_cost") or 0)),
				},
				customer_name=sr.customer_name,
				ref_doctype="Service Request",
				ref_name=sr.name,
			)
			sent = True
	except Exception:
		frappe.log_error(
			title="GoFix Ops Hub — WhatsApp Send Failure",
			message=frappe.get_traceback(),
		)

	if frappe.db.has_column("Service Request", "confirmation_sent_at"):
		frappe.db.set_value(
			"Service Request", sr_name,
			"confirmation_sent_at", now_datetime(),
			update_modified=False,
		)
		frappe.db.commit()

	return {"ok": True, "whatsapp_sent": sent}


@frappe.whitelist()
def mark_customer_confirmed(sr_name):
	"""Mark customer as having confirmed the estimate and issues list."""
	frappe.has_permission("Service Request", sr_name, "write", throw=True)

	if frappe.db.has_column("Service Request", "customer_confirmed"):
		frappe.db.set_value("Service Request", sr_name, "customer_confirmed", 1, update_modified=True)
		frappe.db.commit()

	_log_ops_stage(sr_name, "confirm", "solutions")
	frappe.db.commit()

	return {"ok": True, "stage": "solutions"}


# ── Step 3: Solution Assignment ───────────────────────────────────────────────

@frappe.whitelist()
def get_solutions_for_issue(issue_category):
	"""Return active repair solutions for an issue category."""
	solutions = frappe.get_all(
		"Repair Solution",
		filters={"issue_category": issue_category, "is_active": 1},
		fields=[
			"name", "solution_name", "solution_code", "estimated_minutes",
			"requires_spare", "minimum_grade", "skill_level", "description",
		],
		order_by="solution_name",
	)

	for sol in solutions:
		if sol.minimum_grade:
			grade = frappe.db.get_value(
				"Technician Grade", sol.minimum_grade,
				["grade_name", "grade_level"], as_dict=True,
			)
			sol["grade_display"] = f"L{grade.grade_level} — {grade.grade_name}" if grade else sol.minimum_grade
		else:
			sol["grade_display"] = "Any"

		spares = []
		if sol.requires_spare:
			spares = frappe.get_all(
				"Solution Spare Mapping",
				filters={"repair_solution": sol.name, "is_active": 1},
				fields=["spare_item", "item_name", "default_qty", "uom", "is_mandatory"],
			)
		sol["spares"] = spares

	return solutions


@frappe.whitelist()
def quick_create_solution(solution_name, issue_category, estimated_minutes=30, requires_spare=0, description=""):
	"""Quick-create a Repair Solution from the Ops Hub solutions step."""
	frappe.only_for(["Service Manager", "System Manager", "GoFix Floor Manager"])

	solution_name = (solution_name or "").strip()
	if not solution_name:
		frappe.throw(_("Solution name is required."))
	if not issue_category:
		frappe.throw(_("Issue category is required."))

	# Check if already exists
	if frappe.db.exists("Repair Solution", solution_name):
		return {"name": solution_name, "exists": True}

	doc = frappe.new_doc("Repair Solution")
	doc.solution_name = solution_name
	doc.issue_category = issue_category
	doc.estimated_minutes = cint(estimated_minutes) or 30
	doc.requires_spare = cint(requires_spare)
	doc.description = description or ""
	doc.is_active = 1
	doc.insert()
	frappe.db.commit()

	return {
		"name": doc.name,
		"solution_name": doc.solution_name,
		"solution_code": doc.solution_code or "",
		"issue_category": doc.issue_category,
		"estimated_minutes": doc.estimated_minutes,
		"requires_spare": doc.requires_spare,
		"exists": False,
	}


@frappe.whitelist()
def save_solution_assignment(sr_name, solutions_json):
	"""Save solution lines to the Service Request."""
	frappe.has_permission("Service Request", sr_name, "write", throw=True)

	solutions = json.loads(solutions_json) if isinstance(solutions_json, str) else solutions_json

	sr = frappe.get_doc("Service Request", sr_name)
	sr.flags.ignore_validate_update_after_submit = True
	sr.set("solution_lines", [])
	sr.set("spare_lines", [])
	sr.flags.ignore_mandatory = True

	for sol in solutions:
		sr.append("solution_lines", {
			"repair_solution": sol.get("repair_solution"),
			"issue_category": sol.get("issue_category"),
			"solution_code": sol.get("solution_code", ""),
			"estimated_minutes": cint(sol.get("estimated_minutes", 0)),
			"requires_spare": cint(sol.get("requires_spare", 0)),
			"status": "Planned",
		})

		if sol.get("auto_add_spares"):
			for sp in frappe.get_all(
				"Solution Spare Mapping",
				filters={"repair_solution": sol.get("repair_solution"), "is_active": 1},
				fields=["spare_item", "item_name", "default_qty", "uom"],
			):
				spare_rate = flt(
					frappe.db.get_value("Item Price", {"item_code": sp.spare_item, "selling": 1}, "price_list_rate")
				) or flt(frappe.db.get_value("Item", sp.spare_item, "standard_rate"))
				sr.append("spare_lines", {
					"repair_solution": sol.get("repair_solution"),
					"issue_category": sol.get("issue_category"),
					"spare_item": sp.spare_item,
					"item_name": sp.item_name,
					"qty": sp.default_qty or 1,
					"uom": sp.uom,
					"rate": spare_rate,
					"amount": (sp.default_qty or 1) * spare_rate,
					"status": "Planned",
				})

	sr.save()
	frappe.db.commit()

	_log_ops_stage(sr_name, "solutions", "assign")
	frappe.db.commit()

	return {"ok": True, "solution_count": len(sr.solution_lines), "stage": "assign"}


# ── Step 4: Technician Assignment ─────────────────────────────────────────────

@frappe.whitelist()
def get_technicians_for_grade(minimum_grade=None, issue_category=None):
	"""Return active technicians, filtered by minimum grade level, with workload."""
	employees = frappe.get_all(
		"Employee",
		filters={"status": "Active"},
		fields=["name", "employee_name", "technician_grade", "designation"],
		order_by="employee_name",
	)

	req_level = 0
	if minimum_grade:
		req_level = cint(frappe.db.get_value("Technician Grade", minimum_grade, "grade_level") or 0)

	result = []
	for emp in employees:
		emp_level = 0
		if emp.technician_grade:
			emp_level = cint(frappe.db.get_value("Technician Grade", emp.technician_grade, "grade_level") or 0)

		if req_level and emp_level < req_level:
			continue

		if emp.technician_grade:
			grade = frappe.db.get_value(
				"Technician Grade", emp.technician_grade,
				["grade_name", "grade_level"], as_dict=True,
			)
			emp["grade_display"] = f"L{grade.grade_level} — {grade.grade_name}" if grade else emp.technician_grade
		else:
			emp["grade_display"] = "Ungraded"

		emp["active_jobs"] = frappe.db.count("Job Assignment", {
			"service_engineer": emp.name,
			"docstatus": 1,
			"assignment_status": ["in", ["Open", "In Progress"]],
		})

		result.append(emp)

	return result


@frappe.whitelist()
def assign_technician(sr_name, technician, job_type="Repair", estimated_hours=None):
	"""Create a submitted Job Assignment for the SR."""
	frappe.has_permission("Job Assignment", "create", throw=True)

	sr = frappe.get_doc("Service Request", sr_name)
	if not sr.service_order:
		frappe.throw(
			_("No Service Order found for {0}. Please accept the Service Request first.").format(sr_name)
		)

	ja = frappe.new_doc("Job Assignment")
	ja.service_order = sr.service_order
	ja.service_request = sr_name
	ja.service_engineer = technician
	ja.job_type = job_type
	ja.assignment_type = "Technician Assignment"
	ja.assigned_by = frappe.session.user
	ja.priority = sr.priority
	if estimated_hours:
		ja.estimated_hours = flt(estimated_hours)

	ja.insert()
	ja.submit()
	frappe.db.commit()

	_log_ops_stage(sr_name, "assign", "repair")
	frappe.db.commit()

	return {"ok": True, "job_assignment": ja.name, "stage": "repair"}


# ── Step 5: Repair Execution ──────────────────────────────────────────────────

@frappe.whitelist()
def update_solution_status(sr_name, solution_row_name, status, remarks=""):
	"""Update a solution line status during repair."""
	frappe.has_permission("Service Request", sr_name, "write", throw=True)

	valid = ("Planned", "In Progress", "Completed", "Skipped", "Cancelled")
	if status not in valid:
		frappe.throw(_("Invalid status. Must be one of: {0}").format(", ".join(valid)))

	update_fields = {"status": status, "technician_remarks": remarks}
	if status == "Cancelled":
		update_fields["cancel_reason"] = remarks

	frappe.db.set_value(
		"SR Solution Line",
		solution_row_name,
		update_fields,
		update_modified=True,
	)
	frappe.db.commit()
	return {"ok": True}


@frappe.whitelist()
def mark_spare_damaged(sr_name, spare_row_name, remarks=""):
	"""Mark a spare part as damaged/unusable with a mandatory comment."""
	frappe.has_permission("Service Request", sr_name, "write", throw=True)

	if not remarks or not remarks.strip():
		frappe.throw(_("Please provide a reason for marking the spare as damaged."))

	frappe.db.set_value(
		"SR Spare Line",
		spare_row_name,
		{"status": "Damaged", "remarks": remarks.strip()},
		update_modified=True,
	)
	frappe.db.commit()
	return {"ok": True}


@frappe.whitelist()
def add_spare_to_ticket(sr_name, spare_item, qty, rate=0, repair_solution=None):
	"""Append a spare part used during repair to the SR spare_lines."""
	frappe.has_permission("Service Request", sr_name, "write", throw=True)

	if not spare_item or not frappe.db.exists("Item", spare_item):
		frappe.throw(_("Please select a valid spare part."))

	qty = flt(qty)
	if qty <= 0:
		frappe.throw(_("Quantity must be greater than zero."))

	item = frappe.db.get_value("Item", spare_item, ["item_name", "stock_uom"], as_dict=True)
	rate = flt(rate)

	# Auto-fetch selling price if rate not provided
	if not rate:
		rate = flt(
			frappe.db.get_value("Item Price", {"item_code": spare_item, "selling": 1}, "price_list_rate")
		) or flt(frappe.db.get_value("Item", spare_item, "standard_rate"))

	sr = frappe.get_doc("Service Request", sr_name)
	sr.flags.ignore_validate_update_after_submit = True
	sr.flags.ignore_mandatory = True
	sr.append("spare_lines", {
		"repair_solution": repair_solution or "",
		"spare_item": spare_item,
		"item_name": item.item_name or spare_item,
		"qty": qty,
		"uom": item.stock_uom or "Nos",
		"rate": rate,
		"amount": qty * rate,
		"status": "Consumed",
	})

	sr.save()
	frappe.db.commit()
	return {"ok": True}


@frappe.whitelist()
def handoff_to_technician(sr_name, new_technician, job_type="Repair", reason=""):
	"""Create an additional Job Assignment (Technician Changed) for a handoff."""
	frappe.has_permission("Job Assignment", "create", throw=True)

	sr = frappe.get_doc("Service Request", sr_name)
	if not sr.service_order:
		frappe.throw(_("No Service Order linked to {0}.").format(sr_name))

	ja = frappe.new_doc("Job Assignment")
	ja.service_order = sr.service_order
	ja.service_request = sr_name
	ja.service_engineer = new_technician
	ja.job_type = job_type
	ja.assignment_type = "Technician Changed"
	ja.assigned_by = frappe.session.user
	ja.priority = sr.priority
	ja.comments = reason

	ja.insert()
	ja.submit()
	frappe.db.commit()

	return {"ok": True, "job_assignment": ja.name}


# ── Helpers ────────────────────────────────────────────────────────────────────

@frappe.whitelist()
def get_issue_categories():
	"""Return all active issue categories."""
	return frappe.get_all(
		"Issue Category",
		filters={"is_active": 1} if frappe.db.has_column("Issue Category", "is_active") else {},
		pluck="name",
		order_by="name",
	)


# ── Step 6: Submit for QC ─────────────────────────────────────────────────────

@frappe.whitelist()
def submit_for_qc(sr_name):
	"""Mark all solutions as completed and trigger QC on the Service Order.

	This calls the existing workflow: sets qc_status=Awaiting on the SO and
	populates the QC checklist template.
	"""
	frappe.only_for(["Service Manager", "System Manager", "Service Engineer"])

	sr = frappe.get_doc("Service Request", sr_name)
	if not sr.service_order:
		frappe.throw(_("No Service Order linked to {0}. Cannot submit for QC.").format(sr_name))

	# Mark remaining In-Progress / Planned solutions as Completed (skip Cancelled)
	sr.flags.ignore_validate_update_after_submit = True
	sr.flags.ignore_mandatory = True
	for row in sr.get("solution_lines", []):
		if row.status in ("Planned", "In Progress"):
			row.status = "Completed"
	sr.save()

	# Complete any open Job Assignments so QC guard passes
	open_jobs = frappe.get_all(
		"Job Assignment",
		filters={
			"service_request": sr_name,
			"docstatus": 1,
			"assignment_status": ["in", ["Open", "In Progress"]],
		},
		pluck="name",
	)
	for ja_name in open_jobs:
		frappe.db.set_value("Job Assignment", ja_name, {
			"assignment_status": "Completed",
			"repair_outcome": "Repaired",
			"work_performed": "Completed via Ops Hub QC submission",
		}, update_modified=True)

	# Trigger QC on the Sales Order using the existing workflow helper
	so = frappe.get_doc("Sales Order", sr.service_order)

	if not getattr(so, "is_service_order", False):
		frappe.throw(_("{0} is not a Service Order.").format(sr.service_order))

	from gofix.overrides.sales_order import move_service_order_to_qc_if_ready

	move_service_order_to_qc_if_ready(so)
	frappe.db.commit()

	_log_ops_stage(sr_name, "repair", "qc")
	frappe.db.commit()

	return {
		"ok": True,
		"qc_status": frappe.db.get_value("Sales Order", sr.service_order, "qc_status") or "Awaiting",
		"stage": "qc",
	}


@frappe.whitelist()
def save_qc_results(sr_name, checklist_json):
	"""Save QC checklist results on the linked Sales Order."""
	frappe.only_for(["Service Manager", "System Manager"])

	sr = frappe.get_doc("Service Request", sr_name)
	if not sr.service_order:
		frappe.throw(_("No Service Order linked to {0}.").format(sr_name))

	checklist = json.loads(checklist_json) if isinstance(checklist_json, str) else checklist_json

	so = frappe.get_doc("Sales Order", sr.service_order)
	for row in so.get("qc_checklist", []):
		for entry in checklist:
			if row.name == entry.get("name") or row.check_name == entry.get("check_name"):
				row.result = entry.get("result", row.result)
				row.remarks = entry.get("remarks", row.remarks or "")

	so.save()
	frappe.db.commit()
	return {"ok": True}


@frappe.whitelist()
def complete_qc(sr_name, qc_result):
	"""Mark QC as Pass or Fail on the Service Order.

	Pass: triggers SR → Completed, sends to invoice.
	Fail: sets qc_status=Fail, ops stage becomes 'rework'.
	"""
	frappe.only_for(["Service Manager", "System Manager"])

	if qc_result not in ("Pass", "Fail"):
		frappe.throw(_("QC result must be Pass or Fail."))

	sr = frappe.get_doc("Service Request", sr_name)
	if not sr.service_order:
		frappe.throw(_("No Service Order linked to {0}.").format(sr_name))

	so = frappe.get_doc("Sales Order", sr.service_order)
	so.db_set("qc_status", qc_result, update_modified=True)
	so.db_set("qc_checked_by", frappe.session.user, update_modified=False)
	so.db_set("qc_datetime", now_datetime(), update_modified=False)

	if qc_result == "Pass":
		so.db_set("workflow_state", "QC Pass", update_modified=False)
		# The existing hook update_service_request_on_qc should fire,
		# but set Completed explicitly as safety net:
		if sr.decision != "Completed":
			frappe.db.set_value("Service Request", sr_name, "decision", "Completed", update_modified=True)
			frappe.db.set_value("Service Request", sr_name, "status", "Completed", update_modified=False)
	else:
		so.db_set("workflow_state", "QC Fail", update_modified=False)

	frappe.db.commit()

	stage = "invoice" if qc_result == "Pass" else "rework"
	_log_ops_stage(sr_name, "qc", stage)
	frappe.db.commit()

	return {"ok": True, "qc_result": qc_result, "stage": stage}


# ── Step 7: Invoice / Rework ──────────────────────────────────────────────────

@frappe.whitelist()
def get_invoice_summary(sr_name):
	"""Return billing summary: service items, spares, total cost for POS.
	Returns two cost views:
	  - customer cost: consumed spares only
	  - company cost: all spares including damaged
	"""
	frappe.has_permission("Service Request", sr_name, "read", throw=True)

	sr = frappe.get_doc("Service Request", sr_name)

	service_items = [
		{
			"item_code": row.service_item,
			"item_name": row.service_item_name or "",
			"qty": 1,
			"rate": flt(row.rate),
			"amount": flt(row.amount or row.rate),
		}
		for row in sr.get("service_items", [])
	]

	# Customer spares: exclude damaged
	spare_items = [
		{
			"item_code": row.spare_item,
			"item_name": row.item_name or "",
			"qty": flt(row.qty),
			"rate": flt(row.rate),
			"amount": flt(row.amount or (row.qty * row.rate)),
		}
		for row in sr.get("spare_lines", [])
		if row.status != "Damaged"
	]

	# Damaged spares: company bears these
	damaged_spare_items = [
		{
			"item_code": row.spare_item,
			"item_name": row.item_name or "",
			"qty": flt(row.qty),
			"rate": flt(row.rate),
			"amount": flt(row.amount or (row.qty * row.rate)),
			"remarks": row.remarks or "",
		}
		for row in sr.get("spare_lines", [])
		if row.status == "Damaged"
	]

	service_total = sum(i["amount"] for i in service_items)
	spare_total = sum(i["amount"] for i in spare_items)
	damaged_spare_total = sum(i["amount"] for i in damaged_spare_items)
	discount = flt(sr.get("service_discount_amount") or 0)

	customer_total = service_total + spare_total - discount
	company_total = service_total + spare_total + damaged_spare_total - discount

	return {
		"service_items": service_items,
		"spare_items": spare_items,
		"damaged_spare_items": damaged_spare_items,
		"service_total": service_total,
		"spare_total": spare_total,
		"damaged_spare_total": damaged_spare_total,
		"discount": discount,
		"grand_total": customer_total,
		"customer_total": customer_total,
		"company_total": company_total,
		"service_invoice": sr.service_invoice or "",
		"warranty_status": sr.warranty_status or "",
	}


@frappe.whitelist()
def reassign_after_qc_fail(sr_name, technician, job_type="Repair", manager_notes=""):
	"""Floor manager assigns ticket back to technician after QC failure."""
	frappe.only_for(["Service Manager", "System Manager"])

	sr = frappe.get_doc("Service Request", sr_name)
	if not sr.service_order:
		frappe.throw(_("No Service Order linked to {0}.").format(sr_name))

	# Reset QC status so ticket re-enters repair flow
	so = frappe.get_doc("Sales Order", sr.service_order)
	so.db_set("qc_status", "Pending", update_modified=True)
	so.db_set("workflow_state", "Work in Progress", update_modified=False)

	# Reset solution lines that failed to Planned
	sr.flags.ignore_validate_update_after_submit = True
	sr.flags.ignore_mandatory = True
	for row in sr.get("solution_lines", []):
		if row.status in ("Completed", "Skipped"):
			row.status = "Planned"
	sr.save()

	# Create new Job Assignment
	ja = frappe.new_doc("Job Assignment")
	ja.service_order = sr.service_order
	ja.service_request = sr_name
	ja.service_engineer = technician
	ja.job_type = job_type
	ja.assignment_type = "Technician Changed"
	ja.assigned_by = frappe.session.user
	ja.priority = sr.priority
	ja.comments = f"QC Fail Rework — {manager_notes}" if manager_notes else "QC Fail Rework"

	ja.insert()
	ja.submit()
	frappe.db.commit()

	_log_ops_stage(sr_name, "rework", "repair")
	frappe.db.commit()

	return {"ok": True, "job_assignment": ja.name, "stage": "repair"}


# ── Navigation: Go back to a previous stage ────────────────────────────────────

@frappe.whitelist()
def go_back_to_stage(sr_name, target_stage):
	"""Reset flags so the SR moves back to the target stage.

	Allowed back-navigations:
	  confirm   → analysis   (reset analysis_confirmed)
	  solutions → confirm    (reset customer_confirmed)
	  assign    → solutions  (clear solution_lines)
	  repair    → assign     (cancel open Job Assignments)
	"""
	frappe.has_permission("Service Request", sr_name, "write", throw=True)

	ALLOWED = {"analysis", "confirm", "solutions", "assign"}
	if target_stage not in ALLOWED:
		frappe.throw(_("Cannot navigate back to stage: {0}").format(target_stage))

	sr = frappe.get_doc("Service Request", sr_name)
	current = _derive_stage(sr.as_dict())

	if target_stage == "analysis":
		frappe.db.set_value("Service Request", sr_name, "analysis_confirmed", 0, update_modified=False)
		_log_ops_stage(sr_name, current, "analysis")

	elif target_stage == "confirm":
		frappe.db.set_value("Service Request", sr_name, "customer_confirmed", 0, update_modified=False)
		_log_ops_stage(sr_name, current, "confirm")

	elif target_stage == "solutions":
		# Clear solution lines to re-enter solution picker
		sr.flags.ignore_validate_update_after_submit = True
		sr.flags.ignore_mandatory = True
		sr.set("solution_lines", [])
		sr.set("spare_lines", [])
		sr.save()
		_log_ops_stage(sr_name, current, "solutions")

	elif target_stage == "assign":
		# Cancel open Job Assignments so stage falls back to assign
		open_jobs = frappe.get_all(
			"Job Assignment",
			filters={
				"service_request": sr_name,
				"docstatus": 1,
				"assignment_status": ["in", ["Open", "In Progress"]],
			},
			pluck="name",
		)
		for ja_name in open_jobs:
			ja = frappe.get_doc("Job Assignment", ja_name)
			ja.flags.ignore_validate_update_after_submit = True
			ja.assignment_status = "Cancelled"
			ja.save()
		_log_ops_stage(sr_name, current, "assign")

	frappe.db.commit()
	return {"ok": True, "stage": target_stage}
