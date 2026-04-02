# Copyright (c) 2025, GoFix and contributors
# Sales Order overrides for Service Order functionality

import frappe
from frappe import _
from frappe.utils import flt
from erpnext.selling.doctype.sales_order.sales_order import SalesOrder


class CustomSalesOrder(SalesOrder):
	"""Extended Sales Order with Service Order sync"""

	def validate(self):
		"""Validate Service Order workflow"""
		super().validate()
		if self.is_service_order and self.service_request:
			self.validate_service_order_status()
			self._check_estimate_approval()
			self._check_decision_approval()

	def _check_estimate_approval(self):
		"""Check if estimate requires approval based on GoFix Approval Rules."""
		if not frappe.db.exists("DocType", "GoFix Approval Rule"):
			return
		from gofix.gofix_services.doctype.gofix_approval_rule.gofix_approval_rule import check_approval_required

		total = flt(self.grand_total) or flt(self.total)
		rule = check_approval_required(
			"High Estimate", total, self.company,
			issue_category=getattr(self, "issue_category", None),
			warranty_status=getattr(self, "warranty_status", None),
		)
		if rule and not getattr(self, "estimate_approved", None):
			if not getattr(self, "estimate_approval_status", None) or self.estimate_approval_status == "Pending":
				self.estimate_approval_status = "Pending"
				self.estimate_approval_rule = rule.name

	def _check_decision_approval(self):
		"""Check if repair decisions (free repair, write-off, replacement, beyond repair, discount) need approval."""
		if not frappe.db.exists("DocType", "GoFix Approval Rule"):
			return
		from gofix.gofix_services.doctype.gofix_approval_rule.gofix_approval_rule import check_approval_required

		# Free repair check (total == 0 but parts used)
		total = flt(self.grand_total) or flt(self.total)
		if total == 0 and self._has_spare_parts():
			rule = check_approval_required("Free Repair", 0, self.company)
			if rule and not getattr(self, "decision_approved_by", None):
				self.decision_approval_status = "Pending"
				self.decision_approval_rule = rule.name

		# Beyond repair / write-off check
		repair_outcome = getattr(self, "repair_outcome", None)
		if repair_outcome in ("Not Repairable", "Beyond Repair"):
			rule_type = "Beyond Repair"
			rule = check_approval_required(rule_type, total, self.company)
			if rule and not getattr(self, "decision_approved_by", None):
				self.decision_approval_status = "Pending"
				self.decision_approval_rule = rule.name

	def _has_spare_parts(self):
		if self.service_request:
			return frappe.db.count("Spare Parts Usage", {
				"service_request": self.service_request,
				"status": "Active",
				"part_status": ["in", ["Consumed", "Issued"]],
			}) > 0
		return False
	
	def validate_service_order_status(self):
		"""Validate Service Order workflow based on configured states and transitions"""
		if self.is_new():
			return
		
		# Get old doc
		old_doc = self.get_doc_before_save()
		if not old_doc:
			return
		
		# Get workflow state field (use workflow_state or fall back to status)
		current_state = getattr(self, 'workflow_state', None) or self.status
		old_state = getattr(old_doc, 'workflow_state', None) or old_doc.status
		
		# If state changed, validate the transition
		if old_state != current_state:
			self.validate_state_transition(old_state, current_state)
	
	def validate_state_transition(self, from_state, to_state):
		"""Validate if transition is allowed based on configured workflow"""
		# Get allowed transitions from the database
		transitions = frappe.get_all("Service Order Transition",
			filters={"from_state": from_state, "to_state": to_state},
			fields=["*"])
		
		if not transitions:
			frappe.throw(_(
				"Invalid transition: {0} → {1}. No transition configured for this state change."
			).format(from_state, to_state))
		
		# Check each transition (there may be multiple paths to same state)
		transition_valid = False
		for transition in transitions:
			try:
				# Check role permissions
				if transition.allowed_roles:
					allowed_roles = [r.strip() for r in transition.allowed_roles.split(',')]
					user_roles = frappe.get_roles(frappe.session.user)
					if not any(role in user_roles for role in allowed_roles):
						continue  # Try next transition
				
				# Check condition script — GF-3 fix: use restricted eval context
				if transition.condition_script:
					try:
						doc = self
						# Only allow safe attribute access on doc, no builtins
						condition_met = frappe.safe_eval(
							transition.condition_script,
							eval_globals={"doc": doc, "frappe": frappe._dict({
								"utils": frappe._dict({
									"flt": frappe.utils.flt,
									"cint": frappe.utils.cint,
									"getdate": frappe.utils.getdate,
									"nowdate": frappe.utils.nowdate,
								}),
							})},
							eval_locals={},
						)
						if not condition_met:
							continue  # Try next transition
					except Exception as e:
						frappe.log_error(f"Condition script error for transition {from_state}→{to_state}: {str(e)}")
						continue
				
				# Check Job Sheet completion requirement
				if transition.require_job_sheet_completion:
					job_sheets = frappe.get_all("Job Assignment",
						filters={"service_order": self.name},
						fields=["name", "assignment_status"])
					
					if not job_sheets:
						frappe.throw(_(
						"Cannot change status. Please create and complete Job Sheet first."
					))
					
					incomplete = [js for js in job_sheets if js.assignment_status not in ["Completed", "Closed"]]
					if incomplete:
						frappe.throw(_(
						"Cannot change status. Job Sheet(s) {0} must be completed first."
					).format(', '.join([js.name for js in incomplete])))
				
				# Check QC pass requirement
				if transition.require_qc_pass:
					if not hasattr(self, 'qc_status') or self.qc_status != "Pass":
						frappe.throw(_(
						"QC must be completed and passed before proceeding. Current QC Status: {0}"
					).format(getattr(self, 'qc_status', None) or "Pending"))
					# Enforce QC checklist: all mandatory checks must have a result
					self._validate_qc_checklist()
				
				# Check repair outcome allowance
				if transition.allow_if_repair_outcome:
					allowed_outcomes = [o.strip() for o in transition.allow_if_repair_outcome.split(',')]
					current_outcome = getattr(self, 'repair_outcome', None)
					if current_outcome not in allowed_outcomes:
						continue  # Try next transition
				
				# If we reach here, transition is valid
				transition_valid = True
				break
			
			except frappe.ValidationError:
				raise  # Re-raise validation errors
			except Exception as e:
				frappe.log_error(f"Transition validation error: {str(e)}")
				continue
		
		if not transition_valid:
			frappe.throw(_(
			"Transition {0} → {1} is not allowed. Check your permissions and required conditions."
		).format(from_state, to_state))
	
	def on_update(self):
		"""Sync status to Service Request"""
		super().on_update()
		if self.is_service_order and self.service_request:
			self.sync_to_service_request()
	
	def on_submit(self):
		"""Sync status when SO is submitted"""
		super().on_submit()
		if self.is_service_order and self.service_request:
			move_service_order_to_qc_if_ready(self)
			self.sync_to_service_request()
	
	def on_cancel(self):
		"""Sync cancellation to Service Request"""
		super().on_cancel()
		if self.is_service_order and self.service_request:
			self.update_service_request_status("Cancelled")
	
	def sync_to_service_request(self):
		"""Sync Service Order status back to Service Request"""
		if not self.service_request:
			return
		
		try:
			sr = frappe.get_doc("Service Request", self.service_request)
			
			# Map SO status to SR status
			status_mapping = {
				"Draft": "Accepted",  # SO created but not submitted
				"To Deliver and Bill": "In Service",  # Work in progress
				"To Bill": "Completed",  # Ready for billing
				"To Deliver": "Invoiced",  # Invoice created, pending delivery
				"Completed": "Delivered",  # Fully completed
				"Cancelled": "Cancelled",
				"Closed": "Delivered"
			}
			
			# Check QC status for completion
			if hasattr(self, 'qc_status') and self.qc_status == "Pass":
				new_status = "Completed"
			else:
				new_status = status_mapping.get(self.status, sr.status)
			
			# Only update if status changed
			if sr.status != new_status:
				sr.db_set("status", new_status, update_modified=True)
				sr.db_set("decision", new_status, update_modified=False)
				
				frappe.msgprint(
					_("Service Request {0} updated to {1}").format(self.service_request, new_status),
					indicator="green",
					alert=True
				)
		
		except Exception as e:
			frappe.log_error(f"Failed to sync SO {self.name} to SR: {str(e)}")
	
	def update_service_request_status(self, status):
		"""Update Service Request with specific status"""
		if not self.service_request:
			return
		
		try:
			sr = frappe.get_doc("Service Request", self.service_request)
			sr.db_set("status", status, update_modified=True)
			sr.db_set("decision", status, update_modified=False)
		except Exception as e:
			frappe.log_error(f"Failed to update SR status: {str(e)}")

	def _validate_qc_checklist(self):
		"""Ensure all mandatory QC checklist items have a result before QC Pass.
		Critical checks that fail auto-fail the entire QC."""
		checklist = getattr(self, "qc_checklist", None) or []
		if not checklist:
			return  # No checklist attached — legacy behaviour

		# Check for critical failures first
		critical_failures = [row.check_name for row in checklist
							 if getattr(row, "is_critical", False) and row.result == "Fail"]
		if critical_failures:
			frappe.throw(
				_("QC auto-failed due to critical check failure(s): {0}. "
				  "These must Pass before QC can be approved.").format(
					", ".join(critical_failures)
				),
				title=_("Critical QC Failure"),
			)

		# GF-4 fix: Allow "N/A" as a valid result so technicians can skip irrelevant checks
		incomplete = [row.check_name for row in checklist
					  if not row.result or (hasattr(row, 'is_mandatory') and row.is_mandatory
											and str(row.result).strip().upper() == "N/A")]
		if incomplete:
			frappe.throw(
				_("QC Checklist incomplete. The following checks have no result: {0}").format(
					", ".join(incomplete)
				)
			)


