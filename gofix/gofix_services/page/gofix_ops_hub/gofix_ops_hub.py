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

from gofix.gofix_services.store_context import (
	active_company as _active_company,
	get_store_options as _get_store_options,
)

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


def _mark_sr_in_service(sr_name):
	"""Advance SR decision & workflow_state to 'In Service' if still Accepted."""
	current = frappe.db.get_value("Service Request", sr_name, "decision")
	if current in ("Draft", "Accepted"):
		updates = {"decision": "In Service", "status": "In Service"}
		if frappe.db.has_column("Service Request", "workflow_state"):
			updates["workflow_state"] = "In Service"
		frappe.db.set_value("Service Request", sr_name, updates, update_modified=True)


def _log_ops_stage(sr_name, from_stage, to_stage):
	"""Append a GoFix Status Log row for ops-hub stage transitions."""
	from frappe.utils import time_diff_in_hours

	sr = frappe.get_doc("Service Request", sr_name)
	sr.flags.ignore_validate_update_after_submit = True
	# Prevent sr.save() from triggering ensure_completion_artifacts / invoice
	# creation cascade — the Ops Hub controls stage progression explicitly.
	sr.flags.skip_completion_artifacts = True

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


def _assert_sr_permission(sr_name, ptype="read"):
	"""Validate Service Request access using the correct Frappe argument order."""
	frappe.has_permission("Service Request", ptype=ptype, doc=sr_name, throw=True)


# ── Context ───────────────────────────────────────────────────────────────────

@frappe.whitelist()
def get_ops_context(company=None) -> dict:
	"""Return user context for toolbar initialization."""
	user = frappe.session.user
	roles = frappe.get_roles(user)
	company = _active_company(company)
	stores = _get_store_options(company)

	is_manager = any(r in roles for r in ["Service Manager", "System Manager", "Sales Manager"])

	return {
		"user": user,
		"user_fullname": frappe.utils.get_fullname(user),
		"roles": roles,
		"company": company,
		"stores": stores,
		"warehouses": [store["warehouse"] for store in stores],
		"is_manager": is_manager,
	}


# ── Ticket Queue ──────────────────────────────────────────────────────────────

