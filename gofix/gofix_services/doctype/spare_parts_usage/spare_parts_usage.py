# Copyright (c) 2024, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from frappe import _
from frappe.utils import cint, flt

from gofix.config import get_int_setting, is_privileged_user, require_role_setting
from gofix.security import assert_service_request_access


PART_STATUS_TRANSITIONS = {
	"Reserved": ["Issued", "Consumed", "Returned", "Defective"],
	"Issued": ["Consumed", "Returned", "Defective"],
	"Consumed": ["Returned", "Defective"],  # recovery on Not Repairable / BER
	"Returned": [],  # terminal
	"Defective": [],  # terminal
}

# Disposition choices when recovering a consumed spare
SPARE_DISPOSITION_CHOICES = (
	"Good - Back to Stock",        # spare condition good → Material Receipt to source warehouse
	"Faulty - Supplier Return",    # spare received faulty → Material Transfer to supplier return warehouse
	"Damaged by Technician",       # tech damaged it → Material Transfer to damaged stock warehouse
)


class SparePartsUsage(Document):
	_APPROVAL_CONTEXT = object()
	_STATE_CONTEXT = object()
	_APPROVAL_FIELDS = (
		"requires_approval",
		"approval_status",
		"approved_by",
		"approval_datetime",
		"approval_remarks",
		"approval_rule",
		"approval_threshold",
	)
	_APPROVAL_SENSITIVE_FIELDS = (
		"service_request",
		"service_request_spare_line",
		"spare_part_item",
		"qty_used",
		"sales_price",
		"purchase_cost",
		"warehouse",
		"barcode_value",
	)
	_SYSTEM_STATE_FIELDS = (
		"status",
		"part_status",
		"deleted",
		"is_defective",
		"defect_type",
		"defect_description",
		"defective_action",
		"defective_stock_entry",
		"stock_entry",
		"recovery_disposition",
		"recovery_stock_entry",
	)

	def before_insert(self):
		# These fields are projections of controlled actions, never caller input.
		self.status = "Active"
		self.part_status = "Reserved"
		self.deleted = 0
		self.is_defective = 0
		self.defect_type = None
		self.defect_description = None
		self.defective_action = None
		self.defective_stock_entry = None
		self.stock_entry = None
		self.recovery_disposition = None
		self.recovery_stock_entry = None
		if not self.transaction_date:
			self.transaction_date = frappe.utils.today()
		if not self.added_by_user:
			actor = frappe.session.user
			self.added_by_user = actor if frappe.db.exists("User", actor) else "Administrator"
		if self.service_request and not self.warehouse:
			self.warehouse = self._effective_warehouse()

	def _authorize_approval_transition(self):
		self.flags.spare_approval_context = self._APPROVAL_CONTEXT

	def _has_approval_context(self):
		return self.flags.get("spare_approval_context") is self._APPROVAL_CONTEXT

	def _authorize_state_transition(self):
		self.flags.spare_state_context = self._STATE_CONTEXT

	def _has_state_context(self):
		return self.flags.get("spare_state_context") is self._STATE_CONTEXT

	def _validate_system_state(self):
		if self.is_new() or self._has_state_context():
			return
		before = self.get_doc_before_save()
		if not before:
			return
		changed = {
			fieldname
			for fieldname in self._SYSTEM_STATE_FIELDS
			if self.get(fieldname) != before.get(fieldname)
		}
		# Submission is itself the controlled consumption action; on_submit then
		# creates and submits the authoritative Material Issue atomically.
		if self._action == "submit" and changed <= {"part_status"} and self.part_status == "Consumed":
			return
		if changed:
			frappe.throw(
				_("Spare lifecycle and stock-result fields can only be changed through authorised actions."),
				frappe.PermissionError,
			)

	def _validate_approval_evidence(self):
		if self._has_approval_context():
			return
		before = self.get_doc_before_save() if not self.is_new() else None
		if before is None:
			if any(self.get(fieldname) not in (None, "", 0, 0.0) for fieldname in self._APPROVAL_FIELDS):
				frappe.throw(_("Spare approval state and evidence are server-managed."), frappe.PermissionError)
			return
		if any(self.get(fieldname) != before.get(fieldname) for fieldname in self._APPROVAL_FIELDS):
			frappe.throw(
				_("Spare approval state can only be changed through the approval action."),
				frappe.PermissionError,
			)
		if before.approval_status == "Approved" and any(
			self.get(fieldname) != before.get(fieldname)
			for fieldname in self._APPROVAL_SENSITIVE_FIELDS
		):
			self.approval_status = ""
			self.approved_by = None
			self.approval_datetime = None
			self.approval_remarks = None

	def validate(self):
		"""Validate spare parts usage"""
		if self.docstatus == 0 and self.part_status == "Consumed" and self._action != "submit":
			frappe.throw(_("Consumed status is system-managed; use the consume action to submit this usage."))
		self._validate_system_state()
		self._validate_approval_evidence()
		self.validate_service_request()
		self.validate_plan_binding()
		self.validate_warehouse()
		self.validate_device_compatibility()
		self.validate_barcode()
		self.set_line_seq_no()
		self.fetch_item_details()
		self.check_approval_requirement()
		self.validate_approval_gate()
		self.validate_part_status_transition()
		if self.is_defective:
			self.part_status = "Defective"

	def validate_service_request(self):
		"""Validate that service request exists and is open"""
		if not self.service_request:
			frappe.throw(_("Service Request is mandatory"), title=_("Spare Parts Usage Error"))

		service_request = frappe.get_doc("Service Request", self.service_request)
		if service_request.decision in ["Completed", "Invoiced", "Delivered", "Cancelled", "Rejected", "Withdrawn"]:
			frappe.throw(_("Cannot add spare parts when Service Request is in status {0}").format(service_request.decision), title=_("Spare Parts Usage Error"))

	def validate_plan_binding(self):
		"""Require a single execution row for a planned Service Request spare."""
		if not self.service_request_spare_line:
			frappe.throw(_("Service Request Spare Line is mandatory."))
		row = frappe.db.get_value(
			"SR Spare Line",
			self.service_request_spare_line,
			["parent", "parenttype", "parentfield", "spare_item", "qty"],
			as_dict=True,
		)
		if not row or row.parenttype != "Service Request" or row.parentfield != "spare_lines":
			frappe.throw(_("The selected spare line is not attached to a Service Request."))
		if row.parent != self.service_request:
			frappe.throw(_("Spare usage and plan line belong to different Service Requests."))
		if row.spare_item != self.spare_part_item:
			frappe.throw(_("Spare usage item must match the planned spare item."))
		if flt(self.qty_used) > flt(row.qty):
			frappe.throw(_("Consumed quantity cannot exceed the planned quantity."))
		existing = frappe.db.get_value(
			"Spare Parts Usage",
			{
				"service_request_spare_line": self.service_request_spare_line,
				"name": ("!=", self.name or ""),
				"docstatus": ("<", 2),
			},
			"name",
		)
		if existing:
			frappe.throw(_("Spare line already has usage record {0}.").format(existing))

	def _effective_warehouse(self):
		sr = frappe.db.get_value(
			"Service Request",
			self.service_request,
			["company", "source_warehouse", "current_location", "transferred_to_store", "transfer_status"],
			as_dict=True,
		)
		if not sr:
			return None
		if sr.transfer_status in ("In Transit", "Received at Service Center") and sr.transferred_to_store:
			return sr.transferred_to_store
		return sr.current_location or sr.source_warehouse

	def validate_warehouse(self):
		if not self.warehouse:
			frappe.throw(_("Consumption Warehouse is mandatory."))
		sr_company = frappe.db.get_value("Service Request", self.service_request, "company")
		warehouse_company = frappe.db.get_value("Warehouse", self.warehouse, "company")
		if not warehouse_company or warehouse_company != sr_company:
			frappe.throw(_("Consumption Warehouse must belong to the Service Request company."))
		if self.warehouse != self._effective_warehouse():
			frappe.throw(_("Consumption Warehouse must match the device's current repair location."))

	def validate_device_compatibility(self):
		"""Block spares that are not compatible with the device being repaired.

		Universal spares (no compatibility rows on the Item) are always allowed.
		"""
		if not self.spare_part_item or not self.service_request:
			return

		from gofix.gofix_services.api import is_spare_compatible_with_device

		device_item = frappe.db.get_value("Service Request", self.service_request, "device_item")
		if device_item and not is_spare_compatible_with_device(self.spare_part_item, device_item):
			device_name = frappe.db.get_value("Service Request", self.service_request, "device_item_name") or device_item
			frappe.throw(
				_("Spare {0} is not compatible with device {1}.").format(self.spare_part_item, device_name),
				title=_("Incompatible Spare"),
			)

	def validate_barcode(self):
		"""Validate serialized repair stock against ERPNext's Serial No master.

		A draft Reserved usage is a commitment only.  A serialized part must be
		identified before it can be Issued/Consumed, and that identity must still
		be active in the exact consumption warehouse.
		"""
		item = frappe.get_cached_value(
			"Item",
			self.spare_part_item,
			["has_serial_no", "is_stock_item"],
			as_dict=True,
		) or frappe._dict()
		serial_no = (self.barcode_value or self.installed_part_serial or "").strip()
		if self.barcode_value and self.installed_part_serial:
			if self.barcode_value.strip() != self.installed_part_serial.strip():
				frappe.throw(_("Stock Serial No and installed part serial must match."))

		if item.get("has_serial_no"):
			if flt(self.qty_used) != 1:
				frappe.throw(_("Serialized spare usage must contain exactly one Serial No per row."))
			if self.part_status in ("Issued", "Consumed") and not serial_no:
				frappe.throw(_("Serial No is mandatory before issuing or consuming this spare."))
			if serial_no:
				serial = frappe.db.get_value(
					"Serial No",
					serial_no,
					["item_code", "warehouse", "status"],
					as_dict=True,
					for_update=True,
				)
				if not serial:
					frappe.throw(_("Serial No {0} does not exist in ERPNext stock.").format(serial_no))
				if serial.item_code != self.spare_part_item:
					frappe.throw(
						_("Serial No {0} belongs to item {1}, not {2}.").format(
							serial_no, serial.item_code, self.spare_part_item
						)
					)
				if serial.warehouse != self.warehouse or serial.status != "Active":
					frappe.throw(
						_("Serial No {0} is not active in warehouse {1}.").format(
							serial_no, self.warehouse
						)
					)
				self.barcode_value = serial_no
				self.installed_part_serial = serial_no

		if not self.barcode_value:
			return
		existing = frappe.db.get_value(
			"Spare Parts Usage",
			{
				"barcode_value": self.barcode_value,
				"name": ("!=", self.name or ""),
				"docstatus": ("<", 2),
				"deleted": 0,
				"status": "Active",
			},
			"name",
		)
		if existing:
			frappe.throw(
				_("Serial/barcode {0} is already committed on spare usage {1}.").format(
					self.barcode_value, existing
				),
				title=_("Spare Parts Usage Error"),
			)

	def set_line_seq_no(self):
		"""Set line sequence number"""
		if not self.line_seq_no:
			if not self.service_request:
				frappe.throw(_("Service Request is required before assigning a spare line number."))
			frappe.db.sql(
				"SELECT name FROM `tabService Request` WHERE name = %s FOR UPDATE",
				(self.service_request,),
			)
			last = frappe.db.sql(
				"""
				SELECT line_seq_no
				FROM `tabSpare Parts Usage`
				WHERE service_request = %s
				ORDER BY line_seq_no DESC
				LIMIT 1
				""",
				(self.service_request,),
			)
			self.line_seq_no = (cint(last[0][0]) if last else 0) + 1

	def fetch_item_details(self):
		"""Fetch item details from Item master"""
		if self.spare_part_item:
			item = frappe.get_cached_doc("Item", self.spare_part_item)

			if not self.purchase_cost:
				self.purchase_cost = item.valuation_rate or 0

			if not self.sales_price:
				self.sales_price = item.standard_rate or 0

	def check_approval_requirement(self):
		"""Check if this spare part usage requires manager approval."""
		total_value = flt(self.sales_price) * flt(self.qty_used)

		# Find matching approval rule
		rule = _get_matching_approval_rule(
			"Spare Part",
			total_value,
			self.service_request,
		)
		if rule:
			self.requires_approval = 1
			self.approval_rule = rule.name
			self.approval_threshold = rule.threshold_amount
			if self.approval_status != "Approved":
				self.approval_status = "Pending"
		else:
			self.requires_approval = 0
			self.approval_rule = None
			self.approval_threshold = 0
			self.approval_status = ""
			self.approved_by = None
			self.approval_datetime = None
			self.approval_remarks = None

	def validate_approval_gate(self):
		"""Block submission if approval is required but not granted."""
		if not self.requires_approval:
			return
		if self.approval_status == "Approved" and (
			not self.approved_by or not self.approval_datetime
		):
			frappe.throw(_("Approved spare usage is missing authoritative approval evidence."), frappe.PermissionError)
		if self.approval_status not in ("Approved",):
			if self.docstatus == 1 or self.part_status in ("Issued", "Consumed"):
				frappe.throw(
					_("Spare part {0} (₹{1}) requires approval before it can be issued/consumed. "
					  "Current approval status: {2}").format(
						self.spare_part_item,
						flt(self.sales_price) * flt(self.qty_used),
						self.approval_status or "Pending",
					),
					title=_("Approval Required"),
				)

	def validate_part_status_transition(self):
		"""Ensure part_status follows allowed transitions."""
		if self.is_new():
			return
		old = self.get_doc_before_save()
		if not old:
			return
		old_status = old.get("part_status") or "Reserved"
		new_status = self.part_status or "Reserved"
		if old_status == new_status:
			return
		allowed = PART_STATUS_TRANSITIONS.get(old_status, [])
		if new_status not in allowed:
			frappe.throw(
				_("Invalid part status transition: {0} → {1}. Allowed: {2}").format(
					old_status, new_status, ", ".join(allowed) or "None (terminal state)"
				)
			)

	def on_submit(self):
		"""Submit is the atomic boundary at which ERPNext stock is consumed."""
		if self.part_status != "Consumed":
			frappe.throw(_("A spare usage can only be submitted when the part is Consumed."))
		# High-value parts require manager approval before stock is debited
		if self.get("requires_approval") and self.get("approval_status") not in (
			"Approved",
			"Not Required",
		):
			frappe.throw(
				_("Spare part {0} requires manager approval before consumption. "
				  "Current approval status: {1}.").format(
					frappe.bold(self.spare_part_item),
					frappe.bold(self.get("approval_status") or "Pending"),
				),
				title=_("Approval Required"),
			)
		self.create_stock_entry()
		self.update_spare_parts_count()
		self.sync_to_service_request()
		self._log_parts_consumption()

	def sync_to_service_request(self):
		"""Project execution state onto the bound SR planning row."""
		if not self.service_request_spare_line:
			return
		frappe.db.set_value(
			"SR Spare Line",
			self.service_request_spare_line,
			{"status": self.part_status, "spare_usage": self.name},
			update_modified=False,
		)

	def create_stock_entry(self):
		"""Create stock entry for spare part consumption"""
		if self.status != "Active":
			return
		if self.stock_entry:
			if frappe.db.get_value("Stock Entry", self.stock_entry, "docstatus") != 1:
				frappe.throw(_("Linked Stock Entry must be submitted."))
			return

		service_request = frappe.get_doc("Service Request", self.service_request)
		company = service_request.company or frappe.defaults.get_user_default("Company")
		source_warehouse = self.warehouse

		if not source_warehouse:
			frappe.throw(_("Warehouse is required to issue spare part {0}").format(self.spare_part_item), title=_("Spare Parts Usage Error"))

		# Create Stock Entry for consumption
		stock_entry = frappe.new_doc("Stock Entry")
		stock_entry.stock_entry_type = "Material Issue"
		stock_entry.purpose = "Material Issue"
		stock_entry.company = company

		stock_entry.append("items", {
			"item_code": self.spare_part_item,
			"qty": self.qty_used,
			"uom": self.uom,
			"basic_rate": self.purchase_cost,
			"s_warehouse": source_warehouse,
			"serial_no": self.barcode_value if self.barcode_value else None
		})

		# Material Issue writes the consumption expense to the P&L, so it needs
		# the servicing store's Cost Center like the service invoice does.
		from ch_item_master.ch_core.cost_center import apply_cost_center

		apply_cost_center(stock_entry, warehouse=source_warehouse)

		try:
			frappe.has_permission("Stock Entry", "create", throw=True)
			stock_entry.insert()
			stock_entry.submit()
			self.db_set("stock_entry", stock_entry.name, update_modified=False)
			frappe.msgprint(_("Stock Entry {0} created").format(stock_entry.name))
		except Exception as e:
			frappe.log_error(message=str(e), title="Spare Parts Stock Entry Error")
			frappe.throw(
				_("Could not create stock entry for spare part {0}: {1}").format(
					self.spare_part_item, str(e)),
			title=_("Stock Entry Creation Failed"),
			)

	def on_cancel(self):
		if self.stock_entry and frappe.db.get_value("Stock Entry", self.stock_entry, "docstatus") == 1:
			frappe.throw(
				_("Cancel Stock Entry {0} before cancelling this spare usage.").format(self.stock_entry)
			)
		if self.service_request_spare_line:
			frappe.db.set_value(
				"SR Spare Line",
				self.service_request_spare_line,
				{"status": "Returned", "spare_usage": None},
				update_modified=False,
			)

	def update_spare_parts_count(self):
		"""Update spare parts count in service request"""
		total_count = frappe.db.count("Spare Parts Usage", {
			"service_request": self.service_request,
			"docstatus": 1,
			"deleted": 0,
		})

		billable_count = frappe.db.count("Spare Parts Usage", {
			"service_request": self.service_request,
			"docstatus": 1,
			"deleted": 0,
			"status": "Active",
			"part_status": ["in", ["Consumed", "Issued"]],
		})

		frappe.db.set_value("Service Request", self.service_request, {
			"total_spares_used_count": total_count,
			"billable_spares_count": billable_count
		}, update_modified=False)

	def _log_parts_consumption(self):
		"""GF-14 fix: Create Activity Log entry for spare parts consumption audit trail."""
		try:
			frappe.get_doc({
				"doctype": "Activity Log",
				"subject": _("Spare part {0} (x{1}) {2} for Service Request {3}").format(
					self.spare_part_item, self.qty_used, self.part_status, self.service_request
				),
				"content": _("Item: {0}, Qty: {1}, Status: {2}, Cost: {3}, Stock Entry: {4}").format(
					self.spare_part_item, self.qty_used, self.part_status,
					self.purchase_cost or 0, self.stock_entry or "N/A"
				),
				"reference_doctype": "Spare Parts Usage",
				"reference_name": self.name,
				"link_doctype": "Service Request",
				"link_name": self.service_request,
				"user": frappe.session.user,
			}).insert()
		except Exception:
			frappe.log_error(frappe.get_traceback(), _("Spare parts audit log failed"))

	def move_to_main_stock(self, reason):
		"""Release an unconsumed reservation; physical stock never moved."""
		if self.docstatus != 0:
			frappe.throw(_("Submitted consumption must use the spare recovery action."))
		if self.status != "Active":
			frappe.throw(_("Can only move active spare parts"), title=_("Spare Parts Usage Error"))

		self.status = "Moved to Main Stock"
		self.part_status = "Returned"
		self.deleted = 1
		self.narration = reason or "Reservation released"
		self.reason_desc = "Order Cancel"
		self.moved_to_stock_type = "Main Stock"

		self._authorize_state_transition()
		self.save()
		self._unsync_from_service_request()
		self.update_spare_parts_count()

		frappe.msgprint(_("Spare part moved to Main Stock"))

	def move_to_dispose_stock(self, reason):
		"""Segregate an unconsumed damaged spare into disposal stock."""
		if self.docstatus != 0:
			frappe.throw(_("Submitted consumption must use the spare recovery action."))
		if self.status != "Active":
			frappe.throw(_("Can only move active spare parts"), title=_("Spare Parts Usage Error"))

		self.status = "Moved to Dispose Stock"
		self.part_status = "Defective"
		self.is_defective = 1
		self.defect_type = "Other"
		self.defect_description = reason or "Moved to Dispose Stock"
		self.defective_action = "Dispose"
		self.deleted = 1
		self.narration = reason or "Moved to Dispose Stock"
		self.reason_desc = "Damage"
		self.moved_to_stock_type = "Dispose Stock"

		self._authorize_state_transition()
		self.save()
		self._create_defective_return_entry("Dispose")
		self._unsync_from_service_request()
		self.update_spare_parts_count()

		frappe.msgprint(_("Spare part moved to Dispose Stock"))

	def mark_defective(self, defect_type, description, action):
		"""Mark spare part as defective with details."""
		if self.part_status not in ("Issued", "Reserved"):
			frappe.throw(_("Only Reserved or Issued parts can be marked defective"), title=_("Spare Parts Usage Error"))
		if action not in ("Return to Vendor", "Dispose", "Send for Repair"):
			frappe.throw(_("Select a valid defective-stock action."))

		self.is_defective = 1
		self.part_status = "Defective"
		self.defect_type = defect_type
		self.defect_description = description
		self.defective_action = action

		self._authorize_state_transition()
		self.save()
		self._create_defective_return_entry(action)
		self._unsync_from_service_request()
		self.update_spare_parts_count()
		frappe.msgprint(_("Part marked as defective: {0}").format(defect_type))

	def _create_defective_return_entry(self, action="Return to Vendor"):
		"""Atomically move a defective part into the configured holding warehouse."""
		service_request = frappe.get_doc("Service Request", self.service_request)
		company = service_request.company or frappe.defaults.get_user_default("Company")
		source_warehouse = self.warehouse

		if action == "Return to Vendor":
			target_field, target_label = "supplier_return_warehouse", "Supplier Return Warehouse"
		elif action == "Dispose":
			target_field, target_label = "damaged_stock_warehouse", "Damaged Stock Warehouse"
		else:
			target_field, target_label = "master_hub_warehouse", "Master Hub Warehouse"
		target_warehouse = frappe.db.get_value("Company", company, target_field)
		if not target_warehouse:
			target_warehouse = frappe.db.get_value("Company", company, "master_hub_warehouse")
		if not source_warehouse or not target_warehouse or source_warehouse == target_warehouse:
			frappe.throw(
				_("Configure a distinct {0} for {1} before segregating defective stock.").format(
					target_label, company
				)
			)

		stock_entry = frappe.new_doc("Stock Entry")
		stock_entry.stock_entry_type = "Material Transfer"
		stock_entry.purpose = "Material Transfer"
		stock_entry.company = company
		stock_entry.remarks = f"Defective spare ({action}): {self.spare_part_item} from SR {self.service_request}"
		# This is an immediate, same-operation quarantine move—not a dispatch to
		# another store. The non-persistent flag is the guardrail's explicit
		# server-side exception; users still cannot submit direct transfers.
		stock_entry.flags.ignore_procurement_guardrails = True

		stock_entry.append("items", {
			"item_code": self.spare_part_item,
			"qty": self.qty_used,
			"uom": self.uom,
			"s_warehouse": source_warehouse,
			"t_warehouse": target_warehouse,
			"serial_no": self.barcode_value if self.barcode_value else None,
		})

		frappe.has_permission("Stock Entry", "create", throw=True)
		stock_entry.insert()
		stock_entry.submit()
		self.db_set("defective_stock_entry", stock_entry.name, update_modified=False)

	def create_return_stock_entry(self):
		"""Create stock entry for returning spare to warehouse"""
		service_request = frappe.get_doc("Service Request", self.service_request)
		company = service_request.company or frappe.defaults.get_user_default("Company")
		target_warehouse = service_request.source_warehouse or frappe.db.get_value(
			"Item Default",
			{"parent": self.spare_part_item, "company": company},
			"default_warehouse",
		) or frappe.db.get_single_value("Stock Settings", "default_warehouse")

		if not target_warehouse:
			frappe.throw(_("Warehouse is required to return spare part {0}").format(self.spare_part_item), title=_("Spare Parts Usage Error"))

		stock_entry = frappe.new_doc("Stock Entry")
		stock_entry.stock_entry_type = "Material Receipt"
		stock_entry.purpose = "Material Receipt"
		stock_entry.company = company

		stock_entry.append("items", {
			"item_code": self.spare_part_item,
			"qty": self.qty_used,
			"uom": self.uom,
			"basic_rate": self.purchase_cost,
			"t_warehouse": target_warehouse,
			"serial_no": self.barcode_value if self.barcode_value else None
		})

		try:
			frappe.has_permission("Stock Entry", "create", throw=True)
			stock_entry.insert()
			stock_entry.submit()
		except Exception as e:
			frappe.log_error(message=str(e), title="Spare Parts Return Stock Entry Error")

	# ── Spare Recovery (Not Repairable / BER) ─────────────────────────

	def recover_spare(self, disposition, remarks=None):
		"""Recover a consumed spare when a device is Not Repairable / BER.

		disposition must be one of SPARE_DISPOSITION_CHOICES:
		  - "Good - Back to Stock"      → Material Receipt to source warehouse
		  - "Faulty - Supplier Return"  → Material Transfer to supplier return warehouse
		  - "Damaged by Technician"     → Material Transfer to damaged stock warehouse
		"""
		if self.docstatus != 1 or self.part_status != "Consumed":
			frappe.throw(
				_("Only submitted, consumed parts can be recovered. Current status: {0}").format(self.part_status),
				title=_("Spare Parts Usage Error"),
			)
		if disposition not in SPARE_DISPOSITION_CHOICES:
			frappe.throw(
				_("Invalid disposition. Must be one of: {0}").format(", ".join(SPARE_DISPOSITION_CHOICES)),
				title=_("Spare Parts Usage Error"),
			)

		sr = frappe.get_doc("Service Request", self.service_request)
		company = sr.company or frappe.defaults.get_user_default("Company")
		source_wh = self.warehouse

		if disposition == "Good - Back to Stock":
			self._recover_to_stock(company, source_wh)
			self.part_status = "Returned"
			self.status = "Moved to Main Stock"
		elif disposition == "Faulty - Supplier Return":
			self._recover_to_warehouse(company, source_wh, "supplier_return_warehouse",
				fallback_field="master_hub_warehouse",
				remark_prefix="Faulty spare - supplier return")
			self.part_status = "Defective"
			self.status = "Moved to Main Stock"
			self.is_defective = 1
			self.defect_type = "Manufacture Defect"
			self.defective_action = "Return to Vendor"
		elif disposition == "Damaged by Technician":
			self._recover_to_warehouse(company, source_wh, "damaged_stock_warehouse",
				fallback_field="master_hub_warehouse",
				remark_prefix="Technician damage")
			self.part_status = "Defective"
			self.status = "Moved to Dispose Stock"
			self.is_defective = 1
			self.defect_type = "Technician Damage"
			self.defective_action = "Dispose"

		self.deleted = 1
		self.narration = f"Recovered: {disposition}" + (f" — {remarks}" if remarks else "")
		# reason_desc is a Select field; map disposition to closest valid option
		_disposition_reason_map = {
			"Good - Back to Stock": "Wrong Spare",
			"Faulty - Supplier Return": "Manufacture Defect",
			"Damaged by Technician": "Damage",
		}
		self.reason_desc = _disposition_reason_map.get(disposition, "")
		self.recovery_disposition = disposition

		self.db_set({
			"part_status": self.part_status,
			"status": self.status,
			"deleted": self.deleted,
			"narration": self.narration,
			"reason_desc": self.reason_desc,
			"recovery_disposition": self.recovery_disposition,
			"is_defective": self.is_defective,
			"defect_type": self.defect_type,
			"defective_action": self.defective_action,
		}, update_modified=True)
		self.update_spare_parts_count()
		self._unsync_from_service_request()
		self._log_parts_consumption()

		frappe.msgprint(_("Spare {0} recovered: {1}").format(self.spare_part_item, disposition))

	def _recover_to_stock(self, company, source_wh):
		"""Material Receipt — good condition spare back to original warehouse."""
		target_wh = source_wh or frappe.db.get_value(
			"Item Default", {"parent": self.spare_part_item, "company": company}, "default_warehouse"
		) or frappe.db.get_single_value("Stock Settings", "default_warehouse")
		if not target_wh:
			frappe.throw(_("No warehouse found to return spare {0}").format(self.spare_part_item))

		se = frappe.new_doc("Stock Entry")
		se.stock_entry_type = "Material Receipt"
		se.company = company
		se.remarks = f"Spare recovery (good condition): {self.spare_part_item} from SR {self.service_request}"
		se.append("items", {
			"item_code": self.spare_part_item,
			"qty": self.qty_used,
			"uom": self.uom,
			"basic_rate": self.purchase_cost,
			"t_warehouse": target_wh,
			"serial_no": self.barcode_value if self.barcode_value else None,
		})
		frappe.has_permission("Stock Entry", "create", throw=True)
		se.insert()
		se.submit()
		self.db_set("recovery_stock_entry", se.name, update_modified=False)

	def _recover_to_warehouse(self, company, source_wh, company_wh_field,
							  fallback_field="master_hub_warehouse", remark_prefix=""):
		"""Material Receipt into a segregated warehouse (supplier return / damaged)."""
		target_wh = frappe.db.get_value("Company", company, company_wh_field)
		if not target_wh:
			target_wh = frappe.db.get_value("Company", company, fallback_field)
		if not target_wh:
			target_wh = source_wh
		if not target_wh:
			frappe.throw(_("No target warehouse configured for {0}").format(company_wh_field))

		se = frappe.new_doc("Stock Entry")
		se.stock_entry_type = "Material Receipt"
		se.company = company
		se.remarks = f"{remark_prefix}: {self.spare_part_item} from SR {self.service_request}"
		se.append("items", {
			"item_code": self.spare_part_item,
			"qty": self.qty_used,
			"uom": self.uom,
			"basic_rate": self.purchase_cost,
			"t_warehouse": target_wh,
			"serial_no": self.barcode_value if self.barcode_value else None,
		})
		frappe.has_permission("Stock Entry", "create", throw=True)
		se.insert()
		se.submit()
		self.db_set("recovery_stock_entry", se.name, update_modified=False)

	def _unsync_from_service_request(self):
		"""Reflect recovery on the bound SR planning row."""
		try:
			if self.service_request_spare_line:
				frappe.db.set_value(
					"SR Spare Line",
					self.service_request_spare_line,
					{"status": self.part_status, "spare_usage": self.name},
					update_modified=False,
				)
		except Exception:
			frappe.log_error(frappe.get_traceback(), f"Failed to unsync spare {self.name} from SR")


