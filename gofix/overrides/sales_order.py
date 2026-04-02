# Copyright (c) 2025, GoFix and contributors
# Sales Order overrides for Service Order functionality

import frappe
from frappe import _
from erpnext.selling.doctype.sales_order.sales_order import SalesOrder


class CustomSalesOrder(SalesOrder):
	"""Extended Sales Order with Service Order sync"""
	
	def validate(self):
		"""Validate Service Order workflow"""
		super().validate()
		if self.is_service_order and self.service_request:
			self.validate_service_order_status()
	
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
		"""Ensure all mandatory QC checklist items have a result before QC Pass."""
		checklist = getattr(self, "qc_checklist", None) or []
		if not checklist:
			return  # No checklist attached — legacy behaviour
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

			from gofix.gofix_services.doctype.service_request.service_request import complete_service_request

			complete_service_request(doc.service_request, completion_date=frappe.utils.today())
			
			frappe.msgprint("QC passed. Service Order can now be billed.", indicator="green")
		elif doc.qc_status == "Fail" and getattr(doc, 'workflow_state', None) != "QC Fail":
			doc.db_set("workflow_state", "QC Fail", update_modified=False)


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
			"check_type": check.get("check_type", "Pass-Fail"),
			"result": "",
		})
	doc.save(ignore_permissions=True)
