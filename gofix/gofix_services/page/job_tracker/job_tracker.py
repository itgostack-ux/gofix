import frappe
from frappe import _
from frappe.utils import nowdate, add_days

from gofix.gofix_services.store_context import active_company

no_cache = 1


def get_context(context):
	context.no_cache = 1
	context.show_sidebar = False


@frappe.whitelist()
def get_board_data(warehouse=None, date_from=None, date_to=None, search=None, company=None) -> dict:
	"""Return all active Service Requests grouped by decision for the Kanban board."""
	frappe.has_permission("Service Request", "read", throw=True)
	company = active_company(company)

	if not date_from:
		date_from = add_days(nowdate(), -30)
	if not date_to:
		date_to = nowdate()

	filters = [
		["service_date", ">=", date_from],
		["service_date", "<=", date_to],
		["decision", "not in", ["Delivered", "Cancelled", "Rejected"]],
	]
	if company:
		filters.append(["company", "=", company])
	if warehouse:
		filters.append(["source_warehouse", "=", warehouse])
	if search:
		filters.append(["name", "like", f"%{search}%"])

	sr_list = frappe.get_list(
		"Service Request",
		filters=filters,
		fields=[
			"name", "customer", "customer_name", "contact_number",
			"device_item", "device_item_name", "serial_no", "brand",
			"issue_category", "issue_description",
			"decision", "priority", "service_date", "expected_completion_date",
			"warranty_status", "device_condition",
			"source_warehouse",
		],
		order_by="service_date asc, priority desc",
		limit=200,
	)

	if not sr_list:
		return {"columns": _default_columns(), "cards_by_status": {}, "summary": {}}

	sr_names = [r["name"] for r in sr_list]

	# Fetch latest Job Assignment per SR (one query)
	ja_rows = frappe.db.sql("""
		SELECT
			ja.service_request,
			ja.name as ja_name,
			ja.service_engineer,
			emp.employee_name,
			ja.assignment_status,
			ja.job_type
		FROM `tabJob Assignment` ja
		LEFT JOIN `tabEmployee` emp ON emp.name = ja.service_engineer
		WHERE ja.service_request IN %(names)s
		  AND ja.docstatus = 1
		ORDER BY ja.assignment_date DESC, ja.creation DESC
	""", {"names": sr_names}, as_dict=True)

	ja_by_sr = {}
	for row in ja_rows:
		if row["service_request"] not in ja_by_sr:
			ja_by_sr[row["service_request"]] = row

	# Attach assignment info to each card
	for sr in sr_list:
		ja = ja_by_sr.get(sr["name"]) or {}
		sr["engineer_name"] = ja.get("employee_name") or ja.get("service_engineer") or ""
		sr["assignment_status"] = ja.get("assignment_status") or ""
		sr["job_type"] = ja.get("job_type") or ""
		sr["ja_name"] = ja.get("ja_name") or ""

	# Group by decision
	cards_by_status = {}
	for col in _default_columns():
		cards_by_status[col["status"]] = []

	for sr in sr_list:
		status = sr["decision"]
		if status not in cards_by_status:
			cards_by_status[status] = []
		cards_by_status[status].append(sr)

	# Summary stats
	summary = {col["status"]: len(cards_by_status.get(col["status"], [])) for col in _default_columns()}

	return {
		"columns": _default_columns(),
		"cards_by_status": cards_by_status,
		"summary": summary,
	}


@frappe.whitelist()
def get_sr_detail(sr_name) -> dict:
	"""Return full details of a single Service Request for the side panel."""
	frappe.has_permission("Service Request", "read", throw=True)

	sr = frappe.get_doc("Service Request", sr_name)

	# Customer info
	customer_info = {}
	if sr.customer:
		customer_info = frappe.db.get_value(
			"Customer", sr.customer,
			["customer_name", "mobile_no", "email_id"],
			as_dict=True,
		) or {}

	# All Job Assignments
	assignments = frappe.get_list(
		"Job Assignment",
		filters={"service_request": sr_name, "docstatus": 1},
		fields=[
			"name", "assignment_date", "service_engineer", "team",
			"assignment_type", "assignment_status", "job_type",
			"estimated_hours", "actual_hours", "work_performed",
		],
		order_by="assignment_date desc",
	)
	for a in assignments:
		emp_name = frappe.db.get_value("Employee", a["service_engineer"], "employee_name") if a.get("service_engineer") else None
		a["engineer_display"] = emp_name or a.get("service_engineer") or a.get("team") or "—"

	# Spare Parts
	spare_parts = frappe.get_list(
		"Spare Parts Usage",
		filters={"service_request": sr_name},
		fields=["name", "spare_part_item", "item_name", "qty_used", "sales_price", "status"],
		order_by="line_seq_no",
	)

	return {
		"sr": sr.as_dict(),
		"customer_info": customer_info,
		"assignments": assignments,
		"spare_parts": spare_parts,
	}


