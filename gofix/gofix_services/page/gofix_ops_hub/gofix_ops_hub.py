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

from gofix.config import get_int_setting, get_user_roles, has_role_setting, require_role_setting
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
	related_limit = min(get_int_setting("ops_hub_related_row_limit", 500), 5000)
	for child_field in ("issue_lines", "solution_lines", "spare_lines", "status_log"):
		if len(sr.get(child_field) or ()) > related_limit:
			frappe.throw(
				_("Ticket {0} has more than the configured {1} {2} rows.").format(
					sr.name, related_limit, child_field.replace("_", " ")
				),
				frappe.ValidationError,
			)
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
	from gofix.security import assert_service_request_access

	return assert_service_request_access(sr_name, permission_type=ptype)


def _bound_child_row(doctype, row_name, sr_name, parentfield, fields):
	row = frappe.db.get_value(
		doctype,
		{
			"name": row_name,
			"parent": sr_name,
			"parenttype": "Service Request",
			"parentfield": parentfield,
		},
		fields,
		as_dict=True,
	)
	if not row:
		frappe.throw(
			_("{0} is not attached to Service Request {1}.").format(row_name, sr_name),
			frappe.PermissionError,
		)
	return row


# ── Context ───────────────────────────────────────────────────────────────────

@frappe.whitelist()
def get_ops_context(company=None) -> dict:
	"""Return user context for toolbar initialization."""
	user = frappe.session.user
	roles = sorted(get_user_roles(user))
	company = _active_company(company)
	stores = _get_store_options(company)

	is_manager = has_role_setting("service_manager_roles", user=user)

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
	require_role_setting("service_access_roles", action=_("view the Ops Hub ticket queue"))
	frappe.has_permission("Service Request", "read", throw=True)
	company = _active_company(company)
	queue_limit = min(get_int_setting("ops_hub_ticket_queue_limit", 150), 2000)

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
		# Raw SQL bypasses permission_query_conditions — re-apply the same
		# scoping so an engineer's search can't surface other people's tickets.
		from gofix.security import get_service_request_query
		perm_clause = get_service_request_query(frappe.session.user)
		if perm_clause:
			conditions.append(f"({perm_clause})")
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
			LIMIT %(result_limit)s
			""",
			{
				"s": f"%{search}%",
				"company": company or "",
				"warehouse": warehouse or "",
				"result_limit": queue_limit + 1,
			},
			as_dict=True,
		)
	else:
		sr_list = frappe.get_list(
			"Service Request",
			filters=filters,
			fields=all_fields,
			order_by="service_date asc, priority desc",
			limit_page_length=queue_limit + 1,
		)
	if len(sr_list) > queue_limit:
		frappe.throw(
			_("The Ops Hub queue exceeds the configured limit of {0} tickets. Narrow the dates or filters.").format(
				queue_limit
			),
			frappe.ValidationError,
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

@frappe.whitelist(methods=["POST"])
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
		sr.save()

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


@frappe.whitelist(methods=["POST"])
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
		filters={
			"service_request": sr_name,
			"docstatus": 1,
			"assignment_status": ("!=", "Cancelled"),
		},
		fields=[
			"name", "service_engineer", "job_type", "assignment_status",
			"assignment_date", "priority", "estimated_hours", "actual_hours",
			"work_performed", "technician_remarks", "repair_outcome", "assignment_type",
		],
		order_by="assignment_date asc, creation asc",
		limit_page_length=related_limit + 1,
	)
	if len(assignments) > related_limit:
		frappe.throw(
			_("Ticket {0} has more than the configured {1} job assignments.").format(
				sr.name, related_limit
			),
			frappe.ValidationError,
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
			["qc_status", "qc_checked_by", "qc_datetime", "workflow_state", "rework_count"],
			as_dict=True,
		) or {}
		qc_status = so_data.get("qc_status") or ""
		qc_checked_by = so_data.get("qc_checked_by") or ""
		qc_datetime = str(so_data.get("qc_datetime") or "")
		so_workflow_state = so_data.get("workflow_state") or ""
		rework_count = cint(so_data.get("rework_count"))

		# Fetch QC checklist from SO
		qc_rows = frappe.get_all(
			"GoFix QC Checklist",
			filters={"parent": sr.service_order},
			fields=["name", "check_name", "result", "remarks",
				"linked_solution", "fail_reason", "rework_required", "rework_iteration"],
			order_by="idx asc",
			limit_page_length=related_limit + 1,
		)
		if len(qc_rows) > related_limit:
			frappe.throw(
				_("Ticket {0} has more than the configured {1} QC checks.").format(
					sr.name, related_limit
				),
				frappe.ValidationError,
			)
		qc_checklist = qc_rows

	# Fetch status log (timeline)
	status_rows = sr.get("status_log") or []
	changed_users = tuple({row.changed_by for row in status_rows if row.changed_by})
	changed_user_names = {
		row.name: row.full_name or row.name
		for row in frappe.get_all(
			"User",
			filters={"name": ("in", changed_users)},
			fields=["name", "full_name"],
			limit_page_length=len(changed_users),
		)
	} if changed_users else {}
	status_log = [
		{
			"from_status": row.from_status,
			"to_status": row.to_status,
			"changed_by": row.changed_by,
			"changed_by_name": changed_user_names.get(row.changed_by, row.changed_by or ""),
			"changed_at": str(row.changed_at) if row.changed_at else "",
			"hours_in_prev": flt(row.get("time_in_previous_status_hours")),
		}
		for row in status_rows
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
		"device_holder": next(
			(a.service_engineer for a in assignments if a.assignment_status == "In Progress"), ""
		),
		"custody_log": frappe.get_all(
			"GoFix Custody Log",
			filters={"service_request": sr_name},
			fields=["technician", "technician_name", "taken_at", "released_at", "hours", "note"],
			order_by="taken_at desc",
			limit=15,
		) if frappe.db.exists("DocType", "GoFix Custody Log") else [],
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

@frappe.whitelist(methods=["POST"])
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


@frappe.whitelist(methods=["POST"])
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


@frappe.whitelist(methods=["POST"])
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

	if frappe.db.has_column("Service Request", "analysis_confirmed"):
		frappe.db.set_value("Service Request", sr_name, "analysis_confirmed", 1, update_modified=False)

	_log_ops_stage(sr_name, "analysis", "confirm")
	return {"ok": True, "stage": "confirm"}


# ── Step 2: Customer Confirmation ─────────────────────────────────────────────

@frappe.whitelist(methods=["POST"])
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

	return {"ok": True, "whatsapp_sent": sent}


@frappe.whitelist(methods=["POST"])
def mark_customer_confirmed(sr_name) -> dict:
	"""Mark customer as having confirmed the estimate and issues list."""
	_assert_sr_permission(sr_name, "write")

	if frappe.db.has_column("Service Request", "customer_confirmed"):
		frappe.db.set_value("Service Request", sr_name, "customer_confirmed", 1, update_modified=True)

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


@frappe.whitelist(methods=["POST"])
def quick_create_solution(solution_name, issue_category, estimated_minutes=30, requires_spare=0, description="") -> dict:
	"""Quick-create a Repair Solution from the Ops Hub solutions step."""
	require_role_setting(
		"service_manager_roles",
		("Service Manager", "System Manager", "GoFix Floor Manager"),
		action=_("create a repair solution"),
	)

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
	doc.estimated_minutes = cint(estimated_minutes) or get_int_setting("default_solution_minutes", 30)
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


@frappe.whitelist(methods=["POST"])
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

	_log_ops_stage(sr_name, "solutions", "assign")
	return {"ok": True, "solution_count": len(sr.solution_lines), "stage": "assign"}


# ── Step 4: Technician Assignment ─────────────────────────────────────────────

@frappe.whitelist()
def get_technicians_for_grade(minimum_grade=None, issue_category=None) -> dict:
	"""Return active technicians, filtered by minimum grade level, with workload."""
	row_limit = get_int_setting("token_queue_limit", 200)
	employees = frappe.get_all(
		"Employee",
		filters={"status": "Active"},
		fields=["name", "employee_name", "technician_grade", "designation"],
		order_by="employee_name",
		limit_page_length=row_limit,
	)

	grade_names = {emp.technician_grade for emp in employees if emp.technician_grade}
	if minimum_grade:
		grade_names.add(minimum_grade)
	grade_rows = frappe.get_all(
		"Technician Grade",
		filters={"name": ("in", tuple(grade_names))},
		fields=["name", "grade_name", "grade_level"],
		limit_page_length=len(grade_names),
	) if grade_names else []
	grade_by_name = {grade.name: grade for grade in grade_rows}
	req_level = cint((grade_by_name.get(minimum_grade) or {}).get("grade_level", 0))
	employee_names = tuple(emp.name for emp in employees)
	workload_rows = frappe.db.sql(
		"""
		SELECT service_engineer, COUNT(*) AS active_jobs
		FROM `tabJob Assignment`
		WHERE service_engineer IN %(employees)s
			AND docstatus = 1
			AND assignment_status IN ('Open', 'In Progress')
		GROUP BY service_engineer
		""",
		{"employees": employee_names},
		as_dict=True,
	) if employee_names else []
	workload_by_employee = {row.service_engineer: cint(row.active_jobs) for row in workload_rows}

	result = []
	for emp in employees:
		grade = grade_by_name.get(emp.technician_grade)
		emp_level = cint(grade.grade_level if grade else 0)

		if req_level and emp_level < req_level:
			continue

		if emp.technician_grade:
			emp["grade_display"] = f"L{grade.grade_level} — {grade.grade_name}" if grade else emp.technician_grade
		else:
			emp["grade_display"] = "Ungraded"

		emp["active_jobs"] = workload_by_employee.get(emp.name, 0)

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


@frappe.whitelist(methods=["POST"])
def assign_technician(sr_name, technician, job_type="Repair", estimated_hours=None) -> dict:
	"""Create or reuse a submitted Job Assignment for the SR."""
	_assert_sr_permission(sr_name, "write")
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

	ja = _get_or_create_job_assignment(
		sr,
		technician,
		"Technician Assignment",
		estimated_hours=estimated_hours,
		job_type=job_type,
	)

	_log_ops_stage(sr_name, "assign", "repair")
	_mark_sr_in_service(sr_name)
	return {"ok": True, "job_assignment": ja.name, "stage": "repair"}


@frappe.whitelist(methods=["POST"])
def assign_solutions_to_technician(sr_name, solution_rows_json, technician, estimated_hours=None) -> dict:
	"""Assign specific solutions to a technician and create a Job Assignment.

	solution_rows_json: JSON array of SR Solution Line row names.
	"""
	_assert_sr_permission(sr_name, "write")
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

	# One active Job Assignment per technician per service order — assigning
	# more solutions to the same technician reuses it instead of piling up
	# duplicate JAs (and duplicate chips in the hub header).
	ja = _get_or_create_job_assignment(
		sr, technician, "Technician Assignment", estimated_hours=estimated_hours
	)

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


@frappe.whitelist(methods=["POST"])
def unassign_solution(sr_name, solution_row_name) -> dict:
	"""Remove technician assignment from a solution line (reassignment flow —
	e.g. the technician can't solve it and it must go to a higher grade).

	Knocks a started line back to Planned and releases the old technician's
	Job Assignment (Cancelled) when they have nothing else on this ticket, so
	device custody doesn't stay locked to someone no longer working it."""
	_assert_sr_permission(sr_name, "write")

	line = _bound_child_row(
		"SR Solution Line",
		solution_row_name,
		sr_name,
		"solution_lines",
		["technician", "status"],
	)

	updates = {"technician": "", "technician_name": ""}
	if line.status in ("In Progress", "On Hold"):
		updates["status"] = "Planned"
	frappe.db.set_value("SR Solution Line", solution_row_name, updates, update_modified=True)

	if line.technician:
		_release_idle_technician_ja(sr_name, line.technician, solution_row_name)

	return {"ok": True}


def _release_idle_technician_ja(sr_name, technician, exclude_row_name) -> None:
	"""Cancel the technician's active JA when no other solution line on this
	ticket is theirs — frees device custody after unassign/handoff."""
	still_assigned = frappe.db.exists(
		"SR Solution Line",
		{
			"parent": sr_name,
			"parenttype": "Service Request",
			"technician": technician,
			"status": ("not in", ("Cancelled",)),
			"name": ("!=", exclude_row_name),
		},
	)
	so_name = frappe.db.get_value("Service Request", sr_name, "service_order")
	if still_assigned or not so_name:
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
	if ja_name:
		ja = frappe.get_doc("Job Assignment", ja_name)
		ja.check_permission("write")
		ja.assignment_status = "Cancelled"
		ja.flags.ignore_validate_update_after_submit = True
		ja.save()


@frappe.whitelist(methods=["POST"])
def reassign_solution_to_technician(sr_name, solution_row_name, technician, reason="") -> dict:
	"""Hand off ONE solution to a different technician (market-standard ERPs
	reassign at the operation level, not the whole order, once work is split
	across technicians). Grade-gated; the line drops back to Planned so the
	new technician takes the device through the normal Start custody gate."""
	_assert_sr_permission(sr_name, "write")
	frappe.has_permission("Job Assignment", "create", throw=True)

	line = _bound_child_row(
		"SR Solution Line",
		solution_row_name,
		sr_name,
		"solution_lines",
		["technician", "technician_name", "repair_solution", "status", "technician_remarks"],
	)
	if line.status in ("Completed", "Skipped", "Cancelled"):
		frappe.throw(_("{0} is {1} — use Restart for rework instead of a handoff.").format(
			line.repair_solution, _(line.status)), title=_("Validation Error"))
	if technician == line.technician:
		frappe.throw(_("{0} is already assigned to this solution.").format(
			line.technician_name or technician), title=_("Validation Error"))

	_assert_technician_can_take_solutions(technician, [line.repair_solution])

	sr = frappe.get_doc("Service Request", sr_name)
	old_tech, old_name = line.technician, line.technician_name
	new_name = frappe.db.get_value("Employee", technician, "employee_name") or technician

	trail = f"[Handed off {old_name or old_tech or '—'} → {new_name}]" + (f" {reason}" if reason else "")
	updates = {
		"technician": technician,
		"technician_name": new_name,
		"technician_remarks": ((line.technician_remarks or "") + "\n" + trail).strip(),
	}
	if line.status in ("In Progress", "On Hold"):
		updates["status"] = "Planned"
	frappe.db.set_value("SR Solution Line", solution_row_name, updates, update_modified=True)

	if old_tech:
		_release_idle_technician_ja(sr_name, old_tech, solution_row_name)
	ja = _get_or_create_job_assignment(sr, technician, "Technician Changed", comments=reason or trail)

	return {"ok": True, "job_assignment": ja.name}


@frappe.whitelist(methods=["POST"])
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

@frappe.whitelist(methods=["POST"])
def update_solution_status(sr_name, solution_row_name, status, remarks="") -> dict:
	"""Update a solution line status during repair."""
	_assert_sr_permission(sr_name, "write")

	valid = ("Planned", "In Progress", "On Hold", "Completed", "Skipped", "Cancelled")
	if status not in valid:
		frappe.throw(_("Invalid status. Must be one of: {0}").format(", ".join(valid)), title=_("Validation Error"))
	line = _bound_child_row(
		"SR Solution Line",
		solution_row_name,
		sr_name,
		"solution_lines",
		["technician", "repair_solution", "status"],
	)

	if status in ("In Progress", "Completed"):
		line = _assert_can_work_solution(sr_name, solution_row_name)

	# Parts-readiness gate (SAP "waiting for parts" pattern): a solution
	# cannot be marked Done while a spare attributed to it is still on order
	# or fitted without the NEW part's serial recorded.
	if status == "Completed":
		_assert_solution_parts_ready(sr_name, line.repair_solution)

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

	# Terminal statuses may leave a technician with nothing to do — reconcile
	# so finished technicians release the device automatically.
	if status in ("Completed", "Skipped", "Cancelled"):
		_reconcile_device_custody(sr_name)

	return {"ok": True}


@frappe.whitelist(methods=["POST"])
def handover_device(sr_name, to_technician, remarks="") -> dict:
	"""Physically hand the device to another technician on this ticket.

	Distinct from the per-solution ⇄ handoff: solutions keep their assignees;
	only CUSTODY moves. Current holder's JA goes On Hold, the receiver's goes
	In Progress — both transitions land in the GoFix Custody Log."""
	_assert_sr_permission(sr_name, "write")
	sr = frappe.get_doc("Service Request", sr_name)
	if not sr.service_order:
		frappe.throw(_("No Service Order linked to {0}.").format(sr_name), title=_("Validation Error"))

	to_name = frappe.db.get_value("Employee", to_technician, "employee_name") or to_technician
	holder = frappe.db.get_value(
		"Job Assignment",
		{"service_order": sr.service_order, "assignment_status": "In Progress", "docstatus": ("<", 2)},
		["name", "service_engineer"],
		as_dict=True,
	)
	if holder and holder.service_engineer == to_technician:
		frappe.throw(_("{0} already has the device.").format(to_name), title=_("Validation Error"))

	target_ja = frappe.db.get_value(
		"Job Assignment",
		{
			"service_order": sr.service_order,
			"service_engineer": to_technician,
			"docstatus": ("<", 2),
			"assignment_status": ("not in", ("Completed", "Cancelled")),
		},
		"name",
	)
	if not target_ja:
		if frappe.db.exists(
			"SR Solution Line",
			{"parent": sr_name, "parenttype": "Service Request", "technician": to_technician,
			 "status": ("not in", ("Cancelled",))},
		):
			target_ja = _get_or_create_job_assignment(sr, to_technician, "Technician Assignment").name
		else:
			frappe.throw(
				_("{0} has no solution assigned on this ticket — assign one (Assign stage or ⇄) "
				  "before handing over the device.").format(to_name),
				title=_("Not On This Ticket"),
			)

	holder_name = ""
	if holder:
		holder_name = frappe.db.get_value("Employee", holder.service_engineer, "employee_name") or holder.service_engineer
		hdoc = frappe.get_doc("Job Assignment", holder.name)
		hdoc.check_permission("write")
		hdoc.assignment_status = "On Hold"
		hdoc.flags.ignore_validate_update_after_submit = True
		hdoc.save()

	tdoc = frappe.get_doc("Job Assignment", target_ja)
	tdoc.check_permission("write")
	tdoc.assignment_status = "In Progress"
	tdoc._custody_note = remarks or (
		f"Device handover from {holder_name}" if holder_name else "Device taken"
	)
	tdoc.flags.ignore_validate_update_after_submit = True
	tdoc.save()

	return {"ok": True, "holder": to_technician}


def _get_or_create_job_assignment(sr, technician, assignment_type, estimated_hours=None,
		job_type="Repair", comments=None):
	"""Reuse the technician's active JA on this service order, else create one."""
	ja_name = frappe.db.get_value(
		"Job Assignment",
		{
			"service_order": sr.service_order,
			"service_engineer": technician,
			"docstatus": ("<", 2),
			"assignment_status": ("not in", ("Completed", "Cancelled")),
		},
		"name",
	)
	if ja_name:
		ja = frappe.get_doc("Job Assignment", ja_name)
		if estimated_hours:
			ja.check_permission("write")
			ja.flags.ignore_validate_update_after_submit = True
			ja.estimated_hours = flt(ja.estimated_hours or 0) + flt(estimated_hours)
			ja.save()
		return ja

	frappe.has_permission("Job Assignment", "create", throw=True)
	ja = frappe.new_doc("Job Assignment")
	ja.service_order = sr.service_order
	ja.service_request = sr.name
	ja.service_engineer = technician
	ja.job_type = job_type
	ja.assignment_type = assignment_type
	ja.assigned_by = frappe.session.user
	ja.priority = sr.priority
	if estimated_hours:
		ja.estimated_hours = flt(estimated_hours)
	if comments:
		ja.comments = comments
	ja.insert()
	ja.submit()
	return ja


def _reconcile_device_custody(sr_name) -> None:
	"""Self-heal custody drift: a Job Assignment that is still active while
	its technician has NO active solution line on the ticket is a zombie
	holder — it blocks everyone else's Start for no reason. Complete it
	(they did work) or cancel it (they never had any). Safe to call from any
	write path; no-ops when everything is consistent."""
	so_name = frappe.db.get_value("Service Request", sr_name, "service_order")
	if not so_name:
		return
	for ja_row in frappe.get_all(
		"Job Assignment",
		filters={
			"service_order": so_name,
			"docstatus": ("<", 2),
			"assignment_status": ("not in", ("Completed", "Cancelled")),
		},
		fields=["name", "service_engineer"],
	):
		active_lines = frappe.db.exists(
			"SR Solution Line",
			{
				"parent": sr_name,
				"parenttype": "Service Request",
				"technician": ja_row.service_engineer,
				"status": ("in", ("Planned", "In Progress", "On Hold")),
			},
		)
		if active_lines:
			continue
		ever_had = frappe.db.exists(
			"SR Solution Line",
			{"parent": sr_name, "parenttype": "Service Request", "technician": ja_row.service_engineer},
		)
		ja = frappe.get_doc("Job Assignment", ja_row.name)
		ja.check_permission("write")
		ja.assignment_status = "Completed" if ever_had else "Cancelled"
		if ever_had and not ja.end_datetime:
			ja.end_datetime = now_datetime()
		ja.flags.ignore_validate_update_after_submit = True
		ja.save()


def _assert_can_work_solution(sr_name, solution_row_name):
	"""Grade-safety + device-custody gate for EVERY path that puts a solution
	into active work (start, restart, complete): the line must have an
	assigned technician, and no OTHER technician may currently hold the
	device (active In Progress Job Assignment). Returns the line."""
	# Heal zombie holders first so a finished technician's stale JA never
	# blocks the next one from starting.
	_reconcile_device_custody(sr_name)
	line = _bound_child_row(
		"SR Solution Line",
		solution_row_name,
		sr_name,
		"solution_lines",
		["technician", "repair_solution"],
	)
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
	return line


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
	if not ja_name and status == "In Progress":
		# Restarting after their JA auto-completed (e.g. rework of a finished
		# solution) — reopen the latest Completed JA so the single-holder rule
		# keeps applying and custody logging continues.
		ja_name = frappe.db.get_value(
			"Job Assignment",
			{
				"service_order": so_name,
				"service_engineer": technician,
				"docstatus": ("<", 2),
				"assignment_status": "Completed",
			},
			"name",
			order_by="modified desc",
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
	ja.check_permission("write")
	ja.assignment_status = target
	ja.flags.ignore_validate_update_after_submit = True
	ja.save()


@frappe.whitelist(methods=["POST"])
def restart_solution_line(sr_name, solution_row_name, remarks="") -> dict:
	"""Restart a completed/skipped solution back to In Progress (used in rework).

	Allows technicians to re-open a previously completed repair item
	when QC has identified the fix was insufficient.
	"""
	_assert_sr_permission(sr_name, "write")

	current = _bound_child_row(
		"SR Solution Line",
		solution_row_name,
		sr_name,
		"solution_lines",
		["status"],
	).status
	if current not in ("Completed", "Skipped"):
		frappe.throw(_("Only Completed or Skipped solutions can be restarted."), title=_("Validation Error"))

	# Restart is just another way of starting work — same grade/custody gates
	# as Start, else a restarted line lets a second technician work the device.
	line = _assert_can_work_solution(sr_name, solution_row_name)

	remark = f"[Restarted] {remarks}".strip() if remarks else "[Restarted]"
	prev_remarks = frappe.db.get_value("SR Solution Line", solution_row_name, "technician_remarks") or ""
	frappe.db.set_value("SR Solution Line", solution_row_name, {
		"status": "In Progress",
		"technician_remarks": (prev_remarks + "\n" + remark).strip(),
	}, update_modified=True)

	if line.get("technician"):
		_sync_job_assignment_custody(sr_name, solution_row_name, line.technician, "In Progress")

	return {"ok": True}


@frappe.whitelist(methods=["POST"])
def mark_spare_damaged(sr_name, spare_row_name, remarks="") -> dict:
	"""Mark a spare part as damaged/unusable with a mandatory comment."""
	_assert_sr_permission(sr_name, "write")

	if not remarks or not remarks.strip():
		frappe.throw(_("Please provide a reason for marking the spare as damaged."), title=_("Validation Error"))
	_bound_child_row(
		"SR Spare Line",
		spare_row_name,
		sr_name,
		"spare_lines",
		["name", "status"],
	)

	frappe.db.set_value(
		"SR Spare Line",
		spare_row_name,
		{"status": "Damaged", "remarks": remarks.strip()},
		update_modified=True,
	)
	return {"ok": True}


@frappe.whitelist(methods=["POST"])
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
			frappe.has_permission("Stock Entry", "create", throw=True)
			_se.insert()
			_se.submit()
			if _spare_dict.get("name"):
				frappe.db.set_value(_spare_dict.get("doctype") or "SR Spare Line", _spare_dict.get("name"), "custom_stock_entry", _se.name)
		except Exception:
			frappe.log_error(frappe.get_traceback(), f"Spare SE failed for {_spare_dict.get('item_code')} on {so_name}")
			frappe.throw(
				_("The spare could not be issued from stock. No ticket changes were saved."),
				title=_("Stock Issue Failed"),
			)

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
	history_limit = min(get_int_setting("repair_history_record_limit", 500, minimum=1), 2000)
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
		limit_page_length=history_limit,
	)
	manifest_names = tuple(dict.fromkeys(
		se.custom_transfer_manifest for se in transfer_ses if se.custom_transfer_manifest
	))
	manifest_rows = frappe.get_all(
		"CH Transfer Manifest",
		filters={"name": ("in", manifest_names)},
		fields=["name", "status", "driver_name", "driver", "vehicle_number", "trip", "modified"],
		limit_page_length=len(manifest_names),
	) if manifest_names else []
	manifests_by_name = {row.name: row for row in manifest_rows}
	for se in transfer_ses:
		if se.custom_transfer_manifest:
			tm = manifests_by_name.get(se.custom_transfer_manifest)
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
				limit_page_length=history_limit,
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
	mr_names = tuple(sorted(filter(None, mr_names))[:history_limit])
	mr_rows = frappe.get_all(
		"Material Request",
		filters={"name": ("in", mr_names)},
		fields=["name", "transaction_date", "status"],
		limit_page_length=len(mr_names),
	) if mr_names else []
	mr_by_name = {row.name: row for row in mr_rows}
	po_links = frappe.get_all(
		"Purchase Order Item",
		filters={"material_request": ("in", mr_names), "docstatus": 1},
		fields=["material_request", "parent"],
		distinct=True,
		limit_page_length=history_limit,
	) if mr_names else []
	po_names = tuple(dict.fromkeys(link.parent for link in po_links if link.parent))
	po_rows = frappe.get_all(
		"Purchase Order",
		filters={"name": ("in", po_names)},
		fields=["name", "transaction_date", "supplier", "grand_total"],
		limit_page_length=len(po_names),
	) if po_names else []
	po_by_name = {row.name: row for row in po_rows}
	pos_by_mr = {}
	for link in po_links:
		pos_by_mr.setdefault(link.material_request, []).append(link.parent)
	pr_links = frappe.get_all(
		"Purchase Receipt Item",
		filters={"purchase_order": ("in", po_names), "docstatus": 1},
		fields=["purchase_order", "parent", "warehouse"],
		distinct=True,
		limit_page_length=history_limit,
	) if po_names else []
	pr_names = tuple(dict.fromkeys(link.parent for link in pr_links if link.parent))
	pr_rows = frappe.get_all(
		"Purchase Receipt",
		filters={"name": ("in", pr_names)},
		fields=["name", "posting_date"],
		limit_page_length=len(pr_names),
	) if pr_names else []
	pr_by_name = {row.name: row for row in pr_rows}
	prs_by_po = {}
	for link in pr_links:
		prs_by_po.setdefault(link.purchase_order, []).append(link)

	for mr_name in mr_names:
		mr = mr_by_name.get(mr_name)
		if mr:
			add(
				mr.transaction_date,
				f"Material Request {mr_name} ({mr.status})",
				"",
				"Material Request",
				mr_name,
			)
			for po in dict.fromkeys(pos_by_mr.get(mr_name, [])):
				pod = po_by_name.get(po)
				if not pod:
					continue
				add(
					pod.transaction_date,
					f"Purchase Order {po}",
					f"supplier {pod.supplier}, {frappe.utils.fmt_money(pod.grand_total, currency='INR')}",
					"Purchase Order",
					po,
				)
				for pr in prs_by_po.get(po, []):
					prd = pr_by_name.get(pr.parent)
					if not prd:
						continue
					add(
						prd.posting_date,
						f"Spare received: Purchase Receipt {pr.parent}",
						f"at {pr.warehouse}",
						"Purchase Receipt",
						pr.parent,
					)

	# ── Technician work ──────────────────────────────────────────────────
	assignments = frappe.get_all(
		"Job Assignment",
		filters={"service_request": sr.name},
		fields=[
			"name", "assignment_datetime", "service_engineer", "job_type",
			"assignment_status", "start_datetime", "end_datetime",
			"actual_hours", "estimated_hours", "repair_outcome",
		],
		limit_page_length=history_limit,
	)
	employee_names = tuple(dict.fromkeys(
		ja.service_engineer for ja in assignments if ja.service_engineer
	))
	employees = frappe.get_all(
		"Employee",
		filters={"name": ("in", employee_names)},
		fields=["name", "employee_name"],
		limit_page_length=len(employee_names),
	) if employee_names else []
	employee_by_name = {row.name: row.employee_name for row in employees}
	for ja in assignments:
		engineer = employee_by_name.get(ja.service_engineer) or ja.service_engineer or "—"
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
	return events[-history_limit:]


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
	from gofix.scope_guard import assert_warehouse

	assert_warehouse(warehouse=warehouse)
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


@frappe.whitelist(methods=["POST"])
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


@frappe.whitelist(methods=["POST"])
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
	lead_days = get_int_setting("spare_procurement_lead_days", 3)
	mr.schedule_date = add_days(nowdate(), lead_days)
	mr.set_warehouse = warehouse
	mr.title = f"Spares for {sr_name} — {sr.customer_name or sr.customer}"

	for sl in pending_lines:
		mr.append("items", {
			"item_code": sl.spare_item,
			"item_name": sl.item_name,
			"qty": sl.qty,
			"uom": sl.uom or "Nos",
			"warehouse": warehouse,
			"schedule_date": add_days(nowdate(), lead_days),
		})

	frappe.has_permission("Material Request", "create", throw=True)
	mr.insert()
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


@frappe.whitelist(methods=["POST"])
def handoff_to_technician(sr_name, new_technician, job_type="Repair", reason="") -> dict:
	"""Create an additional Job Assignment (Technician Changed) for a handoff."""
	_assert_sr_permission(sr_name, "write")
	frappe.has_permission("Job Assignment", "create", throw=True)

	sr = frappe.get_doc("Service Request", sr_name)
	if not sr.service_order:
		frappe.throw(_("No Service Order linked to {0}.").format(sr_name), title=_("Validation Error"))

	ja = _get_or_create_job_assignment(
		sr, new_technician, "Technician Changed", job_type=job_type, comments=reason
	)
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

@frappe.whitelist(methods=["POST"])
def submit_for_qc(sr_name) -> dict:
	"""Mark all solutions as completed and trigger QC on the Service Order.

	This calls the existing workflow: sets qc_status=Awaiting on the SO and
	populates the QC checklist template.
	"""
	require_role_setting(
		"engineer_operation_roles",
		("Sales Manager", "System Manager", "Service Manager", "Service Engineer"),
		action=_("submit a repair for QC"),
	)
	_assert_sr_permission(sr_name, "write")

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

	# Complete any open Job Assignments so QC guard passes. Go through
	# doc.save (NOT db_set) so custody periods close and actual_hours
	# accumulate; include On Hold — QC submission releases every holder.
	open_jobs = frappe.get_all(
		"Job Assignment",
		filters={
			"service_request": sr_name,
			"docstatus": ["in", [0, 1]],
			"assignment_status": ["in", ["Open", "In Progress", "Planned", "On Hold"]],
		},
		pluck="name",
	)
	for ja_name in open_jobs:
		ja = frappe.get_doc("Job Assignment", ja_name)
		ja.assignment_status = "Completed"
		ja.repair_outcome = "Repaired"
		ja.work_performed = "Completed via Ops Hub QC submission"
		if not ja.end_datetime:
			ja.end_datetime = now_datetime()
		ja.flags.ignore_mandatory = True
		if ja.docstatus == 0:
			ja.submit()
		else:
			ja.check_permission("write")
			ja.flags.ignore_validate_update_after_submit = True
			ja.save()

	# Trigger QC on the Sales Order using the existing workflow helper
	so = frappe.get_doc("Sales Order", sr.service_order)

	if not getattr(so, "is_service_order", False):
		frappe.throw(_("{0} is not a Service Order.").format(sr.service_order), title=_("Validation Error"))

	from gofix.overrides.sales_order import move_service_order_to_qc_if_ready

	move_service_order_to_qc_if_ready(so)

	_log_ops_stage(sr_name, "repair", "qc")
	return {
		"ok": True,
		"qc_status": frappe.db.get_value("Sales Order", sr.service_order, "qc_status") or "Awaiting",
		"stage": "qc",
	}


@frappe.whitelist(methods=["POST"])
def save_qc_results(sr_name, checklist_json) -> dict:
	"""Save QC checklist results on the linked Sales Order."""
	require_role_setting(
		"qc_approval_roles",
		("QC Manager", "Store Manager", "System Manager"),
		action=_("save QC results"),
	)
	_assert_sr_permission(sr_name, "write")

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


@frappe.whitelist(methods=["POST"])
def complete_qc(sr_name, qc_result) -> dict:
	"""Mark QC as Pass or Fail on the Service Order.

	Pass: triggers SR → Completed, sends to invoice.
	Fail: sets qc_status=Fail, ops stage becomes 'rework'.
	"""
	require_role_setting(
		"qc_approval_roles",
		("QC Manager", "Store Manager", "System Manager"),
		action=_("complete QC"),
	)
	_assert_sr_permission(sr_name, "write")

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

	stage = "invoice" if qc_result == "Pass" else "rework"
	_log_ops_stage(sr_name, "qc", stage)
	return {"ok": True, "qc_result": qc_result, "stage": stage}


# ── Step 7: Invoice / Rework ──────────────────────────────────────────────────

BELOW_COST_EXCEPTION_TYPE = "Service Below Cost Billing"

# Exception states considered "open" vs "cleared" for the below-cost gate.
_EXCEPTION_APPROVED_STATES = ("Approved", "Auto-Approved")
_EXCEPTION_OPEN_STATES = ("Pending", "Escalated")


def _spare_cost_rate(item_code, fallback_rate=0.0) -> float:
	"""Buying-side cost of a spare: valuation rate, then last purchase rate,
	then the line's selling rate as a last resort (better than pretending 0)."""
	if not item_code:
		return flt(fallback_rate)
	val = flt(frappe.db.get_value("Item", item_code, "valuation_rate")) or flt(
		frappe.db.get_value("Item", item_code, "last_purchase_rate")
	)
	return val or flt(fallback_rate)


def _get_labour_cost(sr) -> dict:
	"""Labour cost = Σ completed Job Assignment actual_hours × engineer hourly
	cost (Employee.custom_hourly_rate, else CTC ÷ 2080). Same formula as the
	Service Order costing engine (_update_service_costing)."""
	hours_total = 0.0
	cost_total = 0.0
	if not sr.get("service_order"):
		return {"hours": 0.0, "cost": 0.0}

	has_hourly_col = frappe.db.has_column("Employee", "custom_hourly_rate")
	jobs = frappe.get_all(
		"Job Assignment",
		filters={
			"service_order": sr.service_order,
			"assignment_status": ("in", ["Completed", "Closed"]),
		},
		fields=["actual_hours", "service_engineer"],
	)
	employee_names = {job.service_engineer for job in jobs if job.service_engineer}
	employee_fields = ["name", "ctc"]
	if has_hourly_col:
		employee_fields.append("custom_hourly_rate")
	employees = frappe.get_all(
		"Employee",
		filters={"name": ("in", tuple(employee_names))},
		fields=employee_fields,
		limit_page_length=len(employee_names),
	) if employee_names else []
	employee_by_name = {employee.name: employee for employee in employees}
	for job in jobs:
		hours = flt(job.actual_hours)
		if not hours:
			continue
		hourly = 0.0
		employee = employee_by_name.get(job.service_engineer)
		if employee:
			if has_hourly_col:
				hourly = flt(employee.get("custom_hourly_rate"))
			if not hourly:
				ctc = flt(employee.ctc)
				if ctc:
					hourly = ctc / 2080  # Annual CTC ÷ working hours/year
		hours_total += hours
		cost_total += hours * hourly
	return {"hours": hours_total, "cost": cost_total}


def _get_company_cost(sr) -> dict:
	"""True cost the company bears for this repair (SAP RRB / Oracle debrief
	parity): consumed parts at buying cost + damaged parts at buying cost +
	technician labour at cost rate. Selling rates never enter this figure."""
	parts_cost = 0.0
	damaged_cost = 0.0

	lines = sr.get("spare_lines") or []
	for row in lines:
		qty = flt(row.qty) or 1
		cost_rate = _spare_cost_rate(row.spare_item, flt(row.rate))
		if row.status == "Damaged":
			damaged_cost += qty * cost_rate
		elif row.status in ("Consumed", "Issued"):
			parts_cost += qty * cost_rate

	if not lines:
		# Legacy flow records consumption in Spare Parts Usage with an
		# explicit purchase_cost — use it directly.
		try:
			spu = frappe.get_all(
				"Spare Parts Usage",
				filters={
					"service_request": sr.name,
					"status": "Active",
					"part_status": ("in", ["Consumed", "Issued"]),
				},
				fields=["qty_used", "purchase_cost"],
			)
			parts_cost = sum(flt(r.purchase_cost) * flt(r.qty_used) for r in spu)
		except Exception:
			frappe.log_error(
				frappe.get_traceback(),
				f"GoFix company-cost resolution failed for {sr.name}",
			)
			frappe.throw(
				_("Company cost could not be verified. Billing is blocked until the cost ledger is available."),
				frappe.ValidationError,
			)

	labour = _get_labour_cost(sr)
	total = parts_cost + damaged_cost + labour["cost"]
	return {
		"parts_cost": parts_cost,
		"damaged_parts_cost": damaged_cost,
		"labour_cost": labour["cost"],
		"labour_hours": labour["hours"],
		"total": total,
	}


def _ensure_below_cost_exception_type():
	"""Require the configured CH Exception Type used by the below-cost gate."""
	if frappe.db.exists("CH Exception Type", BELOW_COST_EXCEPTION_TYPE):
		return
	frappe.throw(
		_("Exception Type {0} is not configured. Run the GoFix setup patch before billing.").format(
			BELOW_COST_EXCEPTION_TYPE
		),
		title=_("GoFix Configuration Required"),
	)


def _spare_warranty_description(row) -> str | None:
	"""Invoice-line description for an installed spare that carries its own
	part warranty (Item.warranty_period, in days). Includes the installed
	part serial so the claim is traceable to the exact unit."""
	if not row.spare_item:
		return None
	warranty_days = cint(frappe.db.get_value("Item", row.spare_item, "warranty_period"))
	if not warranty_days:
		return None
	from frappe.utils import add_days, formatdate, nowdate

	until = formatdate(add_days(nowdate(), warranty_days))
	parts = [row.item_name or row.spare_item,
		_("Part warranty: {0} days (until {1})").format(warranty_days, until)]
	if row.get("installed_part_serial"):
		parts.append(_("Installed part serial: {0}").format(row.installed_part_serial))
	return " — ".join(parts)


def _below_cost_exception_status(sr) -> str | None:
	if not sr.get("below_cost_exception_request"):
		return None
	return frappe.db.get_value(
		"CH Exception Request", sr.below_cost_exception_request, "status"
	)


@frappe.whitelist()
def get_invoice_summary(sr_name) -> dict:
	"""Return billing summary: service items, spares, total cost for POS.
	Returns two views:
	  - customer (revenue): SR service items + consumed spares at selling
	    rates, falling back to the linked Service Order's items when the SR
	    carries no billing lines (same fallback create_ops_hub_invoice uses);
	    overridden by Final Cost when set.
	  - company (cost): consumed + damaged parts at buying cost plus
	    technician labour at cost rate — what the repair costs the company.
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

	# Fallback: SR has no billing lines at all → show the linked Service
	# Order's items, exactly like create_ops_hub_invoice bills them. Without
	# this the screen shows ₹0 while the Create Invoice button would bill
	# the SO amount.
	items_source = "service_request"
	if not service_items and not spare_items and sr.get("service_order"):
		try:
			so = frappe.get_doc("Sales Order", sr.service_order)
			service_items = [
				{
					"item_code": row.item_code,
					"item_name": row.item_name or "",
					"qty": flt(row.qty) or 1,
					"rate": flt(row.rate),
					"amount": flt(row.amount or (flt(row.qty) * flt(row.rate))),
				}
				for row in so.items
			]
			items_source = "service_order"
		except Exception:
			frappe.log_error(frappe.get_traceback(), f"Invoice summary Sales Order lookup failed for {sr.name}")

	service_total = sum(i["amount"] for i in service_items)
	spare_total = sum(i["amount"] for i in spare_items)
	damaged_spare_total = sum(i["amount"] for i in damaged_spare_items)
	discount = flt(sr.get("service_discount_amount") or 0)

	base_total = service_total + spare_total - discount
	final_cost = flt(sr.get("final_cost") or 0)
	customer_total = final_cost if final_cost else base_total

	company_cost = _get_company_cost(sr)
	exception_status = _below_cost_exception_status(sr)
	below_cost = bool(company_cost["total"] > 0 and customer_total < company_cost["total"])

	return {
		"service_items": service_items,
		"spare_items": spare_items,
		"damaged_spare_items": damaged_spare_items,
		"items_source": items_source,
		"service_total": service_total,
		"spare_total": spare_total,
		"damaged_spare_total": damaged_spare_total,
		"discount": discount,
		"base_total": base_total,
		"final_cost": final_cost,
		"grand_total": customer_total,
		"customer_total": customer_total,
		"company_cost": company_cost,
		"company_total": company_cost["total"],
		"margin": customer_total - company_cost["total"],
		"below_cost": below_cost,
		"below_cost_exception": sr.get("below_cost_exception_request") or "",
		"below_cost_exception_status": exception_status or "",
		"service_invoice": sr.service_invoice or "",
		"warranty_status": sr.warranty_status or "",
	}


@frappe.whitelist()
def get_service_billing_line(sr_name) -> dict:
	"""One consolidated, non-stock POS cart line for billing a completed
	repair alongside retail items in the same invoice.

	POS invoices post with ``update_stock=1`` and consumed spares were
	already issued to the job from store stock — billing them as individual
	stock rows would deduct inventory twice. The whole repair therefore
	bills as one line on the Company's configured repair service item; the
	labour/spare breakdown (including part warranties) travels in the line
	description. The below-cost floor is re-enforced server-side at invoice
	submit — the flags returned here are for early cashier feedback only.
	"""
	_assert_sr_permission(sr_name, "read")
	sr = frappe.get_doc("Service Request", sr_name)

	if sr.service_invoice:
		frappe.throw(_("{0} is already invoiced ({1}).").format(sr_name, sr.service_invoice),
			title=_("Already Invoiced"))
	if not sr.is_completed_status():
		frappe.throw(_("Service Request {0} must be Completed before billing.").format(sr_name),
			title=_("Not Ready to Bill"))

	at_home_store = True
	custody_message = ""
	try:
		from gofix.gofix_services.api import assert_billing_location

		assert_billing_location(sr, None)
	except ImportError:
		pass
	except Exception as e:
		at_home_store = False
		custody_message = str(e)

	s = get_invoice_summary(sr_name)

	desc_lines = [_("Repair charges — {0}").format(sr_name)]
	if s["items_source"] == "service_order":
		desc_lines.append(_("(amounts from Service Order estimate)"))
	for i in s["service_items"]:
		desc_lines.append("{0} ×{1} — {2}".format(
			i["item_name"] or i["item_code"], cint(i["qty"]) or 1,
			frappe.format_value(flt(i["amount"]), {"fieldtype": "Currency"})))
	for row in sr.get("spare_lines", []):
		if row.status == "Damaged":
			continue
		label = _spare_warranty_description(row) or (row.item_name or row.spare_item)
		desc_lines.append("{0} ×{1} — {2}".format(
			label, cint(row.qty) or 1,
			frappe.format_value(flt(row.amount or (flt(row.qty) * flt(row.rate))), {"fieldtype": "Currency"})))
	if s["discount"]:
		desc_lines.append(_("Discount: -{0}").format(
			frappe.format_value(flt(s["discount"]), {"fieldtype": "Currency"})))
	if s["final_cost"]:
		desc_lines.append(_("Final Cost override applied"))

	repair_item = sr._resolve_service_item()

	return {
		"item_code": repair_item,
		"item_name": _("Repair: {0}").format(sr_name),
		"qty": 1,
		"rate": flt(s["customer_total"]),
		"description": "\n".join(desc_lines),
		"service_request": sr_name,
		"customer": sr.customer,
		"customer_name": sr.get("customer_name") or "",
		"below_cost": s["below_cost"],
		"below_cost_exception_status": s["below_cost_exception_status"],
		"company_cost_total": flt(s["company_cost"]["total"]),
		"final_cost": flt(s["final_cost"]),
		"at_home_store": at_home_store,
		"custody_message": custody_message,
	}


@frappe.whitelist(methods=["POST"])
def set_final_cost(sr_name, final_cost, reason=None) -> dict:
	"""Record the final payable amount agreed with the customer.

	Below Cost-to-Company the amount is allowed only through the CH Exception
	framework: a "Service Below Cost Billing" exception is raised (routed via
	CH Approval Authority) and invoice creation stays blocked until it is
	approved. Setting 0 clears the override.

	Counter staff may RAISE (the exception + its SoD-guarded approval is the
	control, same doctrine as POS free-sale) — hence the broad role list.
	"""
	require_role_setting(
		"billing_roles",
		("Sales Manager", "System Manager", "Service Manager", "Store Manager", "Store Executive"),
		action=_("set a final service cost"),
	)
	_assert_sr_permission(sr_name, "write")

	sr = frappe.get_doc("Service Request", sr_name)
	if sr.service_invoice:
		frappe.throw(_("Invoice {0} already exists — final cost is locked.").format(sr.service_invoice),
			title=_("Validation Error"))

	final_cost = flt(final_cost)
	if final_cost < 0:
		frappe.throw(_("Final cost cannot be negative."), title=_("Validation Error"))

	updates = {"final_cost": final_cost}
	company_cost = _get_company_cost(sr)
	exception_name = sr.get("below_cost_exception_request") or ""
	exception_status = _below_cost_exception_status(sr) or ""

	if final_cost and company_cost["total"] > 0 and final_cost < company_cost["total"]:
		# Reuse an open/approved exception; a rejected/expired one is spent.
		if exception_status not in _EXCEPTION_APPROVED_STATES + _EXCEPTION_OPEN_STATES:
			_ensure_below_cost_exception_type()
			try:
				from ch_item_master.ch_item_master.exception_api import raise_exception
			except ImportError:
				frappe.throw(_("ch_item_master app is required for below-cost exceptions."),
					title=_("Missing App Dependency"))
			result = raise_exception(
				exception_type=BELOW_COST_EXCEPTION_TYPE,
				company=sr.company,
				reason=reason or _("Final cost {0} below company cost {1} on {2}").format(
					final_cost, company_cost["total"], sr_name),
				requested_value=final_cost,
				original_value=company_cost["total"],
				reference_doctype="Service Request",
				reference_name=sr.name,
				store_warehouse=sr.get("source_warehouse"),
				customer=sr.customer,
			)
			exception_name = result.get("name") or ""
			exception_status = result.get("status") or "Pending"
			updates["below_cost_exception_request"] = exception_name

	frappe.db.set_value("Service Request", sr_name, updates, update_modified=True)

	return {
		"final_cost": final_cost,
		"company_cost": company_cost,
		"below_cost": bool(final_cost and company_cost["total"] > 0 and final_cost < company_cost["total"]),
		"exception": exception_name,
		"exception_status": exception_status,
	}


@frappe.whitelist(methods=["POST"])
def create_ops_hub_invoice(sr_name, remote_otp=None) -> dict:
	"""Create a Sales Invoice directly from the Ops Hub invoice stage.

	Falls back to Sales Order items when SR has no service_items / spare_parts.
	"""
	require_role_setting(
		"billing_roles",
		("Sales Manager", "System Manager", "Service Manager", "Store Manager", "Store Executive"),
		action=_("create a service invoice"),
	)
	_assert_sr_permission(sr_name, "write")

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
		item_row = {
			"item_code": row.spare_item,
			"item_name": row.item_name or "",
			"qty": flt(row.qty) or 1,
			"rate": rate,
			"uom": row.get("uom") or "Nos",
		}
		# Installed parts carry their own manufacturer warranty — put it on
		# the invoice line so the customer has it in writing.
		warranty_text = _spare_warranty_description(row)
		if warranty_text:
			item_row["description"] = warranty_text
		items.append(item_row)

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
			frappe.log_error(frappe.get_traceback(), f"Ops Hub invoice Sales Order lookup failed for {sr.name}")

	if not items:
		frappe.throw(_("No billable items found on {0} or its Sales Order.").format(sr_name), title=_("Validation Error"))

	# ── Below-cost gate ───────────────────────────────────────────────────
	# The billed amount may not go below Cost-to-Company (parts at buying
	# cost + labour at cost rate) without an APPROVED exception. Same
	# doctrine as the POS min-selling-rate floor.
	items_total = sum(flt(i.get("qty") or 1) * flt(i.get("rate")) for i in items)
	discount = flt(sr.get("service_discount_amount") or 0)
	final_cost = flt(sr.get("final_cost") or 0)
	effective_total = final_cost if final_cost else (items_total - discount)

	company_cost = _get_company_cost(sr)
	if company_cost["total"] > 0 and effective_total < company_cost["total"]:
		status = _below_cost_exception_status(sr)
		if status not in _EXCEPTION_APPROVED_STATES:
			frappe.throw(
				_("Billing total {0} is below Cost to Company {1} "
				  "(parts {2} + damaged {3} + labour {4}). Set a Final Cost and get the "
				  "below-cost exception approved first (current: {5}).").format(
					frappe.bold(frappe.format_value(effective_total, {"fieldtype": "Currency"})),
					frappe.bold(frappe.format_value(company_cost["total"], {"fieldtype": "Currency"})),
					frappe.format_value(company_cost["parts_cost"], {"fieldtype": "Currency"}),
					frappe.format_value(company_cost["damaged_parts_cost"], {"fieldtype": "Currency"}),
					frappe.format_value(company_cost["labour_cost"], {"fieldtype": "Currency"}),
					status or _("no exception raised"),
				),
				title=_("Below-Cost Billing Blocked"),
			)

	# ── Create Sales Invoice ──────────────────────────────────────────────
	posting_date = sr.get("actual_completion_date") or nowdate()

	inv = frappe.get_doc({
		"doctype": "Sales Invoice",
		"customer": sr.customer,
		"company": sr.company,
		"set_posting_time": 1,
		"posting_date": posting_date,
		"due_date": posting_date,
		"items": items,
		"remarks": f"Service Invoice for {sr_name} (via Ops Hub)",
		"custom_gofix_service_request": sr_name,
		"custom_gofix_service_order": sr.service_order or "",
	})

	frappe.has_permission("Sales Invoice", "create", throw=True)
	inv.insert()

	# ── Apply Final Cost / service discount to the payable total ─────────
	# Final Cost is the agreed FINAL payable (incl. tax) — adjust via an
	# invoice-level discount on Grand Total (negative delta raises it).
	# Without a Final Cost, honour the SR's approved service discount,
	# which the previous version silently dropped.
	target_total = final_cost if final_cost else (
		(flt(inv.grand_total) - discount) if discount else 0
	)
	if target_total:
		delta = flt(inv.grand_total) - flt(target_total)
		if abs(delta) >= 0.005:
			inv.apply_discount_on = "Grand Total"
			inv.discount_amount = delta
			inv.save()

	inv.submit()

	# Link back to SR. workflow_state/decision are workflow-provisioned
	# columns — absent on sites without the SR workflow, so write only
	# the columns that exist.
	updates = {"service_invoice": inv.name, "status": "Invoiced"}
	for optional_col in ("decision", "workflow_state"):
		if frappe.db.has_column("Service Request", optional_col):
			updates[optional_col] = "Invoiced"
	frappe.db.set_value("Service Request", sr_name, updates, update_modified=True)

	from gofix.gofix_services.api import auto_close_service_order_after_billing

	auto_close_service_order_after_billing(service_request=sr_name)

	return {"ok": True, "invoice": inv.name, "grand_total": inv.grand_total}


@frappe.whitelist(methods=["POST"])
def reassign_after_qc_fail(sr_name, technician, job_type="Repair", manager_notes="") -> dict:
	"""Floor manager assigns ticket back to technician after QC failure.

	Only failed QC items are sent for rework — passed solutions stay intact.
	"""
	require_role_setting(
		"service_manager_roles",
		("Service Manager", "System Manager", "GoFix Floor Manager"),
		action=_("reassign failed QC work"),
	)
	_assert_sr_permission(sr_name, "write")

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

	so.check_permission("write")
	so.qc_status = "Pending"
	so.workflow_state = "Work in Progress"
	so.set("qc_checklist", [])
	so.flags.ignore_validate_update_after_submit = True
	so.save()

	# Reset ONLY the failed solution lines back to "In Progress" for rework
	# Use db_set per row to avoid triggering validate_issue_solution_cascade
	rework_tech_name = frappe.db.get_value("Employee", technician, "employee_name") or technician
	reworked = []
	for row in sr.get("solution_lines", []):
		updates = {}
		# Reset if: explicitly linked to a failed check, OR no linking and was Completed
		if row.repair_solution in failed_solutions or (not failed_solutions and row.status == "Completed"):
			remark = f"\n[Rework] QC fail — reassigned. {manager_notes}".strip()
			updates["status"] = "In Progress"
			updates["technician_remarks"] = (row.technician_remarks or "") + remark
			# Rework lines belong to the rework technician now — otherwise the
			# old assignee lingers and the custody gate points at the wrong person.
			updates["technician"] = technician
			updates["technician_name"] = rework_tech_name
			reworked.append(row.repair_solution)
		if updates:
			frappe.db.set_value("SR Solution Line", row.name, updates, update_modified=False)

	# Job Assignment for rework (reuses the technician's active JA if any)
	rework_summary = ", ".join(reworked[:5]) if reworked else "all failed items"
	ja = _get_or_create_job_assignment(
		sr, technician, "Rework", job_type=job_type,
		comments=f"QC Fail Rework ({rework_summary}){(' — ' + manager_notes) if manager_notes else ''}",
	)

	_log_ops_stage(sr_name, "rework", "repair")
	_mark_sr_in_service(sr_name)
	return {
		"ok": True,
		"job_assignment": ja.name,
		"stage": "repair",
		"reworked_solutions": reworked,
	}


# ── Navigation: Go back to a previous stage ────────────────────────────────────

@frappe.whitelist(methods=["POST"])
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

@frappe.whitelist(methods=["POST"])
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
				update_lifecycle_status_for_document as update_lifecycle_status,
			)
			update_lifecycle_status(
				serial_no=serial_no,
				new_status="Not Repairable",
				company=sr.company,
				remarks=_("{0} — {1}").format(status, reason or ""),
			)
		except (ImportError, Exception):
			frappe.log_error(frappe.get_traceback(), f"Not Repairable: lifecycle update failed for {sr_name}")

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


@frappe.whitelist(methods=["POST"])
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
	doc.check_permission("write")
	doc.recover_spare(disposition, remarks)

	# Return updated pending count
	remaining = frappe.db.count("Spare Parts Usage", {
		"service_request": sr_name, "part_status": "Consumed",
		"deleted": 0, "status": "Active"})
	return {"message": _("Recovered: {0}").format(disposition), "remaining": remaining}


@frappe.whitelist(methods=["POST"])
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

	return {"message": _("Device returned to customer")}
