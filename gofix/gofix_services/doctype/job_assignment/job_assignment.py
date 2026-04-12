# Copyright (c) 2024, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from frappe import _


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
			from frappe.utils import get_datetime, time_diff_in_hours
			start = get_datetime(self.start_datetime)
			end = get_datetime(self.end_datetime)
			
			if end < start:
				frappe.throw(_("End Date & Time cannot be before Start Date & Time"), title=_("Job Assignment Error"))
			
			self.actual_hours = time_diff_in_hours(end, start)
	
	def on_update(self):
		"""Check if job sheet is completed and update Service Order"""
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
			self.service_order,
		)

		# Get Service Order
		so = frappe.get_doc("Sales Order", self.service_order)
		
		# Only update if it's a Service Order
		if not hasattr(so, 'is_service_order') or not so.is_service_order:
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
				so.db_set("repair_outcome", self.repair_outcome, update_modified=False)

				# Set workflow state based on outcome
				if self.repair_outcome in ("Not Repairable", "Beyond Repair"):
					so.db_set("workflow_state", "Not Repairable", update_modified=False)
				elif self.repair_outcome == "Customer Cancelled":
					so.db_set("workflow_state", "Customer Cancelled", update_modified=False)

				frappe.msgprint(
					_("Service Order {0} marked as {1}. Can be closed without QC.").format(
						self.service_order, self.repair_outcome
					),
					indicator="orange",
					alert=True,
				)
			else:
				# Set to QC Awaiting for repairable items
				so.db_set("qc_status", "Awaiting", update_modified=False)
				so.db_set("workflow_state", "QC Awaiting", update_modified=False)

				frappe.msgprint(
					_("Service Order {0} is now awaiting QC").format(self.service_order),
					indicator="green",
					alert=True,
				)
	
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
		self.save(ignore_permissions=True)
	
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
		
		self.save(ignore_permissions=True)
		
		frappe.msgprint(_("Marked as received from {0}").format(self.service_engineer))


# API Methods
@frappe.whitelist()
def get_technician_workload(engineer) -> dict:
	"""Get count of open/in-progress jobs for a technician.
	Returns dict with open_count and list of active job names.
	"""
	active_jobs = frappe.get_all(
		"Job Assignment",
		filters={
			"service_engineer": engineer,
			"assignment_status": ["in", ["Open", "In Progress"]],
			"docstatus": ["<", 2],
		},
		fields=["name", "service_order", "job_type", "assignment_date"],
		order_by="assignment_date desc",
	)
	return {"open_count": len(active_jobs), "active_jobs": active_jobs}


@frappe.whitelist()
def create_job_sheet_from_service_order(service_order, service_engineer=None, job_type="Repair", estimated_hours=None) -> dict:
	"""Create Job Sheet (Job Assignment) from Service Order
	
	Args:
		service_order (str): Service Order ID (Sales Order)
		service_engineer (str): Employee ID to assign job to
		job_type (str): Type of job (Repair, Diagnosis, etc.)
		estimated_hours (float): Estimated hours for the job
	"""
	so = frappe.get_doc("Sales Order", service_order)
	
	# Check if Service Order is marked as service order
	if not hasattr(so, 'is_service_order') or not so.is_service_order:
		frappe.throw(_("This is not a Service Order"), title=_("Job Assignment Error"))
	
	# Create Job Sheet
	job_sheet = frappe.new_doc("Job Assignment")
	job_sheet.service_order = service_order
	job_sheet.service_request = so.service_request if hasattr(so, 'service_request') else None
	job_sheet.assignment_date = frappe.utils.today()
	job_sheet.assignment_datetime = frappe.utils.now()
	job_sheet.assigned_by = frappe.session.user
	job_sheet.job_type = job_type
	job_sheet.assignment_status = "Open"
	job_sheet.priority = so.service_priority if hasattr(so, 'service_priority') else "Medium"
	
	# Set estimated hours if provided
	if estimated_hours:
		job_sheet.estimated_hours = estimated_hours
	
	# Assign service engineer if provided
	if service_engineer:
		job_sheet.service_engineer = service_engineer
		job_sheet.assignment_type = "Technician Assignment"
		job_sheet.assignment_status = "In Progress"
		# GF-12 fix: Warn if technician has high workload
		workload = get_technician_workload(service_engineer)
		if workload["open_count"] >= 10:
			frappe.msgprint(
				_("Warning: {0} already has {1} open jobs").format(
					service_engineer, workload["open_count"]
				),
				indicator="orange",
				alert=True,
			)
	else:
		job_sheet.assignment_type = "User Assignment"
		job_sheet.user = frappe.session.user
	
	job_sheet.insert(ignore_permissions=True)
	
	frappe.msgprint(_("Job Sheet {0} created successfully").format(job_sheet.name),
		title=_("Success"),
		indicator="green")
	
	return job_sheet.name


def _get_least_loaded_technician(company=None):
	"""GF-12 fix: Find the technician with the fewest open jobs for auto-balancing."""
	filters = {"designation": ["like", "%Technician%"], "status": "Active"}
	if company:
		filters["company"] = company
	technicians = frappe.get_all("Employee", filters=filters, pluck="name", limit=50)
	if not technicians:
		return None

	best = None
	for emp in technicians:
		count = frappe.db.count("Job Assignment", {
			"service_engineer": emp,
			"assignment_status": ["in", ["Open", "In Progress"]],
			"docstatus": ["<", 2],
		})
		if best is None or count < best["open_count"]:
			best = {"engineer": emp, "open_count": count}
	return best