# @frappe.whitelist()
# def create_assignment(service_request, engineer, job_type="Repair", estimated_hours=None) -> dict:
# 	"""Quick-assign a service engineer to a Service Request."""
# 	frappe.has_permission("Job Assignment", "create", throw=True)

# 	# GF-12 fix: Check technician workload before assigning
# 	from gofix.gofix_services.doctype.job_assignment.job_assignment import get_technician_workload
# 	workload = get_technician_workload(engineer)
# 	if workload["open_count"] >= 10:
# 		frappe.msgprint(
# 			_("Warning: {0} already has {1} open jobs").format(engineer, workload["open_count"]),
# 			indicator="orange",
# 			alert=True,
# 		)

# 	from frappe.utils import nowdate
# 	ja = frappe.new_doc("Job Assignment")
# 	ja.service_request = service_request
# 	ja.assignment_date = nowdate()
# 	ja.service_engineer = engineer
# 	ja.assignment_type = "Technician Assignment"
# 	ja.job_type = job_type
# 	ja.assignment_status = "Open"
# 	if estimated_hours:
# 		ja.estimated_hours = float(estimated_hours)
# 	ja.flags.ignore_permissions = True
# 	ja.insert()
# 	ja.submit()
# 	return {"name": ja.name, "status": "created"}








@frappe.whitelist()
def create_assignment(service_request, engineer, job_type="Repair", estimated_hours=None) -> dict:
	"""Quick-assign a service engineer to a Service Request."""

	frappe.has_permission("Job Assignment", "create", throw=True)

	if not frappe.db.exists("Service Request", service_request):
		frappe.throw(_("Service Request {0} not found").format(service_request))

	if not frappe.db.exists("Employee", engineer) and not frappe.db.exists("User", engineer):
		frappe.throw(_("Engineer {0} not found").format(engineer))

	from gofix.gofix_services.doctype.job_assignment.job_assignment import (
		get_technician_workload,
	)

	workload = get_technician_workload(engineer)

	if workload.get("open_count", 0) >= 10:
		frappe.msgprint(
			_("Warning: {0} already has {1} open jobs").format(
				engineer, workload.get("open_count", 0)
			),
			indicator="orange",
			alert=True,
		)

	current_user = frappe.session.user

	if not current_user or current_user == "Guest":
		current_user = "Administrator"

	if not frappe.db.exists("User", current_user):
		current_user = "Administrator"

	ja = frappe.new_doc("Job Assignment")
	ja.service_request = service_request
	ja.assignment_date = nowdate()
	ja.service_engineer = engineer
	ja.assignment_type = "Technician Assignment"
	ja.job_type = job_type
	ja.assignment_status = "Open"

	
	ja.assigned_by = current_user

	if estimated_hours:
		try:
			ja.estimated_hours = float(estimated_hours)
		except Exception:
			pass

	ja.insert(ignore_permissions=True)
	ja.submit()

	frappe.db.commit()

	return {
		"name": ja.name,
		"status": "created",
		"assigned_by": current_user,
	}



def _default_columns():
	return [
		{"status": "Draft",      "label": "Draft",      "color": "#6b7280", "icon": "fa-pencil"},
		{"status": "Accepted",   "label": "Accepted",   "color": "#3b82f6", "icon": "fa-check"},
		{"status": "In Service", "label": "In Service", "color": "#f59e0b", "icon": "fa-wrench"},
		{"status": "Completed",  "label": "Completed",  "color": "#10b981", "icon": "fa-check-circle"},
		{"status": "Invoiced",   "label": "Invoiced",   "color": "#8b5cf6", "icon": "fa-file-text"},
		{"status": "Withdrawn",  "label": "Withdrawn",  "color": "#ef4444", "icon": "fa-times"},
	]
