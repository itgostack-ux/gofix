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
				
				# Check condition script — declarative matcher only (no safe_eval)
				if transition.condition_script:
					try:
						condition_met = self._evaluate_transition_condition(
							transition.condition_script
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

	def _evaluate_transition_condition(self, condition_script):
		"""Evaluate a transition condition using declarative field matching only.

		Supports simple expressions like:
		  doc.status == "Completed"
		  doc.grand_total > 5000
		  doc.qc_status == "Passed" and doc.repair_outcome != "Not Repairable"

		Complex Python expressions are rejected — configure simpler conditions.
		"""
		import re
		import operator

		# Declarative-only parsing — simple field comparisons joined by and/or
		simple_pattern = re.compile(
			r'doc\.(\w+)\s*(==|!=|>=|<=|>|<)\s*(?:"([^"]*)"|\'([^\']*)\'|(\d+(?:\.\d+)?))'
		)
		ops = {
			"==": operator.eq, "!=": operator.ne,
			">": operator.gt, "<": operator.lt,
			">=": operator.ge, "<=": operator.le,
		}

		parts = re.split(r'\b(and|or)\b', condition_script.strip())
		if all(
			p.strip() in ("and", "or", "") or simple_pattern.fullmatch(p.strip())
			for p in parts
		):
			results = []
			connectors = []
			for p in parts:
				p = p.strip()
				if p in ("and", "or"):
					connectors.append(p)
					continue
				if not p:
					continue
				m = simple_pattern.fullmatch(p)
				field = m.group(1)
				op_str = m.group(2)
				value = m.group(3) or m.group(4) or flt(m.group(5))
				doc_val = self.get(field)
				if isinstance(value, str):
					doc_val = str(doc_val or "")
				else:
					doc_val = flt(doc_val)
				results.append(ops[op_str](doc_val, value))

			# Evaluate and/or chain
			result = results[0] if results else True
			for i, conn in enumerate(connectors):
				if conn == "and":
					result = result and results[i + 1]
				else:
					result = result or results[i + 1]
			return result

		# Complex expressions are not supported — only declarative field comparisons
		# are allowed (e.g. doc.status == "Completed" and doc.grand_total > 0).
		frappe.throw(
			_("Workflow condition {0!r} uses unsupported syntax. "
			  "Only simple field comparisons are allowed: "
			  "doc.field_name == 'value' (joined by and/or).").format(condition_script),
			title=_("Invalid Workflow Condition"),
		)

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

		# N/A is a valid result for all items — technicians use it to mark checks
		# that do not apply to this repair (e.g. camera check on a screen-only repair).
		# Only truly blank results indicate an unanswered checklist item.
		incomplete = [
			row.check_name for row in checklist
			if not row.result or str(row.result).strip() == ""
		]
		if incomplete:
			frappe.throw(
				_("QC Checklist incomplete. The following checks have no result: {0}").format(
					", ".join(incomplete)
				),
				title=_("QC Incomplete"),
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
		frappe.throw(_("Cannot submit Service Order. Please create and complete Job Sheet first."), title=_("Validation Error"))

	incomplete = [js for js in job_sheets if js.assignment_status not in ["Completed", "Closed"]]
	if incomplete:
		names = ", ".join([js.name for js in incomplete])
		frappe.throw(_("Cannot submit. Job Sheet(s) {0} must be completed first.").format(names), title=_("Validation Error"))

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

			# Stamp QC Pass only when moving forward — never drag a Closed
			# order back (the Close action fires this hook via
			# on_update_after_submit and must stay Closed).
			if getattr(doc, 'workflow_state', None) not in ("QC Pass", "Closed"):
				doc.db_set("workflow_state", "QC Pass", update_modified=False)

			# Update QC metadata
			doc.db_set("qc_checked_by", frappe.session.user, update_modified=False)
			doc.db_set("qc_datetime", frappe.utils.now(), update_modified=False)
			doc.db_set("qc_pass_datetime", frappe.utils.now(), update_modified=False)

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
	"""Calculate and store service costing fields on QC Pass.
	
	Includes suggested pricing engine:
	  suggested_labor = sum(actual_hours × technician hourly rate) across Job Assignments
	  suggested_total = spare_parts_revenue + suggested_labor
	  price_override = actual_billed - suggested_total
	Also tracks technician damage cost from defective spare parts.
	"""
	if not doc.service_request:
		return

	# Sum spare parts costs
	parts = frappe.db.sql("""
		SELECT SUM(purchase_cost * qty_used) as total_cost,
			   SUM(sales_price * qty_used) as total_revenue
		FROM `tabSpare Parts Usage`
		WHERE service_request = %s
		  AND status = 'Active'
		  AND part_status IN ('Consumed', 'Issued')
	""", (doc.service_request,), as_dict=True)

	parts_cost = flt(parts[0].total_cost) if parts else 0
	parts_revenue = flt(parts[0].total_revenue) if parts else 0

	# ── Suggested Labor Cost from Job Assignment hours × Employee hourly rate ──
	job_sheets = frappe.get_all("Job Assignment",
		filters={
			"service_order": doc.name,
			"assignment_status": ["in", ["Completed", "Closed"]],
		},
		fields=["actual_hours", "service_engineer"])

	suggested_labor = 0
	for js in job_sheets:
		hours = flt(js.actual_hours)
		hourly_rate = 0
		if js.service_engineer:
			# Try to get hourly rate from Employee custom field, fallback to ctc/2080
			hourly_rate = (
				flt(frappe.db.get_value("Employee", js.service_engineer, "custom_hourly_rate"))
				if frappe.db.has_column("Employee", "custom_hourly_rate")
				else 0
			)
			if not hourly_rate:
				ctc = flt(frappe.db.get_value("Employee", js.service_engineer, "ctc"))
				if ctc:
					hourly_rate = ctc / 2080  # Annual CTC ÷ working hours/year
		suggested_labor += hours * hourly_rate

	# Use suggested labor if calculated, otherwise fall back to manual labor_cost
	labor_cost = flt(getattr(doc, "labor_cost", 0)) or suggested_labor
	total_cost = parts_cost + labor_cost
	total_revenue = flt(doc.grand_total) or flt(doc.total)
	margin = total_revenue - total_cost
	margin_pct = (margin / total_revenue * 100) if total_revenue else 0

	suggested_total = parts_revenue + suggested_labor
	price_override = total_revenue - suggested_total if suggested_total else 0

	doc.db_set("spare_parts_cost", parts_cost, update_modified=False)
	doc.db_set("spare_parts_revenue", parts_revenue, update_modified=False)
	doc.db_set("total_repair_cost", total_cost, update_modified=False)
	doc.db_set("repair_margin", margin, update_modified=False)
	doc.db_set("repair_margin_pct", margin_pct, update_modified=False)
	doc.db_set("suggested_labor_cost", suggested_labor, update_modified=False)
	doc.db_set("suggested_total_cost", suggested_total, update_modified=False)
	doc.db_set("price_override_amount", price_override, update_modified=False)
	if not getattr(doc, "labor_cost", None) and suggested_labor:
		doc.db_set("labor_cost", suggested_labor, update_modified=False)

	# ── Technician Damage Cost ──
	damage = frappe.db.sql("""
		SELECT COALESCE(SUM(purchase_cost * qty_used), 0) as damage_cost
		FROM `tabSpare Parts Usage`
		WHERE service_request = %s
		  AND is_defective = 1
		  AND defect_type = 'Installation Damage'
	""", (doc.service_request,), as_dict=True)
	damage_cost = flt(damage[0].damage_cost) if damage else 0
	doc.db_set("technician_damage_cost", damage_cost, update_modified=False)

	# Track who overrode the price if actual differs from suggested
	if abs(price_override) > 1 and not getattr(doc, "price_overridden_by", None):
		doc.db_set("price_overridden_by", frappe.session.user, update_modified=False)


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

	# Always reset to Awaiting (handles first QC and re-QC after rework)
	doc.db_set("qc_status", "Awaiting", update_modified=False)
	doc.db_set("workflow_state", "QC Awaiting", update_modified=False)

	# Auto-populate QC checklist from matching template (force repopulate on rework)
	is_rework = (getattr(doc, "rework_count", 0) or 0) > 0
	_populate_qc_checklist(doc, force=is_rework)


def _populate_qc_checklist(doc, force=False):
	"""Auto-populate QC checklist from GoFix QC Templates matching ALL issue categories.

	Merges checks from templates for each distinct active issue category on the
	Service Request (Fix #3).  When force=True, clears existing checklist first
	(used by rework flow — Fix #5).
	"""
	if not hasattr(doc, "qc_checklist"):
		return
	# Skip if already populated (unless forced)
	if doc.qc_checklist and not force:
		return

	if force:
		doc.qc_checklist = []

	# ── Gather ALL active issue categories from issue_lines (Fix #3) ──
	issue_categories = set()
	if doc.service_request:
		issue_lines = frappe.get_all(
			"SR Issue Line",
			filters={"parent": doc.service_request, "status": ["not in", ["Deleted", "Cancelled"]]},
			fields=["issue_category"],
		)
		issue_categories = {row.issue_category for row in issue_lines if row.issue_category}

	# Fallback to header-level field if no issue_lines
	if not issue_categories and doc.service_request:
		header_cat = frappe.db.get_value("Service Request", doc.service_request, "issue_category")
		if header_cat:
			issue_categories.add(header_cat)

	if not issue_categories:
		return

	# ── Find matching templates ──────────────────────────────────────────
	filters = {"is_active": 1}
	if doc.company:
		filters["company"] = ["in", [doc.company, "", None]]

	all_templates = frappe.get_all(
		"GoFix QC Template",
		filters=filters,
		fields=["name", "issue_category"],
		order_by="issue_category desc",
	)

	generic_template = None
	for t in all_templates:
		if not t.issue_category:
			generic_template = t
			break

	matched_templates = []
	for cat in sorted(issue_categories):
		found = False
		for t in all_templates:
			if t.issue_category == cat:
				matched_templates.append(t)
				found = True
				break
		if not found and generic_template:
			matched_templates.append(generic_template)

	# Deduplicate template names
	seen_templates = set()
	unique_templates = []
	for t in matched_templates:
		if t.name not in seen_templates:
			seen_templates.add(t.name)
			unique_templates.append(t)

	if not unique_templates:
		return

	# ── Merge checks from all matched templates ─────────────────────────
	seen_checks = set()
	for tmpl in unique_templates:
		tmpl_doc = frappe.get_doc("GoFix QC Template", tmpl.name)
		for check in tmpl_doc.checks:
			if check.check_name not in seen_checks:
				seen_checks.add(check.check_name)
				doc.append("qc_checklist", {
					"check_name": check.check_name,
					"is_mandatory": check.is_mandatory,
					"is_critical": getattr(check, "is_critical", 0),
					"check_type": check.get("check_type", "Pass-Fail"),
					"result": "",
				})

	doc.flags.ignore_validate = True
	doc.save(ignore_permissions=True)