def validate_service_order_before_submit(doc, method=None):
	"""Hook: Validate Service Order on validate — only enforce job sheet check on submit."""
	if not (hasattr(doc, 'is_service_order') and doc.is_service_order):
		return

	# Only enforce job sheet requirement when actually submitting (docstatus transitioning to 1)
	if doc.docstatus != 1:
		return

	job_sheets = frappe.get_all("Job Assignment",
		filters={"service_order": doc.name},
		fields=["name", "assignment_status"])

	if not job_sheets:
		frappe.throw(_("Cannot submit Service Order. Please create and complete Job Sheet first."))

	incomplete = [js for js in job_sheets if js.assignment_status not in ["Completed", "Closed"]]
	if incomplete:
		names = ", ".join([js.name for js in incomplete])
		frappe.throw(_("Cannot submit. Job Sheet(s) {0} must be completed first.").format(names))

def update_service_request_on_qc(doc, method=None):
	"""Hook: Update SR when QC status changes"""
	if hasattr(doc, 'is_service_order') and doc.is_service_order and doc.service_request and hasattr(doc, 'qc_status'):
		if doc.qc_status == "Pass":
			# GF-5 fix: Only users with QC Manager/Store Manager/System Manager role can approve QC Pass
			allowed_roles = {"QC Manager", "Store Manager", "System Manager", "Administrator"}
			user_roles = set(frappe.get_roles())
			if not user_roles.intersection(allowed_roles):
				frappe.throw(
					_("Only QC Managers or Store Managers can approve QC Pass."),
					title=_("Insufficient Permission"),
				)

			if getattr(doc, 'workflow_state', None) != "QC Pass":
				doc.db_set("workflow_state", "QC Pass", update_modified=False)

			# Update QC metadata
			doc.db_set("qc_checked_by", frappe.session.user, update_modified=False)
			doc.db_set("qc_datetime", frappe.utils.now(), update_modified=False)

			# Calculate and store service costing
			_update_service_costing(doc)

			from gofix.gofix_services.doctype.service_request.service_request import complete_service_request

			complete_service_request(doc.service_request, completion_date=frappe.utils.today())

			frappe.msgprint("QC passed. Service Order can now be billed.", indicator="green")
		elif doc.qc_status == "Fail" and getattr(doc, 'workflow_state', None) != "QC Fail":
			doc.db_set("workflow_state", "QC Fail", update_modified=False)

			# Increment rework count
			rework_count = (getattr(doc, "rework_count", 0) or 0) + 1
			doc.db_set("rework_count", rework_count, update_modified=False)

			max_rework = getattr(doc, "max_rework_limit", 3) or 3
			if rework_count >= max_rework:
				# Alert managers that max rework limit reached
				_alert_max_rework(doc, rework_count, max_rework)


