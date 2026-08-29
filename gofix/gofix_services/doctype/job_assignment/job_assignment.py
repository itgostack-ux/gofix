# Copyright (c) 2024, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import json

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, get_datetime, now_datetime, time_diff_in_hours

from gofix.config import get_int_setting, has_role_setting, is_privileged_user, require_role_setting
from gofix.security import assert_service_request_access


def _bounded_rows(doctype, *, batch_limit=None, **kwargs):
	batch_limit = min(
		max(int(batch_limit or get_int_setting("scheduler_batch_limit", 500)), 1),
		5000)
	start = 0
	while True:
		rows = frappe.get_all(
			doctype,
			start=start,
			limit_page_length=batch_limit,
			**kwargs)
		if not rows:
			break
		yield from rows
		if len(rows) < batch_limit:
			break
		start += len(rows)


def _job_assignment_status_transitions(job_assignment):
	"""Return assignment-status transitions recorded in Version history."""
	transitions = []
	for row in _bounded_rows(
		"Version",
		filters={
			"ref_doctype": "Job Assignment",
			"docname": job_assignment,
		},
		fields=["creation", "data"],
		order_by="creation asc"):
		try:
			data = json.loads(row.data or "{}")
		except (TypeError, ValueError):
			continue
		for change in data.get("changed") or []:
			if len(change) >= 3 and change[0] == "assignment_status":
				transitions.append(
					(get_datetime(row.creation), change[1] or "", change[2] or "")
				)
	return transitions


def _reconstruct_in_progress_periods(transitions, terminal_end=None):
	"""Rebuild active-work periods and return ``(closed_periods, open_start)``."""
	periods = []
	open_start = None

	for changed_at, old_status, new_status in sorted(transitions, key=lambda row: row[0]):
		changed_at = get_datetime(changed_at)
		if new_status == "In Progress" and old_status != "In Progress":
			if open_start is None:
				open_start = changed_at
			elif changed_at < open_start:
				open_start = changed_at
		elif old_status == "In Progress" and new_status != "In Progress":
			if open_start and changed_at >= open_start:
				periods.append((open_start, changed_at))
			open_start = None

	if open_start and terminal_end:
		terminal_end = get_datetime(terminal_end)
		if terminal_end >= open_start:
			periods.append((open_start, terminal_end))
			open_start = None

	return periods, open_start


def _same_moment(left, right, tolerance_seconds=2):
	if not left or not right:
		return False
	return abs((get_datetime(left) - get_datetime(right)).total_seconds()) <= tolerance_seconds


def _custody_period_exists(existing_rows, started_at, ended_at):
	return any(
		_same_moment(row.taken_at, started_at)
		and _same_moment(row.released_at, ended_at)
		for row in existing_rows
	)


def _insert_custody_period(assignment, started_at, ended_at, note):
	hours = max(flt(time_diff_in_hours(ended_at, started_at)), 0)
	service_request = assignment.service_request or frappe.db.get_value(
		"Sales Order",
		assignment.service_order,
		"service_request")
	frappe.get_doc({
		"doctype": "GoFix Custody Log",
		"service_request": service_request,
		"service_order": assignment.service_order,
		"job_assignment": assignment.name,
		"technician": assignment.service_engineer,
		"technician_name": (
			frappe.db.get_value("Employee", assignment.service_engineer, "employee_name")
			or assignment.service_engineer
		),
		"taken_at": started_at,
		"released_at": ended_at,
		"hours": round(hours, 2),
		"note": note,
	}).insert(ignore_permissions=True)
	return hours