def _get_matching_approval_rule(rule_type, value, service_request=None):
	"""Find the best matching GoFix Approval Rule for a given type and value."""
	if not frappe.db.exists("DocType", "GoFix Approval Rule"):
		return None

	filters = {"is_active": 1, "rule_type": rule_type}
	rules = frappe.get_all("GoFix Approval Rule",
		filters=filters,
		fields=["name", "threshold_amount", "approver_role"],
		order_by="threshold_amount asc")

	for rule in rules:
		if flt(value) >= flt(rule.threshold_amount):
			return rule
	return None


# API Methods
def _get_locked_spare_usage(name):
	doc = frappe.get_doc("Spare Parts Usage", name)
	doc.check_permission("write")
	assert_service_request_access(doc.service_request, permission_type="write")
	if not frappe.db.get_value("Spare Parts Usage", doc.name, "name", for_update=True):
		frappe.throw(_("Spare Parts Usage {0} no longer exists.").format(name), frappe.DoesNotExistError)
	doc.reload()
	doc.check_permission("write")
	assert_service_request_access(doc.service_request, permission_type="write")
	return doc


@frappe.whitelist(methods=["POST"])
def move_to_main_stock(name, reason) -> dict:
	"""Whitelisted method to move spare part to main stock"""
	frappe.has_permission("Spare Parts Usage", ptype="write", throw=True)
	doc = _get_locked_spare_usage(name)
	doc.move_to_main_stock(reason)
	return {"message": "Moved to Main Stock"}