def _update_service_costing(doc):
	"""Calculate and store service costing fields on QC Pass."""
	if not doc.service_request:
		return

	# Sum spare parts costs
	parts = frappe.get_all("Spare Parts Usage",
		filters={
			"service_request": doc.service_request,
			"status": "Active",
			"part_status": ["in", ["Consumed", "Issued"]],
		},
		fields=["sum(purchase_cost * qty_used) as total_cost",
				"sum(sales_price * qty_used) as total_revenue"])

	parts_cost = flt(parts[0].total_cost) if parts else 0
	parts_revenue = flt(parts[0].total_revenue) if parts else 0
	labor_cost = flt(getattr(doc, "labor_cost", 0))
	total_cost = parts_cost + labor_cost
	total_revenue = flt(doc.grand_total) or flt(doc.total)
	margin = total_revenue - total_cost
	margin_pct = (margin / total_revenue * 100) if total_revenue else 0

	doc.db_set("spare_parts_cost", parts_cost, update_modified=False)
	doc.db_set("spare_parts_revenue", parts_revenue, update_modified=False)
	doc.db_set("total_repair_cost", total_cost, update_modified=False)
	doc.db_set("repair_margin", margin, update_modified=False)
	doc.db_set("repair_margin_pct", margin_pct, update_modified=False)