def reconcile_job_assignment_actual_hours(job_assignments=None, commit=False, batch_limit=None):
	"""Backfill missing actual hours from status history.

	Only submitted, completed assignments whose actual hours are zero are
	changed. Re-running is safe: repaired assignments no longer qualify and
	matching custody periods are not inserted twice.
	"""
	if not frappe.db.exists("DocType", "GoFix Custody Log"):
		return {
			"examined": 0,
			"repaired": 0,
			"skipped": 0,
			"custody_logs_created": 0,
		}

	if isinstance(job_assignments, str):
		job_assignments = [job_assignments]
	batch_limit = min(
		max(int(batch_limit or get_int_setting("scheduler_batch_limit", 500)), 1),
		5000)
	if job_assignments:
		job_assignments = list(dict.fromkeys(job_assignments))
		if len(job_assignments) > batch_limit:
			frappe.throw(
				_("A maximum of {0} Job Assignments can be reconciled in one targeted run.").format(batch_limit),
				frappe.ValidationError)

	filters = {
		"docstatus": 1,
		"assignment_status": ("in", ("Completed", "Closed")),
	}
	if job_assignments:
		filters["name"] = ("in", tuple(job_assignments))

	rows = _bounded_rows(
		"Job Assignment",
		filters=filters,
		fields=[
			"name", "service_request", "service_order", "service_engineer",
			"start_datetime", "end_datetime", "actual_hours",
		],
		order_by="creation asc",
		batch_limit=batch_limit)
	summary = {
		"examined": 0,
		"repaired": 0,
		"skipped": 0,
		"custody_logs_created": 0,
		"results": [],
	}

	for row in rows:
		if flt(row.actual_hours) > 0:
			continue
		summary["examined"] += 1
		transitions = _job_assignment_status_transitions(row.name)
		periods, _open_start = _reconstruct_in_progress_periods(
			transitions,
			terminal_end=row.end_datetime)

		# Older manually-maintained assignments may have timestamps but no
		# status Version rows. Keep that established calculation as fallback.
		if not periods and row.start_datetime and row.end_datetime:
			started_at = get_datetime(row.start_datetime)
			ended_at = get_datetime(row.end_datetime)
			if ended_at >= started_at:
				periods = [(started_at, ended_at)]

		total_hours = sum(
			max(flt(time_diff_in_hours(ended_at, started_at)), 0)
			for started_at, ended_at in periods
		)
		if total_hours <= 0:
			summary["skipped"] += 1
			result = {
				"job_assignment": row.name,
				"status": "SKIPPED",
				"reason": "No recoverable In Progress period",
			}
			if len(summary["results"]) < batch_limit:
				summary["results"].append(result)
			continue

		existing_periods = list(_bounded_rows(
			"GoFix Custody Log",
			filters={"job_assignment": row.name},
			fields=["taken_at", "released_at"],
			batch_limit=batch_limit))
		for started_at, ended_at in periods:
			if _custody_period_exists(existing_periods, started_at, ended_at):
				continue
			_insert_custody_period(
				row,
				started_at,
				ended_at,
				"Backfilled from Job Assignment status history.")
			summary["custody_logs_created"] += 1

		updates = {"actual_hours": round(total_hours, 2)}
		if not row.start_datetime:
			updates["start_datetime"] = periods[0][0]
		if not row.end_datetime:
			updates["end_datetime"] = periods[-1][1]
		frappe.db.set_value(
			"Job Assignment",
			row.name,
			updates,
			update_modified=False)
		summary["repaired"] += 1
		result = {
			"job_assignment": row.name,
			"status": "REPAIRED",
			"actual_hours": round(total_hours, 2),
			"periods": len(periods),
		}
		if len(summary["results"]) < batch_limit:
			summary["results"].append(result)

	if commit:
		frappe.db.commit()
	summary["results_truncated"] = max(
		summary["examined"] - len(summary["results"]),
		0)
	return summary