@frappe.whitelist(methods=["POST"])
def move_to_dispose_stock(name, reason) -> dict:
	"""Whitelisted method to move spare part to dispose stock"""
	frappe.has_permission("Spare Parts Usage", ptype="submit", throw=True)
	doc = _get_locked_spare_usage(name)
	doc.move_to_dispose_stock(reason)
	return {"message": "Moved to Dispose Stock"}


@frappe.whitelist(methods=["POST"])
def mark_defective(name, defect_type, description=None, action=None) -> dict:
	"""Mark a spare part as defective."""
	frappe.has_permission("Spare Parts Usage", ptype="write", throw=True)
	doc = _get_locked_spare_usage(name)
	doc.mark_defective(defect_type, description or "", action or "")
	return {"message": f"Marked as defective: {defect_type}"}


@frappe.whitelist(methods=["POST"])
def change_part_status(name, new_status) -> dict:
	"""Transition part status through lifecycle."""
	frappe.has_permission("Spare Parts Usage", ptype="write", throw=True)
	doc = _get_locked_spare_usage(name)
	if doc.docstatus != 0:
		frappe.throw(_("Submitted spare usage is immutable. Use the recovery action for a consumed part."))

	old_status = doc.part_status or "Reserved"
	allowed = PART_STATUS_TRANSITIONS.get(old_status, [])
	if new_status not in allowed:
		frappe.throw(_("Cannot transition from {0} to {1}. Allowed: {2}").format(
			old_status, new_status, ", ".join(allowed) or "None"))

	if new_status == "Consumed":
		doc._authorize_state_transition()
		doc.part_status = new_status
		doc.validate_approval_gate()
		doc.submit()
	else:
		doc._authorize_state_transition()
		doc.part_status = new_status
		doc.validate_approval_gate()
		doc.save()

	if new_status == "Returned":
		doc.status = "Moved to Main Stock"
		doc.deleted = 1
		doc.narration = "Part returned unused"
		doc._authorize_state_transition()
		doc.save()
		doc.sync_to_service_request()
		doc.update_spare_parts_count()
		return {"message": "Unused reservation returned"}

	return {
		"message": f"Part status changed to {new_status}",
		"stock_entry": doc.stock_entry if new_status == "Consumed" else None,
	}