@frappe.whitelist()
def get_ticket_queue(warehouse=None, search=None, date_from=None, date_to=None, stage_filter="active", company=None) -> list:
	"""Return annotated SR list for the sidebar ticket queue."""
	frappe.has_permission("Service Request", "read", throw=True)
	company = _active_company(company)

	if not date_from:
		date_from = add_days(nowdate(), -60)
	if not date_to:
		date_to = nowdate()

	# When a search term is supplied, skip date-range / stage / service_order
	# restrictions so the user can find any SR by name, customer, or serial.
	if not search:
		# Submitted SRs only — POS-raised intakes land as docstatus 1 with
		# decision "Draft" and no Service Order yet; they must appear in the
		# queue (ops_stage "draft") so the hub can accept them. Docstatus-0
		# form drafts stay out.
		filters = [
			["service_date", ">=", date_from],
			["service_date", "<=", date_to],
			["docstatus", "=", 1],
		]
		if company:
			filters.append(["company", "=", company])

		if stage_filter == "active":
			filters.append(["decision", "in", ["Draft", "Accepted", "In Service", "Completed", "Invoiced"]])
		elif stage_filter != "all":
			filters.append(["decision", "not in", ["Cancelled", "Rejected", "Expired"]])

		if warehouse:
			filters.append(["source_warehouse", "=", warehouse])

	extra_fields = []
	# analysis_confirmed and customer_confirmed are custom fields
	for cf in ("analysis_confirmed", "customer_confirmed"):
		if frappe.db.has_column("Service Request", cf):
			extra_fields.append(cf)

	all_fields = [
		"name", "customer_name", "customer", "contact_number",
		"device_item_name", "device_item", "serial_no", "brand",
		"issue_category", "decision", "priority",
		"service_date", "expected_completion_date",
		"source_warehouse", "service_order",
	] + extra_fields

	if search:
		# Use SQL OR to match name, customer_name, or serial_no
		conditions = []
		if company:
			conditions.append("company = %(company)s")
		if warehouse:
			conditions.append("source_warehouse = %(warehouse)s")
		extra_clause = " AND " + " AND ".join(conditions) if conditions else ""
		sr_list = frappe.db.sql(
			f"""
			SELECT {", ".join(f"`tab{'Service Request'}`.`{f}`" for f in all_fields)}
			FROM `tabService Request`
			WHERE (
				`name` LIKE %(s)s
				OR `customer_name` LIKE %(s)s
				OR `serial_no` LIKE %(s)s
				OR `contact_number` LIKE %(s)s
			) {extra_clause}
			ORDER BY service_date ASC, priority DESC
			LIMIT 100
			""",
			{"s": f"%{search}%", "company": company or "", "warehouse": warehouse or ""},
			as_dict=True,
		)
	else:
		sr_list = frappe.get_list(
			"Service Request",
			filters=filters,
			fields=all_fields,
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
def accept_and_create_service_order(sr_name) -> dict:
	"""Express acceptance from the hub's Draft stage.

	Walks the sanctioned chain in one step — issue line (seeded from the
	header category when diagnosis hasn't added any), analysis confirmed,
	repairability Repairable, estimate v1 recorded as customer-approved —
	which births the Service Order (SAP notification→order moment). Every
	step lands in the ops stage log / estimate versions for audit.
	"""
	_assert_sr_permission(sr_name, "write")
	sr = frappe.get_doc("Service Request", sr_name)
	if sr.docstatus != 1:
		frappe.throw(_("Submit the Service Request before accepting."), title=_("Validation Error"))
	if sr.service_order:
		frappe.throw(_("Service Order {0} already exists.").format(sr.service_order), title=_("Validation Error"))

	sr.flags.ignore_validate_update_after_submit = True
	sr.flags.ignore_mandatory = True

	active_issues = [r for r in sr.get("issue_lines", []) if r.status not in ("Deleted", "Cancelled")]
	if not active_issues:
		if not sr.issue_category:
			frappe.throw(
				_("Add at least one issue (or set the Issue Category) before accepting."),
				title=_("Validation Error"),
			)
		sr.append("issue_lines", {
			"issue_category": sr.issue_category,
			"reported_by": "Customer",
			"status": "Open",
		})
		sr.save(ignore_permissions=True)

	frappe.db.set_value("Service Request", sr_name, {
		"analysis_confirmed": 1,
		"repairability_status": "Repairable",
	}, update_modified=False)
	_log_ops_stage(sr_name, "draft", "confirm")

	from gofix.gofix_services import orchestration

	estimate = orchestration.create_estimate_version(sr_name, reason=None, send_to_customer=False)
	orchestration.customer_approve_estimate(sr_name, remarks=_("Accepted at Ops Hub — express acceptance"))

	sr.reload()
	if not sr.service_order:
		frappe.throw(_("Acceptance completed but Service Order was not created — check estimate gates."))

	updates = {"decision": "Accepted", "walkin_status": "Accepted", "status": "In Service"}
	if frappe.db.has_column("Service Request", "accepted_by"):
		updates["accepted_by"] = frappe.session.user
	frappe.db.set_value("Service Request", sr_name, updates, update_modified=False)

	return {"ok": True, "service_order": sr.service_order, "estimate": estimate}


@frappe.whitelist()
def update_spare_genealogy(sr_name, spare_row_name, removed_part_serial=None,
		installed_part_serial=None, removed_part_condition=None, consume=0) -> dict:
	"""Record the removed/installed part serials and condition on a spare line
	after the physical swap — required before the ticket can close.

	With consume=1 an arrived-but-uninstalled line (Reserved/Issued/Pending —
	e.g. a spare that came in via purchase after the ticket raised an MR) is
	flipped to Consumed: recording the new serial IS the install step."""
	_assert_sr_permission(sr_name, "write")
	row = frappe.db.get_value(
		"SR Spare Line",
		{"name": spare_row_name, "parent": sr_name, "parenttype": "Service Request"},
		["name", "status"],
		as_dict=True,
	)
	if not row:
		frappe.throw(_("Spare line not found on {0}.").format(sr_name))
	updates = {}
	if removed_part_serial is not None:
		updates["removed_part_serial"] = removed_part_serial.strip()
	if installed_part_serial is not None:
		updates["installed_part_serial"] = installed_part_serial.strip()
	if removed_part_condition is not None:
		updates["removed_part_condition"] = removed_part_condition.strip()
	if cint(consume) and row.status in ("Reserved", "Issued", "Pending"):
		if not (updates.get("installed_part_serial") or "").strip():
			frappe.throw(
				_("Record the new part's serial/IMEI to install this spare."),
				title=_("New Part Serial Missing"),
			)
		updates["status"] = "Consumed"
	if updates:
		frappe.db.set_value("SR Spare Line", spare_row_name, updates, update_modified=False)
	return {"ok": True, "status": updates.get("status") or row.status}


def _assert_removed_part_details_complete(sr) -> None:
	from gofix.gofix_services.doctype.service_request.service_request import (
		missing_removed_part_details,
	)

	if isinstance(sr, str):
		sr = frappe.get_doc("Service Request", sr)

	pending = [
		(row.item_name or row.spare_item)
		for row in sr.get("spare_lines", [])
		if row.status in ("Awaiting Procurement", "Pending", "Reserved", "Issued")
	]
	if pending:
		frappe.throw(
			_("Cannot close — spare part(s) still awaited / not installed: {0}. "
			  "Install them (✎ on the spare line) or remove the line.").format(", ".join(pending)),
			title=_("Parts Not Installed"),
		)

	missing = missing_removed_part_details(sr)
	if missing:
		frappe.throw(
			_("Cannot close — part genealogy (old serial + condition, new serial) is missing "
			  "for: {0}. Record it via the spare line's genealogy (✎) button.").format(
				", ".join(missing)
			),
			title=_("Spare Serial Details Required"),
		)


@frappe.whitelist()
def get_ticket_detail(sr_name) -> dict:
	"""Return full SR data with child tables and computed ops_stage."""
	_assert_sr_permission(sr_name, "read")

	sr = frappe.get_doc("Service Request", sr_name)

	# No Service Order yet = the ticket is in its intake phase (SAP
	# notification→order pattern: the execution order is born at acceptance).
	# The payload below is null-safe for SO-less tickets, so the hub manages
	# the full lifecycle — intake, analysis, estimate — and the Service Order
	# appears once the customer accepts.

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
			"technician": row.technician or "",
			"technician_name": row.technician_name or "",
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
			"removed_part_serial": row.get("removed_part_serial") or "",
			"installed_part_serial": row.get("installed_part_serial") or "",
			"removed_part_condition": row.get("removed_part_condition") or "",
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
	# Batch-fetch employee names instead of one query per assignment
	engineer_ids = list({a.service_engineer for a in assignments if a.service_engineer})
	if engineer_ids:
		emp_rows = frappe.db.sql(
			"SELECT name, employee_name FROM `tabEmployee` WHERE name IN %(ids)s",
			{"ids": tuple(engineer_ids)},
			as_dict=True,
		)
		eng_name_map = {r.name: r.employee_name for r in emp_rows}
	else:
		eng_name_map = {}
	for a in assignments:
		a["engineer_display"] = eng_name_map.get(a.service_engineer) or a.service_engineer or ""

	# Fetch QC status from linked Service Order
	qc_status = ""
	qc_checked_by = ""
	qc_datetime = ""
	qc_checklist = []
	so_workflow_state = ""
	rework_count = 0
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
		rework_count = cint(frappe.db.get_value("Sales Order", sr.service_order, "rework_count"))

		# Fetch QC checklist from SO
		qc_rows = frappe.get_all(
			"GoFix QC Checklist",
			filters={"parent": sr.service_order},
			fields=["name", "check_name", "result", "remarks",
				"linked_solution", "fail_reason", "rework_required", "rework_iteration"],
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
		"rework_count": rework_count if sr.service_order else 0,
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
		"rework_count": rework_count if sr.service_order else 0,
		"ops_stage": _derive_stage(sr_dict),
	}


# ── Step 1: Technical Analysis ────────────────────────────────────────────────

@frappe.whitelist()
def save_issue_lines(sr_name, issues_json) -> dict:
	"""Save issue lines identified during technical analysis.
	Preserves soft-deleted rows — only replaces active (non-Deleted) rows.
	"""
	_assert_sr_permission(sr_name, "write")

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
	active_count = sum(1 for r in sr.issue_lines if r.status != "Deleted")

	# Late-identified issue: if the ticket already reached QC, pull it back
	# to repair — the new issue must be solved before QC can run.
	if sr.service_order:
		from gofix.gofix_services.doctype.service_request.service_request import (
			get_unresolved_issue_gaps,
		)

		gaps = get_unresolved_issue_gaps(sr)
		so_state = frappe.db.get_value(
			"Sales Order", sr.service_order, ["qc_status", "workflow_state"], as_dict=True
		)
		if not gaps["ready_for_qc"] and so_state and so_state.workflow_state == "QC Awaiting":
			frappe.db.set_value("Sales Order", sr.service_order, {
				"qc_status": "Pending",
				"workflow_state": "Work in Progress",
			}, update_modified=False)
			sr.add_comment(
				"Comment",
				_("Returned to repair from QC — newly identified issue(s) need solutions: {0}").format(
					", ".join(gaps["uncovered_issues"] + gaps["open_solutions"])
				),
			)
			frappe.msgprint(
				_("Ticket returned to Repair — the new issue must be solved before QC."),
				indicator="orange",
				alert=True,
			)

	return {"ok": True, "issue_count": active_count}


@frappe.whitelist()
def delete_issue_line(sr_name, issue_row_name, reason) -> dict:
	"""Soft-delete an issue line — marks as Deleted with reason, user, and timestamp."""
	_assert_sr_permission(sr_name, "write")

	if not reason or not reason.strip():
		frappe.throw(_("A reason is required to delete an issue."), title=_("Validation Error"))

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
		frappe.throw(_("Issue line {0} not found.").format(issue_row_name), title=_("Validation Error"))

	# ── Cascade-cancel orphaned solutions & spares (Fix #2) ────────────
	deleted_category = None
	cancelled_solutions = []
	for row in sr.get("issue_lines", []):
		if row.name == issue_row_name:
			deleted_category = row.issue_category
			break

	if deleted_category:
		# Check if any OTHER active issue shares this category
		other_active = any(
			row.issue_category == deleted_category
			and row.name != issue_row_name
			and row.status not in ("Deleted", "Cancelled")
			for row in sr.get("issue_lines", [])
		)
		if not other_active:
			cancelled_solutions = []
			for sol in sr.get("solution_lines", []):
				if sol.issue_category == deleted_category and sol.status not in ("Cancelled", "Completed"):
					sol.status = "Cancelled"
					sol.cancel_reason = f"Issue deleted: {reason.strip()}"
					cancelled_solutions.append(sol.repair_solution)
			for sp in sr.get("spare_lines", []):
				if sp.issue_category == deleted_category and sp.status == "Pending":
					sp.status = "Returned"

	sr.save()
	return {"ok": True, "cancelled_solutions": cancelled_solutions if deleted_category else []}


@frappe.whitelist()
def confirm_analysis(sr_name) -> dict:
	"""Confirm the technical analysis — moves all Open issues to In Progress."""
	_assert_sr_permission(sr_name, "write")

	sr = frappe.get_doc("Service Request", sr_name)
	active_issues = [r for r in sr.get("issue_lines", []) if r.status != "Deleted"]
	if not active_issues:
		frappe.throw(_("Add at least one issue before confirming analysis."), title=_("Validation Error"))

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
	return {"ok": True, "stage": "confirm"}


# ── Step 2: Customer Confirmation ─────────────────────────────────────────────

@frappe.whitelist()
def send_confirmation_whatsapp(sr_name) -> dict:
	"""Send a WhatsApp confirmation message to the customer with issue summary & estimate."""
	_assert_sr_permission(sr_name, "write")

	sr = frappe.get_doc("Service Request", sr_name)
	if not sr.contact_number:
		frappe.throw(_("No contact number on Service Request {0}.").format(sr_name), title=_("Validation Error"))

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
def mark_customer_confirmed(sr_name) -> dict:
	"""Mark customer as having confirmed the estimate and issues list."""
	_assert_sr_permission(sr_name, "write")

	if frappe.db.has_column("Service Request", "customer_confirmed"):
		frappe.db.set_value("Service Request", sr_name, "customer_confirmed", 1, update_modified=True)
		frappe.db.commit()

	_log_ops_stage(sr_name, "confirm", "solutions")
	return {"ok": True, "stage": "solutions"}


# ── Step 3: Solution Assignment ───────────────────────────────────────────────

@frappe.whitelist()
def get_solutions_for_issue(issue_category) -> dict:
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

	if not solutions:
		return solutions

	# Batch-fetch all technician grades needed
	grade_ids = list({s.minimum_grade for s in solutions if s.minimum_grade})
	grade_map = {}
	if grade_ids:
		grade_rows = frappe.db.sql(
			"SELECT name, grade_name, grade_level FROM `tabTechnician Grade` WHERE name IN %(ids)s",
			{"ids": tuple(grade_ids)},
			as_dict=True,
		)
		grade_map = {r.name: r for r in grade_rows}

	# Batch-fetch all spare mappings needed
	sol_names_needing_spares = [s.name for s in solutions if s.requires_spare]
	spares_by_sol = {}
	if sol_names_needing_spares:
		spare_rows = frappe.db.sql(
			"""
			SELECT repair_solution, spare_item, item_name, default_qty, uom, is_mandatory
			FROM `tabSolution Spare Mapping`
			WHERE repair_solution IN %(names)s AND is_active = 1
			""",
			{"names": tuple(sol_names_needing_spares)},
			as_dict=True,
		)
		for sp in spare_rows:
			spares_by_sol.setdefault(sp.repair_solution, []).append(sp)

	for sol in solutions:
		grade = grade_map.get(sol.minimum_grade)
		if grade:
			sol["grade_display"] = f"L{grade.grade_level} — {grade.grade_name}"
		else:
			sol["grade_display"] = "Any" if not sol.minimum_grade else sol.minimum_grade
		sol["spares"] = spares_by_sol.get(sol.name, [])

	return solutions


@frappe.whitelist()
def quick_create_solution(solution_name, issue_category, estimated_minutes=30, requires_spare=0, description="") -> dict:
	"""Quick-create a Repair Solution from the Ops Hub solutions step."""
	frappe.only_for(["Service Manager", "System Manager", "GoFix Floor Manager"])

	solution_name = (solution_name or "").strip()
	if not solution_name:
		frappe.throw(_("Solution name is required."), title=_("Validation Error"))
	if not issue_category:
		frappe.throw(_("Issue category is required."), title=_("Validation Error"))

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
def save_solution_assignment(sr_name, solutions_json) -> dict:
	"""Save solution lines to the Service Request.

	Preserves solutions that are already In Progress / Completed / Skipped
	(and their linked spares) so re-entering the Solutions step doesn't
	destroy work already done.  Only replaces Planned rows.

	Validates that every active (non-Deleted) issue category gets at
	least one solution before proceeding.
	"""
	_assert_sr_permission(sr_name, "write")

	solutions = json.loads(solutions_json) if isinstance(solutions_json, str) else solutions_json

	sr = frappe.get_doc("Service Request", sr_name)
	sr.flags.ignore_validate_update_after_submit = True
	sr.flags.ignore_mandatory = True

	# ── Preserve non-Planned rows (Fix #4) ─────────────────────────────
	preserve_statuses = ("In Progress", "Completed", "Skipped")
	preserved_solutions = [
		row.as_dict() for row in sr.get("solution_lines", [])
		if row.status in preserve_statuses
	]
	preserved_solution_names = {
		row.get("repair_solution") for row in preserved_solutions
	}
	preserved_spares = [
		row.as_dict() for row in sr.get("spare_lines", [])
		if row.repair_solution in preserved_solution_names
	]

	sr.set("solution_lines", [])
	sr.set("spare_lines", [])

	# Re-add preserved rows first
	for prow in preserved_solutions:
		sr.append("solution_lines", {
			"repair_solution": prow.get("repair_solution"),
			"issue_category": prow.get("issue_category"),
			"solution_code": prow.get("solution_code", ""),
			"estimated_minutes": cint(prow.get("estimated_minutes", 0)),
			"requires_spare": cint(prow.get("requires_spare", 0)),
			"technician": prow.get("technician", ""),
			"technician_name": prow.get("technician_name", ""),
			"technician_remarks": prow.get("technician_remarks", ""),
			"status": prow.get("status"),
		})
	for prow in preserved_spares:
		sr.append("spare_lines", {
			"repair_solution": prow.get("repair_solution"),
			"issue_category": prow.get("issue_category"),
			"spare_item": prow.get("spare_item"),
			"item_name": prow.get("item_name", ""),
			"qty": prow.get("qty", 1),
			"uom": prow.get("uom"),
			"rate": flt(prow.get("rate")),
			"amount": flt(prow.get("amount")),
			"status": prow.get("status", "Pending"),
		})

	# Batch-fetch spare mappings + item prices for new auto-spare solutions upfront
	new_sols_with_spares = [
		sol for sol in solutions
		if sol.get("auto_add_spares") and sol.get("repair_solution") not in preserved_solution_names
	]
	spare_mapping_by_sol = {}
	spare_price_map = {}
	spare_std_rate_map = {}
	if new_sols_with_spares:
		sol_ids = list({s.get("repair_solution") for s in new_sols_with_spares if s.get("repair_solution")})
		if sol_ids:
			all_spare_rows = frappe.db.sql(
				"""
				SELECT repair_solution, spare_item, item_name, default_qty, uom
				FROM `tabSolution Spare Mapping`
				WHERE repair_solution IN %(ids)s AND is_active = 1
				""",
				{"ids": tuple(sol_ids)},
				as_dict=True,
			)
			for sp in all_spare_rows:
				spare_mapping_by_sol.setdefault(sp.repair_solution, []).append(sp)

			all_items = list({sp.spare_item for sp in all_spare_rows if sp.spare_item})
			if all_items:
				price_rows = frappe.db.sql(
					"SELECT item_code, price_list_rate FROM `tabItem Price` WHERE item_code IN %(items)s AND selling = 1",
					{"items": tuple(all_items)},
					as_dict=True,
				)
				spare_price_map = {r.item_code: flt(r.price_list_rate) for r in price_rows}

				rate_rows = frappe.db.sql(
					"SELECT name, standard_rate FROM `tabItem` WHERE name IN %(items)s",
					{"items": tuple(all_items)},
					as_dict=True,
				)
				spare_std_rate_map = {r.name: flt(r.standard_rate) for r in rate_rows}

	# Add new solutions (skip duplicates that are already preserved)
	for sol in solutions:
		if sol.get("repair_solution") in preserved_solution_names:
			continue
		sr.append("solution_lines", {
			"repair_solution": sol.get("repair_solution"),
			"issue_category": sol.get("issue_category"),
			"solution_code": sol.get("solution_code", ""),
			"estimated_minutes": cint(sol.get("estimated_minutes", 0)),
			"requires_spare": cint(sol.get("requires_spare", 0)),
			"status": "Planned",
		})

		if sol.get("auto_add_spares"):
			from gofix.gofix_services.api import is_spare_compatible_with_device

			for sp in spare_mapping_by_sol.get(sol.get("repair_solution"), []):
				# Never auto-add a spare that doesn't fit this device
				# (brand / category / model applicability ladder).
				if not is_spare_compatible_with_device(sp.spare_item, sr.device_item):
					continue
				spare_rate = spare_price_map.get(sp.spare_item) or spare_std_rate_map.get(sp.spare_item, 0.0)
				sr.append("spare_lines", {
					"repair_solution": sol.get("repair_solution"),
					"issue_category": sol.get("issue_category"),
					"spare_item": sp.spare_item,
					"item_name": sp.item_name,
					"qty": sp.default_qty or 1,
					"uom": sp.uom,
					"rate": spare_rate,
					"amount": (sp.default_qty or 1) * spare_rate,
					"status": "Pending",
				})

	# ── Validate: every active issue must have ≥1 solution (Fix #1) ────
	active_categories = {
		row.issue_category
		for row in sr.get("issue_lines", [])
		if row.status not in ("Deleted", "Cancelled", "Not Reproducible")
	}
	covered_categories = {
		row.issue_category
		for row in sr.get("solution_lines", [])
		if row.status != "Cancelled"
	}
	missing = active_categories - covered_categories
	if missing:
		frappe.throw(
			_("Every active issue must have at least one solution. Missing: {0}").format(
				", ".join(sorted(missing))
			),
			title=_("Incomplete Solution Coverage"),
		)

	sr.save()
	frappe.db.commit()

	_log_ops_stage(sr_name, "solutions", "assign")
	return {"ok": True, "solution_count": len(sr.solution_lines), "stage": "assign"}


# ── Step 4: Technician Assignment ─────────────────────────────────────────────

@frappe.whitelist()
def get_technicians_for_grade(minimum_grade=None, issue_category=None) -> dict:
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


def _assert_technician_can_take_solutions(technician, repair_solutions) -> None:
	"""Block assignment when a technician is below a solution's minimum grade."""
	repair_solutions = list({s for s in (repair_solutions or []) if s})
	if not repair_solutions:
		return

	solution_requirements = frappe.db.sql(
		"""
		SELECT
			rs.name,
			rs.solution_name,
			rs.minimum_grade,
			tg.grade_name,
			tg.grade_level
		FROM `tabRepair Solution` rs
		LEFT JOIN `tabTechnician Grade` tg ON tg.name = rs.minimum_grade
		WHERE rs.name IN %(solutions)s
		""",
		{"solutions": tuple(repair_solutions)},
		as_dict=True,
	)
	required = [row for row in solution_requirements if row.minimum_grade]
	if not required:
		return

	emp = frappe.db.get_value(
		"Employee",
		technician,
		["name", "employee_name", "technician_grade"],
		as_dict=True,
	)
	if not emp:
		frappe.throw(_("Technician {0} not found.").format(technician), title=_("Technician Grade Required"))
	if not emp.technician_grade:
		frappe.throw(
			_("Technician {0} has no Technician Grade. Set it on the Employee record before assigning graded solutions.").format(
				emp.employee_name or technician
			),
			title=_("Technician Grade Required"),
		)

	tech_grade = frappe.db.get_value(
		"Technician Grade",
		emp.technician_grade,
		["grade_name", "grade_level"],
		as_dict=True,
	)
	tech_level = cint(tech_grade.grade_level if tech_grade else 0)
	blocked = [row for row in required if cint(row.grade_level or 0) > tech_level]
	if not blocked:
		return

	needed = max(cint(row.grade_level or 0) for row in blocked)
	solution_names = ", ".join(row.solution_name or row.name for row in blocked)
	frappe.throw(
		_(
			"Technician {0} is {1}, but {2} requires L{3} or above. "
			"Tip: uncheck the higher-grade solution(s) and assign them to a "
			"qualified technician separately — one ticket can be split across "
			"L1/L2/L3/L4 technicians, each taking the solutions their grade covers."
		).format(
			emp.employee_name or technician,
			tech_grade.grade_name if tech_grade else emp.technician_grade,
			solution_names,
			needed,
		),
		title=_("Technician Grade Mismatch"),
	)


def _solution_rows_for_assignment(sr, row_names=None) -> list:
	"""Return active SR solution rows, optionally filtered to selected child rows."""
	rows = [
		row for row in (sr.get("solution_lines") or [])
		if row.status not in ("Cancelled", "Skipped")
	]
	if row_names is None:
		return rows

	requested = set(row_names)
	selected = [row for row in rows if row.name in requested]
	missing = requested - {row.name for row in selected}
	if missing:
		frappe.throw(
			_("Selected solution rows are not active on {0}: {1}").format(
				sr.name, ", ".join(sorted(missing))
			),
			title=_("Invalid Solution Selection"),
		)
	return selected


@frappe.whitelist()
def assign_technician(sr_name, technician, job_type="Repair", estimated_hours=None) -> dict:
	"""Create a submitted Job Assignment for the SR."""
	frappe.has_permission("Job Assignment", "create", throw=True)

	sr = frappe.get_doc("Service Request", sr_name)
	if not sr.service_order:
		frappe.throw(
			_("No Service Order found for {0}. Please accept the Service Request first.").format(sr_name)
		)

	_assert_technician_can_take_solutions(
		technician,
		[row.repair_solution for row in _solution_rows_for_assignment(sr)],
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
	_mark_sr_in_service(sr_name)
	return {"ok": True, "job_assignment": ja.name, "stage": "repair"}


@frappe.whitelist()
def assign_solutions_to_technician(sr_name, solution_rows_json, technician, estimated_hours=None) -> dict:
	"""Assign specific solutions to a technician and create a Job Assignment.

	solution_rows_json: JSON array of SR Solution Line row names.
	"""
	frappe.has_permission("Job Assignment", "create", throw=True)

	solution_rows = json.loads(solution_rows_json) if isinstance(solution_rows_json, str) else solution_rows_json
	if not solution_rows:
		frappe.throw(_("Select at least one solution to assign."), title=_("Validation Error"))

	sr = frappe.get_doc("Service Request", sr_name)
	if not sr.service_order:
		frappe.throw(_("No Service Order found for {0}.").format(sr_name), title=_("Validation Error"))

	selected_rows = _solution_rows_for_assignment(sr, solution_rows)
	_assert_technician_can_take_solutions(
		technician,
		[row.repair_solution for row in selected_rows],
	)

	# Resolve technician name
	tech_name = frappe.db.get_value("Employee", technician, "employee_name") or technician

	# Stamp technician on each selected solution line
	for row in selected_rows:
		frappe.db.set_value("SR Solution Line", row.name, {
			"technician": technician,
			"technician_name": tech_name,
		}, update_modified=True)

	# Create Job Assignment for tracking
	ja = frappe.new_doc("Job Assignment")
	ja.service_order = sr.service_order
	ja.service_request = sr_name
	ja.service_engineer = technician
	ja.job_type = "Repair"
	ja.assignment_type = "Technician Assignment"
	ja.assigned_by = frappe.session.user
	ja.priority = sr.priority
	if estimated_hours:
		ja.estimated_hours = flt(estimated_hours)
	ja.insert()
	ja.submit()

	# Check if ALL solutions are now assigned
	sr.reload()
	all_assigned = all(row.technician for row in sr.get("solution_lines", []))

	if all_assigned:
		_log_ops_stage(sr_name, "assign", "repair")
		_mark_sr_in_service(sr_name)

	return {
		"ok": True,
		"job_assignment": ja.name,
		"all_assigned": all_assigned,
		"stage": "repair" if all_assigned else "assign",
	}


@frappe.whitelist()
def unassign_solution(sr_name, solution_row_name) -> dict:
	"""Remove technician assignment from a solution line."""
	_assert_sr_permission(sr_name, "write")

	frappe.db.set_value("SR Solution Line", solution_row_name, {
		"technician": "",
		"technician_name": "",
	}, update_modified=True)
	return {"ok": True}


@frappe.whitelist()
def advance_to_repair(sr_name) -> dict:
	"""Manually advance from assign to repair stage (when all solutions assigned)."""
	_assert_sr_permission(sr_name, "write")

	sr = frappe.get_doc("Service Request", sr_name)
	unassigned = [row for row in sr.get("solution_lines", []) if not row.technician and row.status != "Cancelled"]
	if unassigned:
		frappe.throw(_("All solutions must be assigned before proceeding to repair."), title=_("Validation Error"))

	_log_ops_stage(sr_name, "assign", "repair")
	_mark_sr_in_service(sr_name)
	return {"ok": True, "stage": "repair"}


# ── Step 5: Repair Execution ──────────────────────────────────────────────────

@frappe.whitelist()
def update_solution_status(sr_name, solution_row_name, status, remarks="") -> dict:
	"""Update a solution line status during repair."""
	_assert_sr_permission(sr_name, "write")

	valid = ("Planned", "In Progress", "On Hold", "Completed", "Skipped", "Cancelled")
	if status not in valid:
		frappe.throw(_("Invalid status. Must be one of: {0}").format(", ".join(valid)), title=_("Validation Error"))

	# Grade-safety gate: work can only start/finish on a solution that has an
	# assigned technician, and only while THAT technician holds the device —
	# otherwise an L1 holding the phone could "Start" L4 board work that was
	# never assigned to them.
	if status in ("In Progress", "Completed"):
		line = frappe.db.get_value(
			"SR Solution Line",
			solution_row_name,
			["technician", "repair_solution"],
			as_dict=True,
		) or frappe._dict()
		if not line.technician:
			frappe.throw(
				_("{0} has no technician assigned — assign it (Assign stage) to a "
				  "technician whose grade covers it before starting.").format(
					line.repair_solution or _("This solution")
				),
				title=_("Unassigned Solution"),
			)
		so_name = frappe.db.get_value("Service Request", sr_name, "service_order")
		active = frappe.db.get_value(
			"Job Assignment",
			{"service_order": so_name, "assignment_status": "In Progress", "docstatus": ("<", 2)},
			"service_engineer",
		) if so_name else None
		if active and active != line.technician:
			holder = frappe.db.get_value("Employee", active, "employee_name") or active
			assignee = frappe.db.get_value("Employee", line.technician, "employee_name") or line.technician
			frappe.throw(
				_("{0} is assigned to {1}, but the device is currently with {2}. "
				  "Hand off the device before working this solution.").format(
					line.repair_solution, assignee, holder
				),
				title=_("Not Your Solution"),
			)

	# Parts-readiness gate (SAP "waiting for parts" pattern): a solution
	# cannot be marked Done while a spare attributed to it is still on order
	# or fitted without the NEW part's serial recorded.
	if status == "Completed":
		_assert_solution_parts_ready(sr_name, line.repair_solution)

	if status == "On Hold":
		line = frappe.db.get_value(
			"SR Solution Line", solution_row_name, ["technician", "repair_solution"], as_dict=True
		) or frappe._dict()

	update_fields = {"status": status, "technician_remarks": remarks}
	if status == "Cancelled":
		update_fields["cancel_reason"] = remarks

	frappe.db.set_value(
		"SR Solution Line",
		solution_row_name,
		update_fields,
		update_modified=True,
	)

	# Device custody follows the work: starting takes the device (Job
	# Assignment → In Progress, single-holder rule enforced there); holding
	# releases it so another technician can work their own solution meanwhile.
	if status in ("In Progress", "On Hold") and line.get("technician"):
		_sync_job_assignment_custody(sr_name, solution_row_name, line.technician, status)

	return {"ok": True}


def _assert_solution_parts_ready(sr_name, repair_solution) -> None:
	"""Block Done while this solution's spares are on order / uninstalled /
	missing the installed (new) part serial. Universal consumables and
	non-stock lines are exempt — they carry no serial."""
	if not repair_solution:
		return
	for row in frappe.get_all(
		"SR Spare Line",
		filters={"parent": sr_name, "parenttype": "Service Request", "repair_solution": repair_solution},
		fields=["item_name", "spare_item", "status", "installed_part_serial"],
	):
		item_flags = frappe.db.get_value(
			"Item", row.spare_item, ["is_stock_item", "gofix_universal_spare"], as_dict=True
		) or frappe._dict()
		if not item_flags.get("is_stock_item") or item_flags.get("gofix_universal_spare"):
			continue
		label = row.item_name or row.spare_item
		if row.status in ("Awaiting Procurement", "Pending", "Reserved", "Issued"):
			frappe.throw(
				_("{0} is not installed yet (status: {1}). Receive/install the part and record "
				  "its serial via the spare line's ✎ button — or put this solution On Hold so "
				  "other repairs can continue meanwhile.").format(label, _(row.status)),
				title=_("Parts Not Installed"),
			)
		if row.status == "Consumed" and not (row.installed_part_serial or "").strip():
			frappe.throw(
				_("New part serial/IMEI is missing for {0}. Record it via the spare line's "
				  "✎ button before marking this solution Done.").format(label),
				title=_("New Part Serial Missing"),
			)


def _sync_job_assignment_custody(sr_name, solution_row_name, technician, status) -> None:
	so_name = frappe.db.get_value("Service Request", sr_name, "service_order")
	if not so_name:
		return
	ja_name = frappe.db.get_value(
		"Job Assignment",
		{
			"service_order": so_name,
			"service_engineer": technician,
			"docstatus": ("<", 2),
			"assignment_status": ("not in", ("Completed", "Cancelled")),
		},
		"name",
	)
	if not ja_name:
		return
	if status == "On Hold":
		# Only release the device if this technician has nothing else running.
		other_running = frappe.db.exists(
			"SR Solution Line",
			{
				"parent": sr_name,
				"parenttype": "Service Request",
				"technician": technician,
				"status": "In Progress",
				"name": ("!=", solution_row_name),
			},
		)
		if other_running:
			return
	target = "On Hold" if status == "On Hold" else "In Progress"
	ja = frappe.get_doc("Job Assignment", ja_name)
	if ja.assignment_status == target:
		return
	ja.assignment_status = target
	ja.flags.ignore_validate_update_after_submit = True
	ja.save(ignore_permissions=True)


@frappe.whitelist()
def restart_solution_line(sr_name, solution_row_name, remarks="") -> dict:
	"""Restart a completed/skipped solution back to In Progress (used in rework).

	Allows technicians to re-open a previously completed repair item
	when QC has identified the fix was insufficient.
	"""
	_assert_sr_permission(sr_name, "write")

	current = frappe.db.get_value("SR Solution Line", solution_row_name, "status")
	if current not in ("Completed", "Skipped"):
		frappe.throw(_("Only Completed or Skipped solutions can be restarted."), title=_("Validation Error"))

	remark = f"[Restarted] {remarks}".strip() if remarks else "[Restarted]"
	prev_remarks = frappe.db.get_value("SR Solution Line", solution_row_name, "technician_remarks") or ""
	frappe.db.set_value("SR Solution Line", solution_row_name, {
		"status": "In Progress",
		"technician_remarks": (prev_remarks + "\n" + remark).strip(),
	}, update_modified=True)

	return {"ok": True}


@frappe.whitelist()
def mark_spare_damaged(sr_name, spare_row_name, remarks="") -> dict:
	"""Mark a spare part as damaged/unusable with a mandatory comment."""
	_assert_sr_permission(sr_name, "write")

	if not remarks or not remarks.strip():
		frappe.throw(_("Please provide a reason for marking the spare as damaged."), title=_("Validation Error"))

	frappe.db.set_value(
		"SR Spare Line",
		spare_row_name,
		{"status": "Damaged", "remarks": remarks.strip()},
		update_modified=True,
	)
	return {"ok": True}


@frappe.whitelist()
def add_spare_to_ticket(sr_name, spare_item, qty, rate=0, repair_solution=None,
		removed_part_serial=None, installed_part_serial=None, removed_part_condition=None) -> dict:
	"""Add a spare part to the SR.  Checks warehouse stock first.

	Returns:
	  {ok: True, status: "Reserved"}       — spare was in stock & reserved
	  {ok: True, status: "Awaiting Procurement"} — spare NOT in stock, added to cart
	"""
	_assert_sr_permission(sr_name, "write")

	if not spare_item or not frappe.db.exists("Item", spare_item):
		frappe.throw(_("Please select a valid spare part."), title=_("Validation Error"))

	qty = flt(qty)
	if qty <= 0:
		frappe.throw(_("Quantity must be greater than zero."), title=_("Validation Error"))

	item = frappe.db.get_value("Item", spare_item, ["item_name", "stock_uom", "is_stock_item"], as_dict=True)
	rate = flt(rate)

	# Auto-fetch selling price if rate not provided
	if not rate:
		rate = flt(
			frappe.db.get_value("Item Price", {"item_code": spare_item, "selling": 1}, "price_list_rate")
		) or flt(frappe.db.get_value("Item", spare_item, "standard_rate"))

	sr = frappe.get_doc("Service Request", sr_name)
	warehouse = _effective_repair_warehouse(sr)

	# ── Compatibility guard ─────────────────────────────────────────────
	# Block spares that are not compatible with the device being repaired.
	from gofix.gofix_services.api import is_spare_compatible_with_device
	if not is_spare_compatible_with_device(spare_item, sr.device_item):
		frappe.throw(
			_("Spare {0} is not compatible with device {1}.").format(
				item.get("item_name") or spare_item,
				sr.device_item_name or sr.device_item,
			),
			title=_("Incompatible Spare"),
		)

	# ── Availability check ──────────────────────────────────────────────
	avail = _get_spare_availability(spare_item, warehouse)
	is_stock = cint(item.get("is_stock_item"))
	in_stock = (avail["available_qty"] >= qty) if is_stock else True

	# When spare is in stock it is being consumed immediately for this repair.
	# "Awaiting Procurement" is set only when the part needs to be ordered first.
	status = "Consumed" if in_stock else "Awaiting Procurement"

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
		"status": status,
		# Part genealogy (KBB/KGB): old part out, new part in — drives
		# defective-return credit and OEM claim evidence.
		"removed_part_serial": (removed_part_serial or "").strip(),
		"installed_part_serial": (installed_part_serial or "").strip(),
		"removed_part_condition": (removed_part_condition or "").strip(),
	})

	sr.save()

	# P1 FIX: Create Material Issue SE for consumed spare
	if status == "Consumed":
		_spare_dict = {
			"item_code": spare_item,
			"qty": qty,
			"warehouse": warehouse,
			"serial_no": None,
			"doctype": "SR Spare Line",
			"name": sr.spare_lines[-1].name if sr.spare_lines else None,
		}
		so_name = sr.service_order
		try:
			_se = frappe.new_doc("Stock Entry")
			_se.stock_entry_type = "Material Issue"
			_se.purpose = "Material Issue"
			_se.company = frappe.db.get_value("Sales Order", so_name, "company") if so_name else (sr.company or frappe.defaults.get_user_default("Company"))
			_se.posting_date = frappe.utils.today()
			_se.custom_gofix_service_order = so_name
			_se_item = _se.append("items", {
				"item_code": _spare_dict.get("item_code"),
				"qty": flt(_spare_dict.get("qty", 1)),
				"s_warehouse": _spare_dict.get("warehouse") or (frappe.db.get_value("Sales Order", so_name, "set_warehouse") if so_name else warehouse),
			})
			if _spare_dict.get("serial_no"):
				_se_item.serial_no = _spare_dict.get("serial_no")
			_se.flags.ignore_permissions = True
			_se.insert()
			_se.submit()
			if _spare_dict.get("name"):
				frappe.db.set_value(_spare_dict.get("doctype") or "SR Spare Line", _spare_dict.get("name"), "custom_stock_entry", _se.name)
		except Exception:
			frappe.log_error(frappe.get_traceback(), f"Spare SE failed for {_spare_dict.get('item_code')} on {so_name}")

	return {"ok": True, "status": status, "available_qty": avail["available_qty"]}