class JobAssignment(Document):
	def before_insert(self):
		"""Set defaults before insert"""
		if not self.assigned_by:
			self.assigned_by = frappe.session.user
	
	def validate(self):
		"""Validate job assignment"""
		self.validate_service_order()
		self.validate_assignment()
		self.set_assignment_datetime()
		self.calculate_hours()
		self.validate_single_active_technician()
		self.detect_custody_transition()

	def before_update_after_submit(self):
		# validate() is skipped on submitted-doc saves — the custody rule
		# must also guard status flips on submitted Job Assignments.
		self.validate_single_active_technician()
		self.detect_custody_transition()

	def detect_custody_transition(self):
		"""Flag In-Progress transitions so the custody log can record who
		physically held the device and for how long."""
		self._custody_event = None
		if self.is_new():
			if self.assignment_status == "In Progress":
				self._custody_event = "take"
			return
		old = frappe.db.get_value("Job Assignment", self.name, "assignment_status")
		if old == self.assignment_status:
			return
		if self.assignment_status == "In Progress":
			self._custody_event = "take"
		elif old == "In Progress":
			self._custody_event = "release"

	def record_custody_event(self):
		"""Open/close a GoFix Custody Log period after the status change is
		saved. Released periods add into actual_hours — 'who spent how much
		time with the device' comes straight from these rows."""
		event = getattr(self, "_custody_event", None)
		if not event or not frappe.db.exists("DocType", "GoFix Custody Log"):
			return
		self._custody_event = None
		sr = self.service_request or frappe.db.get_value(
			"Sales Order", self.service_order, "service_request"
		)
		if not sr:
			return
		now = now_datetime()
		if event == "take":
			if not frappe.db.exists(
				"GoFix Custody Log",
				{"job_assignment": self.name, "released_at": ("is", "not set")}):
				frappe.get_doc({
					"doctype": "GoFix Custody Log",
					"service_request": sr,
					"service_order": self.service_order,
					"job_assignment": self.name,
					"technician": self.service_engineer,
					"technician_name": frappe.db.get_value(
						"Employee", self.service_engineer, "employee_name"
					) or self.service_engineer,
					"taken_at": now,
					"note": getattr(self, "_custody_note", "") or "",
				}).insert(ignore_permissions=True)
			if not self.start_datetime:
				self.db_set("start_datetime", now, update_modified=False)
		else:  # release
			open_row = frappe.db.get_value(
				"GoFix Custody Log",
				{"job_assignment": self.name, "released_at": ("is", "not set")},
				["name", "taken_at"],
				as_dict=True)
			if open_row:
				hours = max(flt(time_diff_in_hours(now, open_row.taken_at)), 0)
				frappe.db.set_value("GoFix Custody Log", open_row.name, {
					"released_at": now,
					"hours": round(hours, 2),
				}, update_modified=False)
				self.db_set(
					"actual_hours",
					round(flt(self.actual_hours) + hours, 2),
					update_modified=False)
			else:
				# The custody DocType was introduced after some assignments
				# were already In Progress. Recover that last active interval
				# from Version history instead of silently recording zero.
				_transitions = _job_assignment_status_transitions(self.name)
				_periods, open_start = _reconstruct_in_progress_periods(_transitions)
				if open_start and not frappe.db.exists(
					"GoFix Custody Log",
					{"job_assignment": self.name, "taken_at": open_start}):
					hours = _insert_custody_period(
						self,
						open_start,
						now,
						"Recovered from Job Assignment status history.")
					if not self.start_datetime:
						self.db_set("start_datetime", open_start, update_modified=False)
					self.db_set(
						"actual_hours",
						round(flt(self.actual_hours) + hours, 2),
						update_modified=False)

	def validate_single_active_technician(self):
		"""Device custody rule: a ticket may be split across technicians
		(L1/L2/L4 each taking their solutions), but the physical device is
		with ONE technician at a time — only one Job Assignment per Service
		Order may be In Progress. Others queue as Open until handoff."""
		if self.assignment_status != "In Progress" or not self.service_order:
			return
		active = frappe.db.get_value(
			"Job Assignment",
			{
				"service_order": self.service_order,
				"assignment_status": "In Progress",
				"name": ("!=", self.name or ""),
				"docstatus": ("<", 2),
			},
			["name", "service_engineer"],
			as_dict=True)
		if active:
			engineer = (
				frappe.db.get_value("Employee", active.service_engineer, "employee_name")
				or active.service_engineer
			)
			frappe.throw(
				_(
					"Device is currently with {0} ({1}). Complete or put that job On Hold "
					"before starting this one — one technician holds the device at a time."
				).format(engineer, active.name),
				title=_("Device With Another Technician"))
	
	def validate_service_order(self):
		"""Validate that service order exists"""
		if self.service_order:
			# Fetch service request from service order
			service_order = frappe.get_doc("Sales Order", self.service_order)
			if hasattr(service_order, 'service_request') and service_order.service_request:
				self.service_request = service_order.service_request
	
	def validate_assignment(self):
		"""Validate assignment type and related fields"""
		if self.assignment_type == "Team Assignment" and not self.team:
			frappe.throw(_("Team is mandatory for Team Assignment"), title=_("Job Assignment Error"))
		
		if self.assignment_type == "User Assignment" and not self.user:
			frappe.throw(_("User is mandatory for User Assignment"), title=_("Job Assignment Error"))
		
		if self.assignment_type in ["Technician Assignment", "Technician Changed"] and not self.service_engineer:
			frappe.throw(_("Service Engineer is mandatory for Technician Assignment"), title=_("Job Assignment Error"))
	
	def set_assignment_datetime(self):
		"""Set assignment datetime if not set"""
		if not self.assignment_datetime:
			self.assignment_datetime = frappe.utils.now_datetime()
	
	def calculate_hours(self):
		"""Calculate actual hours from start and end datetime"""
		if self.start_datetime and self.end_datetime:
			start = get_datetime(self.start_datetime)
			end = get_datetime(self.end_datetime)
			
			if end < start:
				frappe.throw(_("End Date & Time cannot be before Start Date & Time"), title=_("Job Assignment Error"))

			# Custody rows exclude On Hold periods and are the authoritative
			# source once tracking has started. The legacy wall-clock fallback
			# remains for assignments that have no custody history.
			has_custody_history = (
				self.name
				and frappe.db.exists("DocType", "GoFix Custody Log")
				and frappe.db.exists("GoFix Custody Log", {"job_assignment": self.name})
			)
			if not has_custody_history:
				self.actual_hours = time_diff_in_hours(end, start)
	
	def on_update(self):
		"""Check if job sheet is completed and update Service Order"""
		self.record_custody_event()
		if self.assignment_status in ["Completed", "Closed"]:
			self.update_service_order_status()

	def on_update_after_submit(self):
		# Submitted-doc saves fire this instead of on_update — custody status
		# flips (start/hold/handover/complete) all happen post-submit.
		self.record_custody_event()
		if self.assignment_status in ["Completed", "Closed"]:
			self.update_service_order_status()
	
	def on_submit(self):
		"""Update service request with assignment details"""
		self.update_service_request()
		self.create_audit_trail()
	
	def update_service_order_status(self):
		"""Update Service Order status when Job Sheet is completed"""
		if not self.service_order:
			return

		# GF-10 fix: Lock the Service Order row to prevent concurrent job sheets
		# from racing past the all_completed check simultaneously
		frappe.db.sql(
			"SELECT name FROM `tabSales Order` WHERE name=%s FOR UPDATE",
			self.service_order)

		# Read only the locked parent row. Loading the full document here also
		# locks/reads Sales Order Item children after the parent lock, which can
		# deadlock against invoice/QC paths that touch children first.
		so = frappe.db.get_value(
			"Sales Order",
			self.service_order,
			["is_service_order", "service_request"],
			as_dict=True)
		
		# Only update if it's a Service Order
		if not so or not so.is_service_order:
			return
		
		# Check if all job sheets for this SO are completed
		all_job_sheets = frappe.get_all("Job Assignment",
			filters={"service_order": self.service_order},
			fields=["name", "assignment_status"])
		
		all_completed = all(js.assignment_status in ["Completed", "Closed"] for js in all_job_sheets)
		
		if all_completed:
			# Check repair outcome
			# GF-9 fix: Handle "Beyond Repair" alongside other non-repairable outcomes
			non_repairable_outcomes = ("Not Repairable", "Beyond Repair", "Customer Cancelled")
			if hasattr(self, 'repair_outcome') and self.repair_outcome in non_repairable_outcomes:
				# Allow closing without QC
				frappe.db.set_value(
					"Sales Order", self.service_order, "repair_outcome", self.repair_outcome,
					update_modified=False)

				# Set workflow state based on outcome
				if self.repair_outcome in ("Not Repairable", "Beyond Repair"):
					frappe.db.set_value("Sales Order", self.service_order, "workflow_state", "Not Repairable", update_modified=False)
				elif self.repair_outcome == "Customer Cancelled":
					frappe.db.set_value("Sales Order", self.service_order, "workflow_state", "Customer Cancelled", update_modified=False)

				frappe.msgprint(
					_("Service Order {0} marked as {1}. Can be closed without QC.").format(
						self.service_order, self.repair_outcome
					),
					indicator="orange",
					alert=True)

				# Alert about consumed spares that need recovery
				sr_name = so.get("service_request") or frappe.db.get_value(
					"Sales Order", self.service_order, "service_request")
				if sr_name:
					pending = frappe.get_all("Spare Parts Usage", filters={
						"service_request": sr_name,
						"part_status": "Consumed",
						"deleted": 0,
						"status": "Active",
					}, fields=["spare_part_item", "item_name", "qty_used"])
					if pending:
						items_str = ", ".join(
							f"{p.item_name or p.spare_part_item} (x{p.qty_used})" for p in pending
						)
						frappe.msgprint(
							_("<b>⚠ Spare Recovery Required:</b> {0} consumed spare(s) must be "
							  "removed and dispositioned before returning device to customer.<br>"
							  "Pending: {1}").format(len(pending), items_str),
							title=_("Spare Recovery"),
							indicator="red")
			else:
				# QC gate: every identified issue must have a completed
				# solution — including issues added mid-repair.
				gaps = None
				if self.service_request:
					from gofix.gofix_services.doctype.service_request.service_request import (
						get_unresolved_issue_gaps)

					gaps = get_unresolved_issue_gaps(self.service_request)
				if gaps and not gaps["ready_for_qc"]:
					frappe.db.set_value("Sales Order", self.service_order, "workflow_state", "Work in Progress", update_modified=False)
					frappe.msgprint(
						_("Job done, but QC is blocked — unresolved: {0}. Assign and complete "
						  "solutions for every identified issue first.").format(
							", ".join(gaps["uncovered_issues"] + gaps["open_solutions"])
						),
						title=_("All Issues Must Be Solved Before QC"),
						indicator="orange")
				else:
					# Set to QC Awaiting for repairable items
					frappe.db.set_value(
						"Sales Order",
						self.service_order,
						{"qc_status": "Awaiting", "workflow_state": "QC Awaiting"},
						update_modified=False)

					frappe.msgprint(
						_("Service Order {0} is now awaiting QC").format(self.service_order),
						indicator="green",
						alert=True)
	
	def update_service_request(self):
		"""Update service request with latest assignment (uses db_set to avoid re-triggering validate)"""
		if not self.service_request:
			return
		
		updates = {}
		if self.team:
			updates["assigned_to_team"] = self.team
		if self.user:
			updates["assigned_to_user"] = self.user
		if self.service_engineer:
			updates["service_engineer"] = self.service_engineer
		
		if updates:
			frappe.db.set_value("Service Request", self.service_request, updates, update_modified=True)
	
	def create_audit_trail(self):
		"""Create audit trail entry"""
		operation = "ASSIGNED"
		if self.assignment_type == "Technician Changed":
			operation = "CHANGED"
		elif self.received_from_technician:
			operation = "RECEIVED"
		
		audit_entry = {
			"service_engineer": self.service_engineer,
			"assignment_from_time": self.assignment_datetime,
			"operation": operation,
			"is_active_record": "Yes" if not self.received_from_technician else "No"
		}
		
		if self.received_from_technician:
			audit_entry["assignment_to_time"] = self.received_datetime or frappe.utils.now_datetime()
		
		self.flags.ignore_validate_update_after_submit = True
		self.append("technician_audit", audit_entry)
		self.save()
	
	def mark_received_from_technician(self):
		"""Mark job as received from technician"""
		if self.received_from_technician:
			frappe.throw(_("Already marked as received from technician"), title=_("Job Assignment Error"))
		
		if not self.service_engineer:
			frappe.throw(_("No technician assigned to receive from"), title=_("Job Assignment Error"))
		
		self.received_from_technician = 1
		self.received_date = frappe.utils.today()
		self.received_datetime = frappe.utils.now_datetime()
		self.assignment_status = "Received from Technician"
		
		# Update audit trail
		for audit in self.technician_audit:
			if audit.is_active_record == "Yes" and audit.service_engineer == self.service_engineer:
				audit.assignment_to_time = self.received_datetime
				audit.operation = "RECEIVED"
				break
		
		self.save()
		
		frappe.msgprint(_("Marked as received from {0}").format(self.service_engineer))