@frappe.whitelist(methods=["POST"])
def approve_spare_part(name, remarks=None) -> dict:
	"""Approve a spare part that requires approval."""
	frappe.has_permission("Spare Parts Usage", ptype="submit", throw=True)
	doc = _get_locked_spare_usage(name)
	if not doc.requires_approval:
		frappe.throw(_("This spare part does not require approval"), title=_("Spare Parts Usage Error"))
	if doc.approval_status == "Approved":
		frappe.throw(_("Already approved"), title=_("Spare Parts Usage Error"))
	if doc.owner == frappe.session.user and not is_privileged_user():
		frappe.throw(_("The spare usage creator cannot approve their own request."), frappe.PermissionError)
	required_role = frappe.db.get_value("GoFix Approval Rule", doc.approval_rule, "approver_role")
	if required_role and not is_privileged_user() and required_role not in frappe.get_roles():
		frappe.throw(
			_("This approval requires the configured {0} role.").format(required_role),
			frappe.PermissionError,
		)

	doc._authorize_approval_transition()
	doc.approval_status = "Approved"
	doc.approved_by = frappe.session.user
	doc.approval_datetime = frappe.utils.now()
	doc.approval_remarks = remarks or ""
	doc.save()
	return {"message": "Spare part approved"}


@frappe.whitelist(methods=["POST"])
def recover_spare(name, disposition, remarks=None) -> dict:
	"""Recover a consumed spare part when device is Not Repairable.

	disposition: "Good - Back to Stock" | "Faulty - Supplier Return" | "Damaged by Technician"
	"""
	frappe.has_permission("Spare Parts Usage", ptype="write", throw=True)
	doc = _get_locked_spare_usage(name)
	doc.recover_spare(disposition, remarks)
	return {"message": f"Spare recovered: {disposition}"}