@frappe.whitelist()
def get_repair_history(sr_name) -> list:
	"""Full chronological history of a repair — the "charge sheet" trail.

	Assembles every event across the repair lifecycle: intake, analysis
	stages, estimate versions, acceptance, device transfers (with logistics
	manifests), spare requests and their procurement chain (MR → PO → PR),
	technician assignments and hours, QC, invoicing. Used by the Repair
	Charge Sheet print format and available to any UI.
	"""
	_assert_sr_permission(sr_name, "read")
	sr = frappe.get_doc("Service Request", sr_name)
	events = []

	def add(at, title, detail="", ref_dt=None, ref=None):
		if not at:
			return
		at = frappe.utils.get_datetime(at)
		events.append(
			{
				"at": at,
				"title": title,
				"detail": detail,
				"ref_doctype": ref_dt,
				"ref_name": ref,
			}
		)

	# ── Intake ───────────────────────────────────────────────────────────
	add(
		sr.creation,
		"Device received at counter",
		f"{sr.customer_name or sr.customer} — {sr.device_item_name or sr.device_item or ''}"
		+ (f", serial {sr.serial_no}" if sr.serial_no else "")
		+ (f" · via {sr.walkin_source}" if sr.get("walkin_source") else ""),
		"Service Request",
		sr.name,
	)

	# ── Ops-hub stage transitions ────────────────────────────────────────
	for row in sr.get("status_log", []):
		add(
			row.changed_at,
			f"Stage: {row.from_status or '—'} → {row.to_status}",
			f"by {row.changed_by or ''}"
			+ (
				f" (after {frappe.utils.flt(row.time_in_previous_status_hours, 1)}h)"
				if row.get("time_in_previous_status_hours")
				else ""
			),
		)

	# ── Estimates ────────────────────────────────────────────────────────
	for ev in sr.get("estimate_versions", []):
		add(
			ev.get("creation"),
			f"Estimate v{ev.version_number}: {frappe.utils.fmt_money(ev.estimate_amount, currency='INR')}",
			f"labour {frappe.utils.fmt_money(ev.labor_cost or 0, currency='INR')}, "
			f"spares {frappe.utils.fmt_money(ev.spare_cost or 0, currency='INR')} — {ev.status}",
		)

	if sr.get("accepted_datetime"):
		add(sr.accepted_datetime, "Customer accepted — device left with us", f"by {sr.get('accepted_by') or ''}")

	# ── Device transfers + logistics manifests ───────────────────────────
	if sr.get("transfer_date"):
		add(
			sr.transfer_date,
			f"Device transfer initiated → {sr.transferred_to_store}",
			sr.get("transfer_reason") or "",
		)
	if sr.get("transfer_received_date"):
		add(sr.transfer_received_date, f"Device received at service center ({sr.transferred_to_store})")
	if sr.get("transfer_return_date"):
		add(sr.transfer_return_date, f"Device returned to store ({sr.source_warehouse})")

	transfer_ses = frappe.get_all(
		"Stock Entry",
		filters={
			"stock_entry_type": "Material Transfer",
			"remarks": ["like", f"%{sr.name}%"],
			"docstatus": 1,
		},
		fields=["name", "posting_date", "custom_transfer_manifest"],
	)
	for se in transfer_ses:
		if se.custom_transfer_manifest:
			tm = frappe.db.get_value(
				"CH Transfer Manifest",
				se.custom_transfer_manifest,
				["status", "driver_name", "driver", "vehicle_number", "trip", "modified"],
				as_dict=True,
			)
			if tm:
				add(
					se.posting_date,
					f"Logistics manifest {se.custom_transfer_manifest} ({tm.status})",
					f"driver {tm.driver_name or tm.driver or '—'}, vehicle {tm.vehicle_number or '—'}"
					+ (f", trip {tm.trip}" if tm.trip else ""),
					"CH Transfer Manifest",
					se.custom_transfer_manifest,
				)

	# ── Spares + procurement chain ───────────────────────────────────────
	mr_names = {sl.material_request for sl in sr.get("spare_lines", []) if sl.get("material_request")}
	if frappe.db.has_column("Material Request", "service_request"):
		mr_names.update(
			frappe.get_all(
				"Material Request",
				filters={"service_request": sr.name, "docstatus": ["<", 2]},
				pluck="name",
			)
		)
	for sl in sr.get("spare_lines", []):
		genealogy = ""
		if sl.get("removed_part_serial") or sl.get("installed_part_serial"):
			genealogy = (
				f" | out: {sl.get('removed_part_serial') or '—'}"
				f" / in: {sl.get('installed_part_serial') or '—'}"
			)
		add(
			sl.get("creation"),
			f"Spare requested: {sl.item_name or sl.spare_item} × {frappe.utils.flt(sl.qty)}",
			f"rate {frappe.utils.fmt_money(sl.rate or 0, currency='INR')} — {sl.status}{genealogy}",
		)
	for mr_name in sorted(mr_names):
		mr = frappe.db.get_value(
			"Material Request", mr_name, ["transaction_date", "status"], as_dict=True
		)
		if mr:
			add(
				mr.transaction_date,
				f"Material Request {mr_name} ({mr.status})",
				"",
				"Material Request",
				mr_name,
			)
			pos = frappe.get_all(
				"Purchase Order Item",
				filters={"material_request": mr_name, "docstatus": 1},
				fields=["parent"],
				distinct=True,
				pluck="parent",
			)
			for po in pos:
				pod = frappe.db.get_value(
					"Purchase Order", po, ["transaction_date", "supplier", "grand_total"], as_dict=True
				)
				add(
					pod.transaction_date,
					f"Purchase Order {po}",
					f"supplier {pod.supplier}, {frappe.utils.fmt_money(pod.grand_total, currency='INR')}",
					"Purchase Order",
					po,
				)
				prs = frappe.get_all(
					"Purchase Receipt Item",
					filters={"purchase_order": po, "docstatus": 1},
					fields=["parent", "warehouse"],
					distinct=True,
				)
				for pr in prs:
					prd = frappe.db.get_value("Purchase Receipt", pr.parent, "posting_date")
					add(
						prd,
						f"Spare received: Purchase Receipt {pr.parent}",
						f"at {pr.warehouse}",
						"Purchase Receipt",
						pr.parent,
					)

	# ── Technician work ──────────────────────────────────────────────────
	for ja in frappe.get_all(
		"Job Assignment",
		filters={"service_request": sr.name},
		fields=[
			"name", "assignment_datetime", "service_engineer", "job_type",
			"assignment_status", "start_datetime", "end_datetime",
			"actual_hours", "estimated_hours", "repair_outcome",
		],
	):
		engineer = (
			frappe.db.get_value("Employee", ja.service_engineer, "employee_name")
			if ja.service_engineer
			else None
		) or ja.service_engineer or "—"
		add(
			ja.assignment_datetime,
			f"Job assigned to {engineer} ({ja.job_type})",
			f"estimated {frappe.utils.flt(ja.estimated_hours)}h" if ja.estimated_hours else "",
			"Job Assignment",
			ja.name,
		)
		if ja.start_datetime:
			add(ja.start_datetime, f"Repair work started — {engineer}", "", "Job Assignment", ja.name)
		if ja.end_datetime:
			add(
				ja.end_datetime,
				f"Repair work finished — {engineer}",
				f"{frappe.utils.flt(ja.actual_hours, 1)}h logged"
				+ (f", outcome: {ja.repair_outcome}" if ja.repair_outcome else ""),
				"Job Assignment",
				ja.name,
			)

	# ── QC + completion + billing ────────────────────────────────────────
	if sr.service_order:
		so = frappe.db.get_value(
			"Sales Order", sr.service_order, ["qc_status", "workflow_state", "modified"], as_dict=True
		)
		if so and so.qc_status:
			add(so.modified, f"QC {so.qc_status}", f"Service Order {sr.service_order} — {so.workflow_state}",
				"Sales Order", sr.service_order)
	if sr.get("actual_completion_date"):
		add(sr.actual_completion_date, "Repair completed")
	if sr.get("service_invoice"):
		inv = frappe.db.get_value(
			"Sales Invoice",
			sr.service_invoice,
			["posting_date", "grand_total", "status", "creation"],
			as_dict=True,
		)
		if inv:
			add(
				inv.creation,
				f"Invoiced {frappe.utils.fmt_money(inv.grand_total, currency='INR')} ({inv.status})",
				f"Sales Invoice {sr.service_invoice}",
				"Sales Invoice",
				sr.service_invoice,
			)

	events.sort(key=lambda e: e["at"])
	return events