def _alert_max_rework(doc, rework_count, max_rework):
	"""Alert managers when max rework limit is reached."""
	message = _(
		"⚠️ Service Order {0} has reached {1} rework attempts (limit: {2}). "
		"Immediate attention required — consider reassigning technician or escalating."
	).format(doc.name, rework_count, max_rework)

	# Send real-time alert to Service Managers
	manager_users = frappe.get_all("Has Role",
		filters={"role": "Service Manager", "parenttype": "User"},
		pluck="parent")

	for user in manager_users:
		frappe.publish_realtime("msgprint",
			{"message": message, "alert": True},
			user=user)


def move_service_order_to_qc_if_ready(doc):
	"""Align workflow fields after submit when repair work is already complete."""
	job_sheets = frappe.get_all(
		"Job Assignment",
		filters={"service_order": doc.name},
		fields=["assignment_status"],
	)
	if not job_sheets:
		return

	if any(js.assignment_status not in ["Completed", "Closed"] for js in job_sheets):
		return

	repair_outcome = getattr(doc, 'repair_outcome', None)
	if repair_outcome in ["Not Repairable", "Customer Cancelled"]:
		if getattr(doc, 'workflow_state', None) != repair_outcome:
			doc.db_set("workflow_state", repair_outcome, update_modified=False)
		return

	if getattr(doc, 'qc_status', None) not in ["Pass", "Fail"]:
		doc.db_set("qc_status", "Awaiting", update_modified=False)

	if getattr(doc, 'workflow_state', None) != "QC Awaiting":
		doc.db_set("workflow_state", "QC Awaiting", update_modified=False)

	# Auto-populate QC checklist from matching template
	_populate_qc_checklist(doc)


def _populate_qc_checklist(doc):
	"""Auto-populate QC checklist from GoFix QC Template matching the issue category."""
	if not hasattr(doc, "qc_checklist"):
		return
	# Skip if already populated
	if doc.qc_checklist:
		return

	issue_category = None
	if doc.service_request:
		issue_category = frappe.db.get_value("Service Request", doc.service_request, "issue_category")

	# Find best-match template: exact category > catch-all (no category)
	filters = {"is_active": 1}
	if doc.company:
		filters["company"] = ["in", [doc.company, "", None]]

	templates = frappe.get_all(
		"GoFix QC Template",
		filters=filters,
		fields=["name", "issue_category"],
		order_by="issue_category desc",
	)

	template = None
	for t in templates:
		if t.issue_category == issue_category:
			template = t
			break
	if not template and templates:
		# Fallback: template without issue_category (generic)
		for t in templates:
			if not t.issue_category:
				template = t
				break

	if not template:
		return

	tmpl_doc = frappe.get_doc("GoFix QC Template", template.name)
	for check in tmpl_doc.checks:
		doc.append("qc_checklist", {
			"check_name": check.check_name,
			"is_mandatory": check.is_mandatory,
			"is_critical": getattr(check, "is_critical", 0),
			"check_type": check.get("check_type", "Pass-Fail"),
			"result": "",
		})
	doc.save(ignore_permissions=True)
