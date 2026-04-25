# Copyright (c) 2024, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from frappe import _
from frappe.utils import flt


PART_STATUS_TRANSITIONS = {
	"Reserved": ["Issued", "Returned"],
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
	def validate(self):
		"""Validate spare parts usage"""
		self.validate_service_request()
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
		if service_request.status in ["Completed", "Invoiced", "Delivered", "Cancelled", "Rejected", "Withdrawn"]:
			frappe.throw(_("Cannot add spare parts when Service Request is in status {0}").format(service_request.status), title=_("Spare Parts Usage Error"))

	def validate_barcode(self):
		"""Validate barcode uniqueness and availability"""
		if not self.barcode_value:
			return

		# Check if barcode already used in this service
		existing = frappe.db.sql("""
			SELECT name FROM `tabSpare Parts Usage`
			WHERE barcode_value = %s
			AND service_request = %s
			AND name != %s
			AND deleted = 0
		""", (self.barcode_value, self.service_request, self.name or ""))

		if existing:
			frappe.throw(_("Barcode {0} is already used in this service request").format(self.barcode_value), title=_("Spare Parts Usage Error"))

		# Check if barcode exists in stock and is available
		barcode_exists = frappe.db.exists("Serial No", {"name": self.barcode_value})
		if not barcode_exists:
			frappe.msgprint(_("Barcode {0} not found in system").format(self.barcode_value), alert=True)

	def set_line_seq_no(self):
		"""Set line sequence number"""
		if not self.line_seq_no:
			max_seq = frappe.db.sql("""
				SELECT MAX(line_seq_no) as max_seq
				FROM `tabSpare Parts Usage`
				WHERE service_request = %s
			""", self.service_request, as_dict=1)

			self.line_seq_no = (max_seq[0].max_seq or 0) + 1 if max_seq else 1

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
		if self.approval_status == "Approved":
			return  # Already approved

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
			if not self.approval_status:
				self.approval_status = "Pending"

	def validate_approval_gate(self):
		"""Block submission if approval is required but not granted."""
		if not self.requires_approval:
			return
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
		"""On submit: create stock entry only if consumed and approved."""
		if self.part_status == "Consumed":
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
		"""Add this spare part to the Service Request spare_parts child table
		so it is included in the auto-generated repair invoice."""
		if self.status != "Active" or self.part_status not in ("Consumed", "Issued"):
			return
		sr = frappe.get_doc("Service Request", self.service_request)
		# Avoid duplicates — check if already synced
		for row in sr.spare_parts:
			if row.get("spu_reference") == self.name:
				return
		sr.append("spare_parts", {
			"spare_part_item": self.spare_part_item,
			"qty": self.qty_used,
			"uom": self.uom,
			"rate": self.sales_price or 0,
			"amount": (self.qty_used or 0) * (self.sales_price or 0),
			"spu_reference": self.name,
		})
		sr.flags.ignore_validate = True
		sr.save(ignore_permissions=True)

	def create_stock_entry(self):
		"""Create stock entry for spare part consumption"""
		if self.status != "Active":
			return

		service_request = frappe.get_doc("Service Request", self.service_request)
		company = service_request.company or frappe.defaults.get_user_default("Company")
		source_warehouse = service_request.source_warehouse or frappe.db.get_value(
			"Item Default",
			{"parent": self.spare_part_item, "company": company},
			"default_warehouse",
		) or frappe.db.get_value("Item", self.spare_part_item, "default_warehouse")

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

		try:
			stock_entry.insert(ignore_permissions=True)
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

	def update_spare_parts_count(self):
		"""Update spare parts count in service request"""
		total_count = frappe.db.count("Spare Parts Usage", {
			"service_request": self.service_request
		})

		billable_count = frappe.db.count("Spare Parts Usage", {
			"service_request": self.service_request,
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
			}).insert(ignore_permissions=True)
		except Exception:
			frappe.log_error(frappe.get_traceback(), _("Spare parts audit log failed"))

	def move_to_main_stock(self, reason):
		"""Move spare part back to main stock"""
		if self.status != "Active":
			frappe.throw(_("Can only move active spare parts"), title=_("Spare Parts Usage Error"))

		self.status = "Moved to Main Stock"
		self.part_status = "Returned"
		self.deleted = 1
		self.narration = "Moved to Main Stock"
		self.reason_desc = reason
		self.moved_to_stock_type = "Main Stock"

		# Create stock entry to return to warehouse
		self.create_return_stock_entry()

		self.save(ignore_permissions=True)
		self.update_spare_parts_count()

		frappe.msgprint(_("Spare part moved to Main Stock"))

	def move_to_dispose_stock(self, reason):
		"""Move spare part to dispose stock"""
		if self.status != "Active":
			frappe.throw(_("Can only move active spare parts"), title=_("Spare Parts Usage Error"))

		self.status = "Moved to Dispose Stock"
		self.part_status = "Defective"
		self.deleted = 1
		self.narration = "Moved to Dispose Stock"
		self.reason_desc = reason
		self.moved_to_stock_type = "Dispose Stock"

		self.save(ignore_permissions=True)
		self.update_spare_parts_count()

		frappe.msgprint(_("Spare part moved to Dispose Stock"))

	def mark_defective(self, defect_type, description, action):
		"""Mark spare part as defective with details."""
		if self.part_status not in ("Issued", "Reserved"):
			frappe.throw(_("Only Reserved or Issued parts can be marked defective"), title=_("Spare Parts Usage Error"))

		self.is_defective = 1
		self.part_status = "Defective"
		self.defect_type = defect_type
		self.defect_description = description
		self.defective_action = action

		if action == "Return to Vendor":
			self._create_defective_return_entry()

		self.save(ignore_permissions=True)
		self.update_spare_parts_count()
		frappe.msgprint(_("Part marked as defective: {0}").format(defect_type))

	def _create_defective_return_entry(self):
		"""Create stock entry to move defective part to a holding warehouse."""
		service_request = frappe.get_doc("Service Request", self.service_request)
		company = service_request.company or frappe.defaults.get_user_default("Company")
		source_warehouse = service_request.source_warehouse

		# Use company's master hub warehouse as defective holding area
		target_warehouse = frappe.db.get_value(
			"Company", company, "master_hub_warehouse"
		) or source_warehouse

		if not source_warehouse:
			return

		stock_entry = frappe.new_doc("Stock Entry")
		stock_entry.stock_entry_type = "Material Transfer"
		stock_entry.purpose = "Material Transfer"
		stock_entry.company = company
		stock_entry.remarks = f"Defective part return: {self.spare_part_item} from SR {self.service_request}"

		stock_entry.append("items", {
			"item_code": self.spare_part_item,
			"qty": self.qty_used,
			"uom": self.uom,
			"s_warehouse": source_warehouse,
			"t_warehouse": target_warehouse,
			"serial_no": self.barcode_value if self.barcode_value else None,
		})

		try:
			stock_entry.insert(ignore_permissions=True)
			stock_entry.submit()
			self.db_set("defective_stock_entry", stock_entry.name, update_modified=False)
		except Exception as e:
			frappe.log_error(message=str(e), title="Defective Part Stock Entry Error")

	def create_return_stock_entry(self):
		"""Create stock entry for returning spare to warehouse"""
		service_request = frappe.get_doc("Service Request", self.service_request)
		company = service_request.company or frappe.defaults.get_user_default("Company")
		target_warehouse = service_request.source_warehouse or frappe.db.get_value(
			"Item Default",
			{"parent": self.spare_part_item, "company": company},
			"default_warehouse",
		) or frappe.db.get_value("Item", self.spare_part_item, "default_warehouse")

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
			stock_entry.insert(ignore_permissions=True)
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
		if self.part_status != "Consumed":
			frappe.throw(
				_("Only consumed parts can be recovered. Current status: {0}").format(self.part_status),
				title=_("Spare Parts Usage Error"),
			)
		if disposition not in SPARE_DISPOSITION_CHOICES:
			frappe.throw(
				_("Invalid disposition. Must be one of: {0}").format(", ".join(SPARE_DISPOSITION_CHOICES)),
				title=_("Spare Parts Usage Error"),
			)

		sr = frappe.get_doc("Service Request", self.service_request)
		company = sr.company or frappe.defaults.get_user_default("Company")
		source_wh = sr.source_warehouse

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

		self.save(ignore_permissions=True)
		self.update_spare_parts_count()
		self._unsync_from_service_request()
		self._log_parts_consumption()

		frappe.msgprint(_("Spare {0} recovered: {1}").format(self.spare_part_item, disposition))

	def _recover_to_stock(self, company, source_wh):
		"""Material Receipt — good condition spare back to original warehouse."""
		target_wh = source_wh or frappe.db.get_value(
			"Item Default", {"parent": self.spare_part_item, "company": company}, "default_warehouse"
		) or frappe.db.get_value("Item", self.spare_part_item, "default_warehouse")
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
		se.insert(ignore_permissions=True)
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
		se.insert(ignore_permissions=True)
		se.submit()
		self.db_set("recovery_stock_entry", se.name, update_modified=False)

	def _unsync_from_service_request(self):
		"""Remove this spare from the SR spare_parts child table after recovery."""
		try:
			sr = frappe.get_doc("Service Request", self.service_request)
			to_remove = [row for row in sr.spare_parts if row.get("spu_reference") == self.name]
			for row in to_remove:
				sr.remove(row)
			if to_remove:
				sr.flags.ignore_validate = True
				sr.save(ignore_permissions=True)
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
@frappe.whitelist()
def move_to_main_stock(name, reason) -> dict:
	"""Whitelisted method to move spare part to main stock"""
	frappe.only_for(["Sales Manager", "System Manager", "Service Manager", "Sales User"])
	doc = frappe.get_doc("Spare Parts Usage", name)
	doc.move_to_main_stock(reason)
	return {"message": "Moved to Main Stock"}


@frappe.whitelist()
def move_to_dispose_stock(name, reason) -> dict:
	"""Whitelisted method to move spare part to dispose stock"""
	frappe.only_for(["Sales Manager", "System Manager", "Service Manager"])
	doc = frappe.get_doc("Spare Parts Usage", name)
	doc.move_to_dispose_stock(reason)
	return {"message": "Moved to Dispose Stock"}


@frappe.whitelist()
def mark_defective(name, defect_type, description=None, action=None) -> dict:
	"""Mark a spare part as defective."""
	frappe.only_for(["Sales Manager", "System Manager", "Service Manager", "Service Engineer"])
	doc = frappe.get_doc("Spare Parts Usage", name)
	doc.mark_defective(defect_type, description or "", action or "")
	return {"message": f"Marked as defective: {defect_type}"}


@frappe.whitelist()
def change_part_status(name, new_status) -> dict:
	"""Transition part status through lifecycle."""
	frappe.only_for(["Sales Manager", "System Manager", "Service Manager", "Service Engineer", "Sales User"])
	doc = frappe.get_doc("Spare Parts Usage", name)

	old_status = doc.part_status or "Reserved"
	allowed = PART_STATUS_TRANSITIONS.get(old_status, [])
	if new_status not in allowed:
		frappe.throw(_("Cannot transition from {0} to {1}. Allowed: {2}").format(
			old_status, new_status, ", ".join(allowed) or "None"))

	doc.part_status = new_status

	# If consumed and not yet submitted, create stock entry
	if new_status == "Consumed" and not doc.stock_entry:
		doc.create_stock_entry()
	elif new_status == "Returned":
		doc.move_to_main_stock("Part returned unused")
		return {"message": "Part returned to stock"}

	doc.save(ignore_permissions=True)
	return {"message": f"Part status changed to {new_status}"}


@frappe.whitelist()
def approve_spare_part(name, remarks=None) -> dict:
	"""Approve a spare part that requires approval."""
	frappe.only_for(["Sales Manager", "System Manager", "Service Manager", "Store Manager"])
	doc = frappe.get_doc("Spare Parts Usage", name)
	if not doc.requires_approval:
		frappe.throw(_("This spare part does not require approval"), title=_("Spare Parts Usage Error"))
	if doc.approval_status == "Approved":
		frappe.throw(_("Already approved"), title=_("Spare Parts Usage Error"))

	doc.approval_status = "Approved"
	doc.approved_by = frappe.session.user
	doc.approval_datetime = frappe.utils.now()
	doc.approval_remarks = remarks or ""
	doc.save(ignore_permissions=True)
	return {"message": "Spare part approved"}


@frappe.whitelist()
def recover_spare(name, disposition, remarks=None) -> dict:
	"""Recover a consumed spare part when device is Not Repairable.

	disposition: "Good - Back to Stock" | "Faulty - Supplier Return" | "Damaged by Technician"
	"""
	frappe.only_for(["Sales Manager", "System Manager", "Service Manager", "Service Engineer"])
	doc = frappe.get_doc("Spare Parts Usage", name)
	doc.recover_spare(disposition, remarks)
	return {"message": f"Spare recovered: {disposition}"}


@frappe.whitelist()
def get_pending_recovery_spares(service_request) -> list:
	"""Return consumed spares that need recovery disposition for a Service Request."""
	frappe.has_permission("Service Request", doc=service_request, throw=True)
	return frappe.get_all("Spare Parts Usage",
		filters={
			"service_request": service_request,
			"part_status": "Consumed",
			"deleted": 0,
			"status": "Active",
		},
		fields=["name", "spare_part_item", "item_name", "qty_used", "uom",
				"barcode_value", "purchase_cost", "sales_price"],
	)


@frappe.whitelist()
def bulk_recover_spares(service_request, dispositions_json) -> dict:
	"""Recover all consumed spares for a service request in one go.

	dispositions_json: JSON list of {"spu_name": "...", "disposition": "...", "remarks": "..."}
	"""
	import json as _json
	frappe.only_for(["Sales Manager", "System Manager", "Service Manager", "Service Engineer"])
	dispositions = _json.loads(dispositions_json) if isinstance(dispositions_json, str) else dispositions_json

	recovered = []
	errors = []
	for entry in dispositions:
		try:
			doc = frappe.get_doc("Spare Parts Usage", entry["spu_name"])
			doc.recover_spare(entry["disposition"], entry.get("remarks"))
			recovered.append(entry["spu_name"])
		except Exception as e:
			errors.append({"spu_name": entry["spu_name"], "error": str(e)})
			frappe.log_error(frappe.get_traceback(),
				f"Spare recovery failed: {entry['spu_name']}")

	frappe.db.commit()
	return {
		"recovered": len(recovered),
		"errors": errors,
		"message": _("{0} spare(s) recovered, {1} error(s)").format(len(recovered), len(errors)),
	}