def _effective_repair_warehouse(sr):
	"""Warehouse where the repair is physically happening.

	A device transferred to a service hub consumes spares AT the hub —
	availability checks, Material Issues and procurement follow the device
	instead of always hitting the origin store.
	"""
	if sr.get("transfer_status") in ("In Transit", "Received at Service Center") and sr.get(
		"transferred_to_store"
	):
		return sr.transferred_to_store
	return sr.get("current_location") or sr.source_warehouse


@frappe.whitelist()
def get_spare_availability(item_code, warehouse) -> dict:
	"""Public API: return stock availability for a spare + warehouse."""
	return _get_spare_availability(item_code, warehouse)


def _get_spare_availability(item_code, warehouse) -> dict:
	"""Check available qty for a spare in warehouse, accounting for reservations.

	available_qty = bin.actual_qty − already-reserved-on-other-tickets
	"""
	actual_qty = flt(
		frappe.db.get_value("Bin", {"item_code": item_code, "warehouse": warehouse}, "actual_qty")
	)

	# Count already committed (reserved or consumed) across all SRs whose
	# repair is effectively happening at this warehouse — transferred devices
	# reserve hub stock, not their origin store's.
	reserved_qty = flt(frappe.db.sql("""
		SELECT COALESCE(SUM(sl.qty), 0)
		FROM `tabSR Spare Line` sl
		JOIN `tabService Request` sr ON sr.name = sl.parent
		WHERE sl.spare_item = %(item)s
		  AND sl.status IN ('Reserved', 'Consumed')
		  AND sl.parenttype = 'Service Request'
		  AND (
			CASE
				WHEN sr.transfer_status IN ('In Transit', 'Received at Service Center')
					AND COALESCE(sr.transferred_to_store, '') != ''
				THEN sr.transferred_to_store
				ELSE COALESCE(NULLIF(sr.current_location, ''), sr.source_warehouse)
			END
		  ) = %(wh)s
	""", {"item": item_code, "wh": warehouse})[0][0])

	return {
		"actual_qty": actual_qty,
		"reserved_qty": reserved_qty,
		"available_qty": actual_qty - reserved_qty,
	}