@frappe.whitelist()
def get_pending_recovery_spares(service_request) -> list:
	"""Return consumed spares that need recovery disposition for a Service Request."""
	assert_service_request_access(service_request, permission_type="read")
	frappe.has_permission("Spare Parts Usage", "read", throw=True)
	return frappe.get_all("Spare Parts Usage",
		filters={
			"service_request": service_request,
			"part_status": "Consumed",
			"deleted": 0,
			"status": "Active",
		},
		fields=["name", "spare_part_item", "item_name", "qty_used", "uom",
				"barcode_value", "purchase_cost", "sales_price"],
		limit_page_length=get_int_setting("token_queue_limit", 200),
	)


@frappe.whitelist(methods=["POST"])
def bulk_recover_spares(service_request, dispositions_json) -> dict:
	"""Recover all consumed spares for a service request in one go.

	dispositions_json: JSON list of {"spu_name": "...", "disposition": "...", "remarks": "..."}
	"""
	import json as _json
	dispositions = _json.loads(dispositions_json) if isinstance(dispositions_json, str) else dispositions_json
	if not isinstance(dispositions, list):
		frappe.throw(_("Dispositions must be a JSON list."), frappe.ValidationError)
	row_limit = get_int_setting("token_queue_limit", 200)
	if len(dispositions) > row_limit:
		frappe.throw(
			_("A maximum of {0} spares can be recovered at once.").format(row_limit),
			frappe.ValidationError,
		)
	assert_service_request_access(service_request, permission_type="write")
	frappe.has_permission("Spare Parts Usage", "write", throw=True)
	spu_names = tuple(dict.fromkeys(
		entry.get("spu_name")
		for entry in dispositions
		if isinstance(entry, dict) and entry.get("spu_name")
	))
	spu_rows = frappe.get_all(
		"Spare Parts Usage",
		filters={"name": ("in", spu_names)},
		fields=["name", "service_request"],
		limit_page_length=len(spu_names),
	) if spu_names else []
	spu_service_request = {row.name: row.service_request for row in spu_rows}

	recovered = []
	errors = []
	for entry in dispositions:
		if not isinstance(entry, dict) or not entry.get("spu_name"):
			errors.append({"spu_name": None, "error": _("Invalid disposition row.")})
			continue
		if spu_service_request.get(entry["spu_name"]) != service_request:
			errors.append({
				"spu_name": entry["spu_name"],
				"error": _("Spare does not belong to this Service Request."),
			})
			continue
		try:
			doc = frappe.get_doc("Spare Parts Usage", entry["spu_name"])
			doc.check_permission("write")
			doc.recover_spare(entry["disposition"], entry.get("remarks"))
			recovered.append(entry["spu_name"])
		except Exception:
			errors.append({
				"spu_name": entry["spu_name"],
				"error": _("Spare recovery failed. Review the server error log."),
			})
			frappe.log_error(frappe.get_traceback(),
				f"Spare recovery failed: {entry['spu_name']}")

	return {
		"recovered": len(recovered),
		"errors": errors,
		"message": _("{0} spare(s) recovered, {1} error(s)").format(len(recovered), len(errors)),
	}