# API Methods
def get_technician_workload(engineer) -> dict:
	"""Get count of open/in-progress jobs for a technician.
	Returns dict with open_count and list of active job names.
	"""
	frappe.has_permission("Job Assignment", "read", throw=True)
	if not frappe.db.exists("Employee", engineer):
		frappe.throw(_("Technician {0} does not exist.").format(engineer))
	employee = frappe.get_doc("Employee", engineer)
	employee.check_permission("read")
	warehouse_field = (
		"gofix_service_warehouse"
		if frappe.db.has_column("Employee", "gofix_service_warehouse")
		else None
	)
	warehouse = employee.get(warehouse_field) if warehouse_field else None
	if not is_privileged_user() and not warehouse:
		frappe.throw(_("Technician warehouse scope is not configured."), frappe.PermissionError)
	from gofix.scope_guard import assert_warehouse

	assert_warehouse(warehouse=warehouse, company=employee.company)
	row_limit = min(get_int_setting("technician_workload_record_limit", 200), 1000)
	candidates = frappe.get_list(
		"Job Assignment",
		filters={
			"service_engineer": engineer,
			"assignment_status": ["in", ["Open", "In Progress"]],
			"docstatus": ["<", 2],
		},
		fields=["name", "service_request", "service_order", "job_type", "assignment_date"],
		order_by="assignment_date desc",
		limit=row_limit)
	active_jobs = []
	for row in candidates:
		service_request = row.service_request or frappe.db.get_value(
			"Sales Order", row.service_order, "service_request"
		)
		if not service_request:
			continue
		try:
			assert_service_request_access(service_request, permission_type="read")
		except frappe.PermissionError:
			continue
		row.pop("service_request", None)
		active_jobs.append(row)
	return {"open_count": len(active_jobs), "active_jobs": active_jobs}