@frappe.whitelist()
def release_spare_reservation(sr_name, spare_row_name) -> dict:
	"""Release a previously reserved spare (e.g. ticket cancelled, spare no longer needed)."""
	_assert_sr_permission(sr_name, "write")
	sr = frappe.get_doc("Service Request", sr_name)
	sr.flags.ignore_validate_update_after_submit = True
	sr.flags.ignore_mandatory = True

	for row in sr.spare_lines:
		if row.name == spare_row_name and row.status in ("Reserved", "Awaiting Procurement", "Pending"):
			sr.remove(row)
			sr.save()
			return {"ok": True}

	frappe.throw(_("Spare line not found or not in a removable state."))


@frappe.whitelist()
def raise_material_request(sr_name) -> dict:
	"""Raise a single Material Request for all spares on this SR that need procurement.

	Collects all spare_lines with status = 'Awaiting Procurement' and creates one MR
	linked to the Service Request.
	"""
	_assert_sr_permission(sr_name, "write")
	sr = frappe.get_doc("Service Request", sr_name)

	pending_lines = [sl for sl in sr.spare_lines if sl.status == "Awaiting Procurement"]
	if not pending_lines:
		frappe.throw(_("No spares awaiting procurement on this ticket."))

	# Procure to wherever the repair is physically happening (hub when the
	# device has been transferred, else the source store).
	warehouse = _effective_repair_warehouse(sr)
	if not warehouse:
		frappe.throw(_("Source warehouse not set on Service Request."))

	mr = frappe.new_doc("Material Request")
	mr.material_request_type = "Purchase"
	mr.company = sr.company or frappe.defaults.get_user_default("Company")
	mr.service_request = sr_name
	mr.transaction_date = nowdate()
	mr.schedule_date = add_days(nowdate(), 3)
	mr.set_warehouse = warehouse
	mr.title = f"Spares for {sr_name} — {sr.customer_name or sr.customer}"

	for sl in pending_lines:
		mr.append("items", {
			"item_code": sl.spare_item,
			"item_name": sl.item_name,
			"qty": sl.qty,
			"uom": sl.uom or "Nos",
			"warehouse": warehouse,
			"schedule_date": add_days(nowdate(), 3),
		})

	mr.insert(ignore_permissions=True)
	mr.submit()

	# Update spare_lines with MR reference
	sr.flags.ignore_validate_update_after_submit = True
	sr.flags.ignore_mandatory = True
	for sl in pending_lines:
		sl.material_request = mr.name
	sr.save()

	frappe.msgprint(
		_("Material Request {0} created for {1} spare(s).").format(
			f'<a href="/app/material-request/{mr.name}">{mr.name}</a>',
			len(pending_lines),
		),
		title=_("Material Request Created"),
		indicator="green",
	)
	return {"ok": True, "material_request": mr.name, "count": len(pending_lines)}


