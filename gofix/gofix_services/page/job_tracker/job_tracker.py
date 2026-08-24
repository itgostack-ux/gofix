import frappe
from frappe import _
from frappe.utils import nowdate, add_days

from gofix.config import get_int_setting, require_role_setting
from gofix.gofix_services.store_context import active_company
from gofix.security import assert_service_request_access
from gofix.scope_guard import assert_warehouse

no_cache = 1


def get_context(context):
	context.no_cache = 1
	context.show_sidebar = False


@frappe.whitelist()
def get_board_data(warehouse=None, date_from=None, date_to=None, search=None, company=None) -> dict:
	"""Return all active Service Requests grouped by decision for the Kanban board."""
	frappe.has_permission("Service Request", "read", throw=True)
	company = active_company(company)
	assert_warehouse(warehouse=warehouse, company=company)

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
		limit_page_length=min(get_int_setting("token_queue_limit", 200), 500),
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
	sr = assert_service_request_access(sr_name, permission_type="read")

	# Customer info
	customer_info = {}
	if sr.customer:
		customer = frappe.get_doc("Customer", sr.customer)
		customer.check_permission("read")
		customer_info = {
			"customer_name": customer.customer_name,
			"mobile_no": customer.mobile_no,
			"email_id": customer.email_id,
		}

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
		limit_page_length=get_int_setting("token_queue_limit", 200),
	)
	engineer_ids = {assignment["service_engineer"] for assignment in assignments if assignment.get("service_engineer")}
	if engineer_ids:
		frappe.has_permission("Employee", "read", throw=True)
	engineer_rows = frappe.get_all(
		"Employee",
		filters={"name": ("in", tuple(engineer_ids))},
		fields=["name", "employee_name"],
		limit_page_length=len(engineer_ids),
	) if engineer_ids else []
	engineer_names = {row.name: row.employee_name for row in engineer_rows}
	for a in assignments:
		emp_name = engineer_names.get(a.get("service_engineer"))
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


@frappe.whitelist(methods=["POST"])
def create_assignment(service_request, engineer, job_type="Repair", estimated_hours=None) -> dict:
	"""Quick-assign a service engineer to a Service Request."""

	from gofix.gofix_services.doctype.job_assignment.job_assignment import (
		authorize_job_assignment_creation,
		get_technician_workload,
	)
	authorize_job_assignment_creation(service_request, engineer)

	workload = get_technician_workload(engineer)

	warning_count = get_int_setting("technician_workload_warning_count", 10)
	if workload.get("open_count", 0) >= warning_count:
		frappe.msgprint(
			_("Warning: {0} already has {1} open jobs").format(
				engineer, workload.get("open_count", 0)
			),
			indicator="orange",
			alert=True,
		)

	current_user = frappe.session.user

	if not current_user or current_user == "Guest":
		frappe.throw(_("Sign in before assigning a technician."), frappe.AuthenticationError)

	if not frappe.db.exists("User", current_user):
		frappe.throw(_("The signed-in user account no longer exists."), frappe.AuthenticationError)

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
		except (TypeError, ValueError):
			frappe.throw(_("Estimated hours must be a number."))

	ja.insert()
	ja.submit()

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