def authorize_job_assignment_creation(service_request, service_engineer=None):
	"""Authorize a named, scoped service ticket before creating an assignment."""
	frappe.has_permission("Job Assignment", ptype="create", throw=True)
	sr = assert_service_request_access(service_request, permission_type="write")

	if not service_engineer:
		return sr

	if not frappe.db.exists("Employee", service_engineer):
		frappe.throw(_("Engineer {0} not found").format(service_engineer))
	employee = frappe.get_doc("Employee", service_engineer)
	if not frappe.has_permission("Employee", ptype="read", doc=employee):
		frappe.throw(_("You cannot assign this employee."), frappe.PermissionError)
	if employee.status != "Active":
		frappe.throw(_("Engineer {0} is not active.").format(service_engineer))
	if sr.get("company") and employee.company and employee.company != sr.company:
		frappe.throw(_("The engineer belongs to a different company."), frappe.PermissionError)

	can_assign_others = has_role_setting(
		"job_assignment_manager_roles")
	if not can_assign_others and employee.user_id != frappe.session.user:
		frappe.throw(_("You may only create an assignment for yourself."), frappe.PermissionError)
	return sr


@frappe.whitelist(methods=["POST"])
def create_job_sheet_from_service_order(service_order, service_engineer=None, job_type="Repair", estimated_hours=None) -> dict:
	"""Create Job Sheet (Job Assignment) from Service Order
	
	Args:
		service_order (str): Service Order ID (Sales Order)
		service_engineer (str): Employee ID to assign job to
		job_type (str): Type of job (Repair, Diagnosis, etc.)
		estimated_hours (float): Estimated hours for the job
	"""
	if not service_order:
		frappe.throw(_("Service Order is required."))
	so = frappe.get_doc("Sales Order", service_order)
	so.check_permission("read")
	
	# Check if Service Order is marked as service order
	if not hasattr(so, 'is_service_order') or not so.is_service_order:
		frappe.throw(_("This is not a Service Order"), title=_("Job Assignment Error"))
	service_request = getattr(so, "service_request", None)
	if not service_request:
		frappe.throw(_("The Service Order is not linked to a Service Request."))
	authorize_job_assignment_creation(service_request, service_engineer)
	frappe.db.sql(
		"SELECT name FROM `tabSales Order` WHERE name = %s FOR UPDATE",
		(service_order))
	allowed_job_types = {
		value.strip()
		for value in (
			frappe.get_meta("Job Assignment").get_field("job_type").options or ""
		).splitlines()
		if value.strip()
	}
	if job_type not in allowed_job_types:
		frappe.throw(_("Invalid job type {0}.").format(job_type))
	
	# Create Job Sheet
	job_sheet = frappe.new_doc("Job Assignment")
	job_sheet.service_order = service_order
	job_sheet.service_request = service_request
	job_sheet.assignment_date = frappe.utils.today()
	job_sheet.assignment_datetime = frappe.utils.now()
	job_sheet.assigned_by = frappe.session.user
	job_sheet.job_type = job_type
	job_sheet.assignment_status = "Open"
	job_sheet.priority = so.service_priority if hasattr(so, 'service_priority') else "Medium"
	
	# Set estimated hours if provided
	if estimated_hours is not None:
		hours = flt(estimated_hours)
		max_hours = get_int_setting("max_job_estimated_hours", 1000)
		if hours <= 0 or hours > max_hours:
			frappe.throw(_("Estimated hours must be between 0 and {0}.").format(max_hours))
		job_sheet.estimated_hours = hours
	
	# Assign service engineer if provided
	if service_engineer:
		job_sheet.service_engineer = service_engineer
		job_sheet.assignment_type = "Technician Assignment"
		job_sheet.assignment_status = "In Progress"
		# GF-12 fix: Warn if technician has high workload
		workload = get_technician_workload(service_engineer)
		if workload["open_count"] >= get_int_setting("technician_workload_warning_count", 10):
			frappe.msgprint(
				_("Warning: {0} already has {1} open jobs").format(
					service_engineer, workload["open_count"]
				),
				indicator="orange",
				alert=True)
	else:
		job_sheet.assignment_type = "User Assignment"
		job_sheet.user = frappe.session.user
	
	duplicate = frappe.db.exists(
		"Job Assignment",
		{
			"service_order": service_order,
			"service_engineer": service_engineer,
			"assignment_status": ("in", ("Open", "In Progress")),
			"docstatus": ("<", 2),
		}) if service_engineer else None
	if duplicate:
		frappe.throw(_("Active Job Assignment {0} already exists.").format(duplicate))

	job_sheet.insert()
	
	frappe.msgprint(_("Job Sheet {0} created successfully").format(job_sheet.name),
		title=_("Success"),
		indicator="green")
	
	return job_sheet.name