@frappe.whitelist()
def handoff_to_technician(sr_name, new_technician, job_type="Repair", reason="") -> dict:
	"""Create an additional Job Assignment (Technician Changed) for a handoff."""
	frappe.has_permission("Job Assignment", "create", throw=True)

	sr = frappe.get_doc("Service Request", sr_name)
	if not sr.service_order:
		frappe.throw(_("No Service Order linked to {0}.").format(sr_name), title=_("Validation Error"))

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
	return {"ok": True, "job_assignment": ja.name}


# ── Helpers ────────────────────────────────────────────────────────────────────

@frappe.whitelist()
def get_issue_categories() -> list:
	"""Return all active issue categories."""
	return frappe.get_all(
		"Issue Category",
		filters={"is_active": 1} if frappe.db.has_column("Issue Category", "is_active") else {},
		pluck="name",
		order_by="name",
	)


# ── Step 6: Submit for QC ─────────────────────────────────────────────────────

@frappe.whitelist()
def submit_for_qc(sr_name) -> dict:
	"""Mark all solutions as completed and trigger QC on the Service Order.

	This calls the existing workflow: sets qc_status=Awaiting on the SO and
	populates the QC checklist template.
	"""
	frappe.only_for(["Service Manager", "System Manager", "Service Engineer"])

	sr = frappe.get_doc("Service Request", sr_name)
	if not sr.service_order:
		frappe.throw(_("No Service Order linked to {0}. Cannot submit for QC.").format(sr_name), title=_("Validation Error"))

	# Every identified issue needs a solution before QC — submit_for_qc
	# force-completes assigned solutions, but it must never paper over an
	# issue that has no solution at all.
	from gofix.gofix_services.doctype.service_request.service_request import (
		get_unresolved_issue_gaps,
	)

	gaps = get_unresolved_issue_gaps(sr)
	if gaps["uncovered_issues"]:
		frappe.throw(
			_("Cannot submit for QC — these issues have no repair solution yet: {0}. "
			  "Assign a solution (or cancel the issue with a reason) first.").format(
				", ".join(gaps["uncovered_issues"])
			),
			title=_("All Issues Must Be Solved Before QC"),
		)

	_assert_removed_part_details_complete(sr)

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
			"docstatus": ["in", [0, 1]],
			"assignment_status": ["in", ["Open", "In Progress", "Planned"]],
		},
		pluck="name",
	)
	for ja_name in open_jobs:
		ja = frappe.get_doc("Job Assignment", ja_name)
		if ja.docstatus == 0:
			ja.assignment_status = "Completed"
			ja.repair_outcome = "Repaired"
			ja.work_performed = "Completed via Ops Hub QC submission"
			ja.flags.ignore_mandatory = True
			ja.submit()
		else:
			frappe.db.set_value("Job Assignment", ja_name, {
				"assignment_status": "Completed",
				"repair_outcome": "Repaired",
				"work_performed": "Completed via Ops Hub QC submission",
			}, update_modified=True)

	# Trigger QC on the Sales Order using the existing workflow helper
	so = frappe.get_doc("Sales Order", sr.service_order)

	if not getattr(so, "is_service_order", False):
		frappe.throw(_("{0} is not a Service Order.").format(sr.service_order), title=_("Validation Error"))

	from gofix.overrides.sales_order import move_service_order_to_qc_if_ready

	move_service_order_to_qc_if_ready(so)
	frappe.db.commit()

	_log_ops_stage(sr_name, "repair", "qc")
	return {
		"ok": True,
		"qc_status": frappe.db.get_value("Sales Order", sr.service_order, "qc_status") or "Awaiting",
		"stage": "qc",
	}


@frappe.whitelist()
def save_qc_results(sr_name, checklist_json) -> dict:
	"""Save QC checklist results on the linked Sales Order."""
	frappe.only_for(["Service Manager", "System Manager"])

	sr = frappe.get_doc("Service Request", sr_name)
	if not sr.service_order:
		frappe.throw(_("No Service Order linked to {0}.").format(sr_name), title=_("Validation Error"))

	checklist = json.loads(checklist_json) if isinstance(checklist_json, str) else checklist_json

	so = frappe.get_doc("Sales Order", sr.service_order)
	for row in so.get("qc_checklist", []):
		for entry in checklist:
			if row.name == entry.get("name") or row.check_name == entry.get("check_name"):
				row.result = entry.get("result", row.result)
				row.remarks = entry.get("remarks", row.remarks or "")

	so.save()
	return {"ok": True}


@frappe.whitelist()
def complete_qc(sr_name, qc_result) -> dict:
	"""Mark QC as Pass or Fail on the Service Order.

	Pass: triggers SR → Completed, sends to invoice.
	Fail: sets qc_status=Fail, ops stage becomes 'rework'.
	"""
	frappe.only_for(["Service Manager", "System Manager"])

	if qc_result not in ("Pass", "Fail"):
		frappe.throw(_("QC result must be Pass or Fail."), title=_("Validation Error"))

	sr = frappe.get_doc("Service Request", sr_name)
	if not sr.service_order:
		frappe.throw(_("No Service Order linked to {0}.").format(sr_name), title=_("Validation Error"))

	# Defence-in-depth: never pass/fail QC while an identified issue is
	# unsolved (e.g. added by the technician after the ticket reached QC).
	from gofix.gofix_services.doctype.service_request.service_request import (
		get_unresolved_issue_gaps,
	)

	gaps = get_unresolved_issue_gaps(sr)
	if not gaps["ready_for_qc"]:
		frappe.throw(
			_("QC blocked — every identified issue must be solved first. Unresolved: {0}").format(
				", ".join(gaps["uncovered_issues"] + gaps["open_solutions"])
			),
			title=_("All Issues Must Be Solved Before QC"),
		)

	_assert_removed_part_details_complete(sr)

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
		if frappe.db.has_column("Service Request", "workflow_state"):
			frappe.db.set_value("Service Request", sr_name, "workflow_state", "Completed", update_modified=False)
	else:
		so.db_set("workflow_state", "QC Fail", update_modified=False)
		if frappe.db.has_column("Service Request", "workflow_state"):
			frappe.db.set_value("Service Request", sr_name, "workflow_state", "In Service", update_modified=False)

	frappe.db.commit()

	stage = "invoice" if qc_result == "Pass" else "rework"
	_log_ops_stage(sr_name, "qc", stage)
	return {"ok": True, "qc_result": qc_result, "stage": stage}


# ── Step 7: Invoice / Rework ──────────────────────────────────────────────────

@frappe.whitelist()
def get_invoice_summary(sr_name) -> dict:
	"""Return billing summary: service items, spares, total cost for POS.
	Returns two cost views:
	  - customer cost: consumed spares only
	  - company cost: all spares including damaged
	"""
	_assert_sr_permission(sr_name, "read")

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
def create_ops_hub_invoice(sr_name, remote_otp=None) -> dict:
	"""Create a Sales Invoice directly from the Ops Hub invoice stage.

	Falls back to Sales Order items when SR has no service_items / spare_parts.
	"""
	frappe.only_for(["Service Manager", "System Manager"])

	sr = frappe.get_doc("Service Request", sr_name)

	if sr.service_invoice:
		frappe.throw(_("Invoice {0} already exists for {1}.").format(sr.service_invoice, sr_name), title=_("Validation Error"))

	if not sr.is_completed_status():
		frappe.throw(_("Service Request must be in Completed status to create an invoice."), title=_("Validation Error"))

	_assert_removed_part_details_complete(sr)

	# Bill only at the device's home store — off-store billing needs customer OTP.
	from gofix.gofix_services.api import assert_billing_location

	assert_billing_location(sr, remote_otp)

	# ── Gather line items ──────────────────────────────────────────────────
	items = []

	# 1) Service items on SR
	for row in sr.get("service_items", []):
		items.append({
			"item_code": row.service_item,
			"item_name": row.service_item_name or row.item_name or "",
			"qty": 1,
			"rate": flt(row.rate or row.actual_cost or row.estimated_cost or 0),
			"uom": "Nos",
		})

	# 2) Consumed spare lines (exclude damaged – company cost)
	for row in sr.get("spare_lines", []):
		if row.status == "Damaged":
			continue
		rate = flt(row.rate)
		if not rate:
			continue
		items.append({
			"item_code": row.spare_item,
			"item_name": row.item_name or "",
			"qty": flt(row.qty) or 1,
			"rate": rate,
			"uom": row.get("uom") or "Nos",
		})

	# 3) Fallback: pull from linked Sales Order
	if not items and sr.service_order:
		try:
			so = frappe.get_doc("Sales Order", sr.service_order)
			for row in so.items:
				item_row = {
					"item_code": row.item_code,
					"item_name": row.item_name,
					"qty": row.qty or 1,
					"rate": flt(row.rate),
					"uom": row.uom,
				}
				if so.docstatus == 1:
					item_row["sales_order"] = so.name
					item_row["so_detail"] = row.name
				items.append(item_row)
		except Exception:
			pass

	if not items:
		frappe.throw(_("No billable items found on {0} or its Sales Order.").format(sr_name), title=_("Validation Error"))

	# ── Create Sales Invoice ──────────────────────────────────────────────
	posting_date = sr.get("actual_completion_date") or nowdate()

	inv = frappe.get_doc({
		"doctype": "Sales Invoice",
		"customer": sr.customer,
		"company": sr.company,
		"posting_date": posting_date,
		"due_date": posting_date,
		"items": items,
		"remarks": f"Service Invoice for {sr_name} (via Ops Hub)",
		"custom_gofix_service_request": sr_name,
		"custom_gofix_service_order": sr.service_order or "",
	})

	inv.flags.ignore_permissions = True
	inv.insert()
	inv.submit()

	# Link back to SR
	frappe.db.set_value("Service Request", sr_name, {
		"service_invoice": inv.name,
		"status": "Invoiced",
		"decision": "Invoiced",
		"workflow_state": "Invoiced",
	}, update_modified=True)

	from gofix.gofix_services.api import auto_close_service_order_after_billing

	auto_close_service_order_after_billing(service_request=sr_name)

	return {"ok": True, "invoice": inv.name, "grand_total": inv.grand_total}


@frappe.whitelist()
def reassign_after_qc_fail(sr_name, technician, job_type="Repair", manager_notes="") -> dict:
	"""Floor manager assigns ticket back to technician after QC failure.

	Only failed QC items are sent for rework — passed solutions stay intact.
	"""
	frappe.only_for(["Service Manager", "System Manager"])

	sr = frappe.get_doc("Service Request", sr_name)
	if not sr.service_order:
		frappe.throw(_("No Service Order linked to {0}.").format(sr_name), title=_("Validation Error"))

	so = frappe.get_doc("Sales Order", sr.service_order)

	# Identify which solutions are linked to failed QC checks
	failed_solutions = set()
	failed_check_names = set()
	for check in (so.get("qc_checklist") or []):
		if check.result == "Fail":
			failed_check_names.add(check.check_name)
			if check.get("linked_solution"):
				failed_solutions.add(check.linked_solution)

	# Update failed QC checklist rows — increment rework_iteration
	# Use db_set per row to avoid triggering SO workflow validation
	for check in (so.get("qc_checklist") or []):
		if check.result == "Fail":
			frappe.db.set_value("GoFix QC Checklist", check.name, {
				"rework_required": 1,
				"rework_iteration": (check.rework_iteration or 0) + 1,
			}, update_modified=False)

	# Reset QC status and workflow state via db_set (bypasses workflow validation)
	so.db_set("qc_status", "Pending", update_modified=True)
	so.db_set("workflow_state", "Work in Progress", update_modified=False)

	# Clear QC checklist so it is freshly populated on next QC submission (Fix #5)
	for check in list(so.get("qc_checklist") or []):
		frappe.delete_doc("GoFix QC Checklist", check.name, force=True, ignore_permissions=True)

	# Reset ONLY the failed solution lines back to "In Progress" for rework
	# Use db_set per row to avoid triggering validate_issue_solution_cascade
	reworked = []
	for row in sr.get("solution_lines", []):
		updates = {}
		# Reset if: explicitly linked to a failed check, OR no linking and was Completed
		if row.repair_solution in failed_solutions:
			remark = f"\n[Rework] QC fail — reassigned. {manager_notes}".strip()
			updates["status"] = "In Progress"
			updates["technician_remarks"] = (row.technician_remarks or "") + remark
			reworked.append(row.repair_solution)
		# If no solutions were linked to checks (legacy), fall back to failing only Completed
		elif not failed_solutions and row.status == "Completed":
			remark = f"\n[Rework] QC fail — reassigned. {manager_notes}".strip()
			updates["status"] = "In Progress"
			updates["technician_remarks"] = (row.technician_remarks or "") + remark
			reworked.append(row.repair_solution)
		if updates:
			frappe.db.set_value("SR Solution Line", row.name, updates, update_modified=False)

	# Create new Job Assignment for rework only
	ja = frappe.new_doc("Job Assignment")
	ja.service_order = sr.service_order
	ja.service_request = sr_name
	ja.service_engineer = technician
	ja.job_type = job_type
	ja.assignment_type = "Rework"
	ja.assigned_by = frappe.session.user
	ja.priority = sr.priority
	rework_summary = ", ".join(reworked[:5]) if reworked else "all failed items"
	ja.comments = f"QC Fail Rework ({rework_summary}){(' — ' + manager_notes) if manager_notes else ''}"

	ja.insert()
	ja.submit()
	frappe.db.commit()

	_log_ops_stage(sr_name, "rework", "repair")
	_mark_sr_in_service(sr_name)
	return {
		"ok": True,
		"job_assignment": ja.name,
		"stage": "repair",
		"reworked_solutions": reworked,
	}


# ── Navigation: Go back to a previous stage ────────────────────────────────────

@frappe.whitelist()
def go_back_to_stage(sr_name, target_stage) -> dict:
	"""Reset flags so the SR moves back to the target stage.

	Allowed back-navigations:
	  confirm   → analysis   (reset analysis_confirmed)
	  solutions → confirm    (reset customer_confirmed)
	  assign    → solutions  (clear solution_lines)
	  repair    → assign     (cancel open Job Assignments)
	"""
	_assert_sr_permission(sr_name, "write")

	ALLOWED = {"analysis", "confirm", "solutions", "assign"}
	if target_stage not in ALLOWED:
		frappe.throw(_("Cannot navigate back to stage: {0}").format(target_stage), title=_("Validation Error"))

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

	return {"ok": True, "stage": target_stage}


# ── Not Repairable Flow ───────────────────────────────────────────────────────

@frappe.whitelist()
def mark_not_repairable(sr_name, status="Not Repairable", reason="") -> dict:
	"""Mark a Service Request as Not Repairable / BER from the Ops Hub.

	Orchestrates:
	  1) Set repairability status on SR → Rejected
	  2) Update Sales Order repair_outcome + workflow_state
	  3) Cancel open Job Assignments
	  4) Return list of consumed spares needing recovery
	"""
	_assert_sr_permission(sr_name, "write")
	if status not in ("Not Repairable", "BER"):
		frappe.throw(_("Status must be 'Not Repairable' or 'BER'"))

	sr = frappe.get_doc("Service Request", sr_name)
	if sr.decision in ("Delivered", "Withdrawn", "Cancelled"):
		frappe.throw(_("Cannot mark as {0} — SR is already {1}").format(status, sr.decision))

	current_stage = _derive_stage(sr.as_dict())

	# 1) Update SR
	sr.flags.ignore_validate_update_after_submit = True
	sr.flags.ignore_mandatory = True
	sr.set("repairability_status", status)
	sr.set("repairability_reason", reason)
	sr.set("repairability_decided_by", frappe.session.user)
	sr.set("repairability_decided_at", frappe.utils.now_datetime())
	sr.set("decision", "Rejected")
	sr.set("status", "Rejected")
	sr.set("rejection_reason", reason or f"Device is {status}")
	sr.save()

	# 2) Update Sales Order if linked
	if sr.service_order:
		try:
			so = frappe.get_doc("Sales Order", sr.service_order)
			so.flags.ignore_validate_update_after_submit = True
			so.db_set("repair_outcome", status, update_modified=True)
			so.db_set("workflow_state", "Not Repairable", update_modified=False)
		except Exception:
			frappe.log_error(frappe.get_traceback(), f"Not Repairable: SO update failed for {sr.service_order}")

	# 3) Cancel open Job Assignments
	open_jobs = frappe.get_all("Job Assignment",
		filters={"service_request": sr_name, "docstatus": 1,
				 "assignment_status": ["in", ["Open", "In Progress"]]},
		pluck="name")
	# P2-14: do NOT swallow JA cancellation errors silently — a stuck assignment
	# leaves the technician with a ghost job. Surface the failure so the caller
	# can decide (retry / manual intervention / abort the NR transition).
	for ja_name in open_jobs:
		ja = frappe.get_doc("Job Assignment", ja_name)
		ja.flags.ignore_validate_update_after_submit = True
		ja.assignment_status = "Cancelled"
		ja.repair_outcome = status
		ja.save()

	_log_ops_stage(sr_name, current_stage, "closed")

	# 4) Update serial lifecycle
	serial_no = sr.get("serial_no")
	if serial_no:
		try:
			from ch_item_master.ch_item_master.doctype.ch_serial_lifecycle.ch_serial_lifecycle import (
				update_lifecycle_status,
			)
			update_lifecycle_status(
				serial_no=serial_no,
				new_status="Not Repairable",
				company=sr.company,
				remarks=_("{0} — {1}").format(status, reason or ""),
			)
		except (ImportError, Exception):
			frappe.log_error(frappe.get_traceback(), f"Not Repairable: lifecycle update failed for {sr_name}")

	frappe.db.commit()

	# 4) Check for consumed spares that need recovery
	pending_spares = frappe.get_all("Spare Parts Usage",
		filters={"service_request": sr_name, "part_status": "Consumed",
				 "deleted": 0, "status": "Active"},
		fields=["name", "spare_part_item", "item_name", "qty_used", "uom",
				"barcode_value", "purchase_cost", "sales_price"])

	return {
		"message": _("Marked as {0}").format(status),
		"pending_spares": pending_spares,
		"needs_spare_recovery": len(pending_spares) > 0,
	}


@frappe.whitelist()
def recover_spare_from_ops_hub(sr_name, spu_name, disposition, remarks="") -> dict:
	"""Recover a single consumed spare from the Ops Hub spare recovery panel."""
	_assert_sr_permission(sr_name, "write")
	from gofix.gofix_services.doctype.spare_parts_usage.spare_parts_usage import (
		SPARE_DISPOSITION_CHOICES,
	)
	if disposition not in SPARE_DISPOSITION_CHOICES:
		frappe.throw(_("Invalid disposition: {0}").format(disposition))

	doc = frappe.get_doc("Spare Parts Usage", spu_name)
	if doc.service_request != sr_name:
		frappe.throw(_("Spare {0} does not belong to SR {1}").format(spu_name, sr_name))
	doc.recover_spare(disposition, remarks)
	frappe.db.commit()

	# Return updated pending count
	remaining = frappe.db.count("Spare Parts Usage", {
		"service_request": sr_name, "part_status": "Consumed",
		"deleted": 0, "status": "Active"})
	return {"message": _("Recovered: {0}").format(disposition), "remaining": remaining}


@frappe.whitelist()
def return_unrepaired_device(sr_name, remarks="") -> dict:
	"""Mark an NR/BER device as returned to customer (Delivered) after spare recovery.

	Gate: all consumed spares must be recovered first.
	"""
	_assert_sr_permission(sr_name, "write")
	sr = frappe.get_doc("Service Request", sr_name)

	if sr.decision != "Rejected":
		frappe.throw(_("Can only return devices that are Rejected (Not Repairable/BER)"))
	if sr.repairability_status not in ("Not Repairable", "BER"):
		frappe.throw(_("Device must be Not Repairable or BER to use this action"))

	# Gate: spare recovery must be complete
	pending = frappe.db.count("Spare Parts Usage", {
		"service_request": sr_name, "part_status": "Consumed",
		"deleted": 0, "status": "Active"})
	if pending:
		frappe.throw(_("{0} consumed spare(s) still need recovery before return").format(pending))

	sr.flags.ignore_validate_update_after_submit = True
	sr.flags.ignore_mandatory = True
	sr.set("decision", "Delivered")
	sr.set("status", "Delivered")
	sr.set("delivery_remarks", remarks or _("Device returned to customer — {0}").format(
		sr.repairability_status))
	sr.set("actual_completion_date", frappe.utils.today())
	sr.save()

	# Update SO if linked
	if sr.service_order:
		try:
			frappe.db.set_value("Sales Order", sr.service_order, {
				"workflow_state": "Delivered",
				"delivered_datetime": frappe.utils.now(),
				"actual_delivery_date": frappe.utils.today(),
			}, update_modified=True)
		except Exception:
			frappe.log_error(frappe.get_traceback(),
				f"Return device: SO update failed for {sr.service_order}")

	frappe.db.commit()
	return {"message": _("Device returned to customer")}
