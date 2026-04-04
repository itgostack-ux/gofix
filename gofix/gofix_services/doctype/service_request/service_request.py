# Copyright (c) 2025, GoFix and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import getdate, today, add_days, flt, nowdate


class ServiceRequest(Document):
	def before_insert(self):
		"""Set defaults before first insert"""
		self.set_warehouse_defaults()
		self.set_received_by()
	
	def validate(self):
		self.detect_customer_type()
		self.check_open_requests()
		self.fetch_customer_details()
		# Fetch warehouse details - especially state for GST
		if self.source_warehouse:
			self.fetch_warehouse_details()
		self.fetch_warranty_from_serial()
		self.validate_withdrawal()
		self.validate_contact_details()
		self.validate_referral_code()
		self.validate_dates()
		self.validate_mandatory_fields()
		self.validate_issue_solution_cascade()
		self.sync_decision_to_status()
		self._validate_serial_substitution()
		self._validate_service_discount()
		self._detect_repeat_complaint()

	def before_save(self):
		"""Generate barcode if not exists and fetch warehouse details"""
		self.generate_barcode()
		# Fetch warehouse state details if missing
		if self.source_warehouse and (not self.state_name or not self.state_code):
			self.fetch_warehouse_details()
		# Sync walk-in status with decision
		self.sync_walkin_status()
		# Log status transitions
		self._log_status_transition()

	def _log_status_transition(self):
		"""Append a GoFix Status Log row whenever decision/status changes."""
		old_decision = (self.get_doc_before_save() or {}).get("decision") if self.get_doc_before_save() else None
		new_decision = self.decision
		if not old_decision or old_decision == new_decision:
			return
		from frappe.utils import now_datetime, time_diff_in_hours
		prev_at = None
		if self.status_log:
			prev_at = self.status_log[-1].changed_at
		elapsed = 0
		if prev_at:
			elapsed = round(time_diff_in_hours(now_datetime(), prev_at), 2)
		self.append("status_log", {
			"from_status": old_decision,
			"to_status": new_decision,
			"changed_by": frappe.session.user,
			"changed_at": now_datetime(),
			"time_in_previous_status_hours": elapsed,
		})

		# GF-11 fix: Notify customer on key status changes
		self._notify_customer_on_status_change(old_decision, new_decision)
	
	def set_received_by(self):
		"""Set received_by to current user if not set"""
		if not self.received_by:
			self.received_by = frappe.session.user
	
	def set_warehouse_defaults(self):
		"""Set default warehouse from user defaults"""
		if not self.source_warehouse:
			# Get user's default warehouse
			user_warehouse = frappe.defaults.get_user_default("warehouse")
			
			if user_warehouse:
				self.source_warehouse = user_warehouse
			elif self.company:
				# Fallback to first non-group warehouse of company
				warehouse = frappe.db.get_value("Warehouse", 
					{"company": self.company, "is_group": 0},
					"name",
					order_by="name"
				)
				if warehouse:
					self.source_warehouse = warehouse
		
		# Set current location same as source initially
		if self.source_warehouse and not self.current_location:
			self.current_location = self.source_warehouse
	
	def fetch_warehouse_details(self):
		"""Fetch warehouse address and state details"""
		if self.source_warehouse:
			# Get warehouse details
			warehouse = frappe.get_doc("Warehouse", self.source_warehouse)
			
			# Get address from warehouse if linked
			if warehouse.address:
				self.warehouse_address = warehouse.address
				
				# Fetch state details from address
				address = frappe.get_doc("Address", warehouse.address)
				if address.state:
					self.state_name = address.state
				if address.get("gst_state_number"):
					self.state_code = address.get("gst_state_number")
			elif warehouse.company:
				# Fallback: Try to get from company's default address
				try:
					company = frappe.get_doc("Company", warehouse.company)
					# Try to get any linked address for the company
					company_addresses = frappe.get_all("Dynamic Link",
						filters={
							"link_doctype": "Company",
							"link_name": warehouse.company,
							"parenttype": "Address"
						},
						fields=["parent"],
						limit=1)
					if company_addresses:
						address = frappe.get_doc("Address", company_addresses[0].parent)
						if address.state and not self.state_name:
							self.state_name = address.state
						if address.get("gst_state_number") and not self.state_code:
							self.state_code = address.get("gst_state_number")
				except Exception:
					pass  # Skip fallback if company address not found

	def detect_customer_type(self):
		"""Detect if customer is NEW or REGULAR based on previous service requests"""
		if self.customer and not self.customer_type:
			# Check if customer has any previous service requests
			previous_requests = frappe.get_all("Service Request",
				filters={"customer": self.customer, "name": ["!=", self.name]},
				limit=1)
			
			if previous_requests:
				self.customer_type = "REGULAR"
			else:
				self.customer_type = "NEW"

	def check_open_requests(self):
		"""Check for open service requests for this customer"""
		if self.customer and self.is_new():
			open_requests = frappe.get_all("Service Request",
				filters={
					"customer": self.customer,
					"status": ["in", ["Open", "In Progress", "On Hold"]],
					"walkin_status": "Accepted"
				},
				fields=["name", "service_date", "device_item_name", "status"])
			
			self.open_requests_count = len(open_requests)
			
			# Warning if open requests exist
			if open_requests and self.walkin_status == "Accepted":
				request_nos = ", ".join([d.name for d in open_requests])
				frappe.msgprint(
					_("This customer has {0} open service request(s): {1}").format(
						len(open_requests), request_nos),
					title=_("Open Requests Found"),
					indicator="orange"
				)

	def fetch_customer_details(self):
		"""Fetch customer contact details and addresses"""
		if self.customer:
			customer = frappe.get_doc("Customer", self.customer)
			
			# Fetch primary contact if not provided
			if not self.contact_number or not self.email:
				contacts = frappe.get_all("Dynamic Link",
					filters={
						"link_doctype": "Customer",
						"link_name": self.customer,
						"parenttype": "Contact"
					},
					fields=["parent"],
					limit=1)
				
				if contacts:
					contact = frappe.get_doc("Contact", contacts[0].parent)
					if not self.contact_number and contact.mobile_no:
						self.contact_number = contact.mobile_no
					if not self.email and contact.email_id:
						self.email = contact.email_id

	def fetch_warranty_from_serial(self):
		"""Fetch warranty status from CH Sold Plan via ch_item_master warranty API.
		Falls back to Serial No warranty_expiry_date if no sold plans exist."""
		# Skip if warranty was already set by a warranty claim
		if self.flags.get("skip_warranty_fetch"):
			return
		if not self.serial_no:
			if not self.warranty_status:
				self.warranty_status = "No Warranty"
			return

		# Validate serial no belongs to the device item
		serial = frappe.get_doc("Serial No", self.serial_no)
		if self.device_item and serial.item_code != self.device_item:
			frappe.throw(_("Serial No {0} does not belong to Item {1}").format(
				self.serial_no, self.device_item))

		# Try CH Sold Plan lookup via warranty API
		try:
			from ch_item_master.ch_item_master.warranty_api import check_warranty
			result = check_warranty(serial_no=self.serial_no, company=self.company)

			if result.get("warranty_covered"):
				self.warranty_status = "Under Warranty"
				covering = result.get("covering_plan") or {}
				self.warranty_plan = covering.get("warranty_plan")
				self.warranty_plan_name = covering.get("plan_title")
				self.warranty_deductible = covering.get("deductible_amount")
				self.warranty_expiry_date = covering.get("end_date")
			else:
				# No active sold plan — fallback to Serial No expiry
				self._fallback_warranty_from_serial(serial)
		except ImportError:
			# ch_item_master not installed — use basic Serial No lookup
			self._fallback_warranty_from_serial(serial)
		except Exception:
			# GF-6 fix: Any other error (DB, config, etc) — fallback gracefully instead of blocking
			frappe.log_error(frappe.get_traceback(), f"Warranty lookup failed for {self.serial_no}")
			self._fallback_warranty_from_serial(serial)

	def _notify_customer_on_status_change(self, old_status, new_status):
		"""GF-11: Send email/SMS notification to customer on key status transitions."""
		# Only notify on customer-relevant transitions
		notify_statuses = {
			"Received", "Diagnosis Complete", "Repair In Progress",
			"QC Pass", "Ready for Delivery", "Delivered",
			"Not Repairable", "Customer Cancelled",
		}
		if new_status not in notify_statuses:
			return

		customer_email = self.get("customer_email") or self.get("email")
		customer_mobile = self.get("customer_mobile") or self.get("mobile_no")
		customer_name = self.get("customer_name") or "Customer"

		subject = f"Service Request {self.name} — Status Update: {new_status}"
		message = (
			f"Dear {customer_name},<br><br>"
			f"Your service request <b>{self.name}</b> for <b>{self.get('item_name', 'your device')}</b> "
			f"has been updated to: <b>{new_status}</b>.<br><br>"
			f"Previous status: {old_status}<br><br>"
			f"Thank you for choosing GoFix Services."
		)

		if customer_email:
			try:
				frappe.sendmail(
					recipients=[customer_email],
					subject=subject,
					message=message,
					reference_doctype="Service Request",
					reference_name=self.name,
					now=True,
				)
			except Exception:
				frappe.log_error(frappe.get_traceback(),
								f"SR {self.name} customer email notification failed")

		if customer_mobile:
			try:
				from frappe.core.doctype.sms_settings.sms_settings import send_sms
				sms_text = f"GoFix: Service Request {self.name} status updated to {new_status}."
				send_sms([customer_mobile], sms_text)
			except Exception:
				pass  # SMS is optional

	def _fallback_warranty_from_serial(self, serial):
		"""Basic warranty check from Serial No.warranty_expiry_date (legacy fallback)."""
		if serial.warranty_expiry_date:
			self.warranty_expiry_date = serial.warranty_expiry_date
			if getdate(serial.warranty_expiry_date) >= getdate(today()):
				self.warranty_status = "Under Warranty"
			else:
				self.warranty_status = "Out of Warranty"
		else:
			self.warranty_status = "No Warranty"

	def validate_dates(self):
		"""Validate service and completion dates"""
		# Prevent future dates for service request
		if getdate(self.service_date) > getdate(today()):
			frappe.throw(_("Service Request Date cannot be in the future"))
		
		# Validate expected completion date
		if self.expected_completion_date and self.service_date:
			if getdate(self.expected_completion_date) < getdate(self.service_date):
				frappe.throw(_("Expected Completion Date cannot be before Service Request Date"))
		
		# Validate received datetime
		if self.received_datetime:
			from frappe.utils import get_datetime, now_datetime
			if get_datetime(self.received_datetime) > now_datetime():
				frappe.throw(_("Received Date & Time cannot be in the future"))
			
			# Validate expected completion datetime >= received datetime
			expected_completion_time = self.get("expected_completion_time")
			if self.expected_completion_date and expected_completion_time:
				expected_dt = get_datetime(f"{self.expected_completion_date} {expected_completion_time}")
				if expected_dt < get_datetime(self.received_datetime):
					frappe.throw(_("Expected Completion Date & Time must be after Received Date & Time"))
		
		# Validate actual completion date
		if self.get("actual_completion_date") and self.service_date:
			if getdate(self.get("actual_completion_date")) < getdate(self.service_date):
				frappe.throw(_("Actual Completion Date cannot be before Service Request Date"))

	def validate_withdrawal(self):
		"""Validate withdrawal reason is provided if status is withdrawn"""
		if self.walkin_status == "Withdrawn":
			if not self.withdrawal_reason:
				frappe.throw(_("Withdrawal Reason is mandatory when Walk-in Status is Withdrawn"))
			
			# Auto-cancel if withdrawn
			if self.status not in ["Cancelled"]:
				self.status = "Cancelled"
	
	def sync_walkin_status(self):
		"""Auto-sync walk-in status based on decision"""
		# If decision is Accepted, set walk-in status to Accepted (customer left device)
		if self.decision == "Accepted" and not self.walkin_status:
			self.walkin_status = "Accepted"
		# If decision is Draft and walk-in status is not explicitly set, leave it blank
		elif self.decision == "Draft" and self.walkin_status == "Accepted":
			# Don't auto-clear if user explicitly set it
			pass
		# If rejected/cancelled, clear walk-in status unless explicitly withdrawn
		elif self.decision in ["Rejected", "Cancelled"] and self.walkin_status == "Accepted":
			self.walkin_status = None
	
	def sync_decision_to_status(self):
		"""Sync decision field to status for consistency"""
		if self.decision:
			self.status = self.decision
	
	def on_update_after_submit(self):
		"""Handle post-submission updates - mainly for Accept/Reject"""
		# If decision changed to Accepted and no service order exists
		if self.decision == "Accepted" and not self.service_order:
			self.create_service_order()

		if self.is_completed_status():
			self.ensure_completion_artifacts()
			# INT-1 fix: Sync completion back to linked Warranty Claim
			self._sync_warranty_claim_completion()
			# INT-2 fix: Update serial lifecycle status to "Repaired"
			self._update_serial_lifecycle_on_completion()

	def is_completed_status(self):
		return self.status == "Completed" or self.decision == "Completed"

	def ensure_completion_artifacts(self):
		"""Create the billing and stock artifacts expected at repair completion."""
		completion_date = self.get("actual_completion_date") or today()

		if self.meta.has_field("actual_completion_date") and not self.get("actual_completion_date"):
			self.db_set("actual_completion_date", completion_date, update_modified=False)
			self.set("actual_completion_date", completion_date)

		if self.meta.has_field("repair_warranty_expiry") and not self.repair_warranty_expiry:
			warranty_days = self.repair_warranty_days or 30
			self.db_set(
				"repair_warranty_expiry",
				add_days(completion_date, warranty_days),
				update_modified=False,
			)
			self.repair_warranty_expiry = add_days(completion_date, warranty_days)

		if not self.get("service_invoice"):
			self.create_service_invoice()

		if self.spare_parts and not self.get("stock_entry"):
			self.create_stock_entry()

	def _sync_warranty_claim_completion(self):
		"""INT-1: When Service Request is completed, mark the linked Warranty Claim as repair-complete."""
		try:
			claim_name = frappe.db.get_value(
				"CH Warranty Claim", {"service_request": self.name, "docstatus": 1}, "name"
			)
			if not claim_name:
				return
			claim = frappe.get_doc("CH Warranty Claim", claim_name)
			if claim.claim_status in ("Ticket Created", "In Repair", "Sent to Manufacturer"):
				claim.mark_repair_complete(
					remarks=_("Auto-completed via Service Request {0}").format(self.name)
				)
		except Exception:
			frappe.log_error(
				frappe.get_traceback(),
				_("Failed to sync warranty claim for SR {0}").format(self.name),
			)

	def _update_serial_lifecycle_on_completion(self):
		"""INT-2: Update serial lifecycle status to 'Repaired' when repair is completed."""
		serial_no = self.get("serial_no")
		if not serial_no:
			return
		try:
			from ch_item_master.ch_item_master.doctype.ch_serial_lifecycle.ch_serial_lifecycle import (
				update_lifecycle_status,
			)
			update_lifecycle_status(
				serial_no=serial_no,
				new_status="Repaired",
				company=self.company,
				remarks=_("Repair completed via Service Request {0}").format(self.name),
			)
		except ImportError:
			pass  # ch_item_master not installed
		except Exception:
			frappe.log_error(
				frappe.get_traceback(),
				_("Failed to update serial lifecycle for SR {0}").format(self.name),
			)

	def create_service_order(self):
		"""Create Service Order (Sales Order) from accepted Service Request"""
		if self.service_order:
			frappe.throw(_("Service Order already exists: {0}").format(self.service_order))
		
		# Validate mandatory fields before creating Service Order
		if not self.estimated_cost or self.estimated_cost <= 0:
			frappe.throw(_("Estimated Cost is mandatory before accepting Service Request. Please enter the estimated repair cost."))
		
		if not self.expected_completion_date:
			frappe.throw(_("Expected Completion Date is mandatory before accepting Service Request. Please set the expected delivery date."))
		
		# Create Sales Order as Service Order
		so = frappe.new_doc("Sales Order")
		so.customer = self.customer
		so.company = self.company
		so.transaction_date = frappe.utils.today()
		# Use the expected completion date from Service Request
		so.delivery_date = self.expected_completion_date
		
		# Set company address for GST compliance (required for India GST)
		company_address = frappe.db.get_value("Dynamic Link",
			{
				"link_doctype": "Company",
				"link_name": self.company,
				"parenttype": "Address"
			},
			"parent")
		
		if not company_address:
			# Fallback: Get any company address marked as preferred billing
			company_address = frappe.db.get_value("Address",
				{
					"is_your_company_address": 1,
					"disabled": 0
				},
				"name")
		
		if company_address:
			so.company_address = company_address
		
		# Set title
		so.title = f"Service - {self.customer_name} - {self.serial_no or self.device_item_name}"
		
		# Mark as Service Order
		so.is_service_order = 1
		
		# Link to Service Request
		so.service_request = self.name
		
		# Copy Device Information
		so.device_brand = self.brand
		so.device_model = self.device_item_name
		so.imei_serial_no = self.serial_no
		so.device_condition = self.device_condition
		so.device_condition_desc = self.product_condition_desc
		so.accessories_received = self.accessories_received
		
		# Copy Issue Information
		so.issue_category = self.issue_category
		so.issue_description = self.issue_description
		
		# Copy customer-reported issues for comparison with technician findings
		so.customer_reported_issues = self.issue_description
		
		# Copy Security Information
		so.password_pattern = self.password if self.password else ""
		if self.pattern:
			so.password_pattern += f"\nPattern: {self.pattern}" if so.password_pattern else f"Pattern: {self.pattern}"
		so.backup_status = self.backup_info
		so.actual_imei = self.actual_imei
		
		# Copy Service Planning
		so.service_priority = self.priority
		so.warranty_status = self.warranty_status
		so.warranty_expiry_date = self.warranty_expiry_date
		so.warranty_plan = self.warranty_plan
		so.warranty_deductible = self.warranty_deductible
		so.estimated_delivery_date = frappe.utils.add_days(frappe.utils.today(), 7)  # Default 7 days
		
		# Copy Warehouse/Location
		so.set_warehouse = self.source_warehouse
		so.current_location = self.current_location
		so.state_name = self.state_name
		so.state_code = self.state_code
		
		# Set QC Status to Pending
		so.qc_status = "Pending"
		
		# Set Delivery Mode default
		so.delivery_mode = "Pick-up"
		
		# Add service item
		# Find a non-stock service item
		service_item = frappe.db.get_value("Item", {"is_stock_item": 0}, "item_code")
		
		if not service_item:
			# Fallback to device item if no service item found
			service_item = self.device_item
		
		so.append('items', {
			'item_code': service_item,
			'item_name': f'Service Repair - {self.device_item_name}',
			'description': self.issue_description,
			'qty': 1.0,
			'rate': float(self.estimated_cost),
			'warehouse': self.source_warehouse
		})
		
		# Let ERPNext set missing values (company address, tax template, etc.)
		so.set_missing_values()
		
		# Save and link
		try:
			so.insert(ignore_permissions=True)
			
			# Update Service Request with Service Order link using db_set (document is submitted)
			self.db_set("service_order", so.name, update_modified=False)
			
			frappe.msgprint(_("Service Order {0} created successfully").format(so.name),
				title=_("Success"),
				indicator="green")
			
			return so.name
		except Exception as e:
			frappe.log_error(f"Error creating Service Order: {str(e)}")
			frappe.throw(_("Error creating Service Order: {0}").format(str(e)))

	def calculate_costs(self):
		"""Calculate total costs from service items and spare parts"""
		total_estimated = 0
		
		# Calculate from service items
		for item in self.service_items:
			if item.estimated_cost:
				total_estimated += flt(item.estimated_cost)
		
		# Calculate from spare parts
		for part in self.spare_parts:
			if part.rate and part.qty:
				part.amount = flt(part.rate) * flt(part.qty)
				total_estimated += flt(part.amount)
		
		self.total_estimated_cost = total_estimated

	def on_submit(self):
		"""Actions on submission"""
		if self.is_completed_status():
			self.ensure_completion_artifacts()

	def on_cancel(self):
		"""Cancel related documents"""
		# Cancel service invoice if exists
		if self.service_invoice:
			invoice = frappe.get_doc("Sales Invoice", self.service_invoice)
			if invoice.docstatus == 1:
				frappe.throw(_("Please cancel Sales Invoice {0} first").format(self.service_invoice))
		
		# Cancel stock entry if exists
		if self.stock_entry:
			stock_entry = frappe.get_doc("Stock Entry", self.stock_entry)
			if stock_entry.docstatus == 1:
				frappe.throw(_("Please cancel Stock Entry {0} first").format(self.stock_entry))

	@frappe.whitelist()
	def get_device_details(self):
		"""Fetch device item details"""
		if self.device_item:
			item = frappe.get_doc("Item", self.device_item)
			self.device_item_name = item.item_name
			self.brand = item.brand
			
			# Fetch serial nos for this item
			serial_nos = frappe.get_all("Serial No",
				filters={"item_code": self.device_item, "status": "Delivered"},
				fields=["name", "warehouse"])
			
			return serial_nos

	@frappe.whitelist()
	def get_open_requests(self):
		"""Get list of open requests for this customer"""
		if self.customer:
			open_requests = frappe.get_all("Service Request",
				filters={
					"customer": self.customer,
					"status": ["in", ["Open", "In Progress", "On Hold"]],
					"walkin_status": "Accepted",
					"name": ["!=", self.name]
				},
				fields=["name", "service_date", "device_item_name", "status", 
						"advance_amount", "issue_description", "assigned_technician"],
				order_by="service_date desc")
			
			return open_requests

	def create_service_invoice(self):
		"""Create Sales Invoice for completed service with service items and spare parts"""
		if self.get("service_invoice"):
			return
		
		if not self.is_completed_status():
			frappe.throw(_("Service Invoice can only be created for Completed requests"))
		
		items = self.get_service_invoice_items()
		if not items:
			frappe.throw(_("No service items or spare parts to invoice"))

		posting_date = self.get("actual_completion_date") or today()
		
		# Create invoice
		invoice = frappe.get_doc({
			"doctype": "Sales Invoice",
			"customer": self.customer,
			"company": self.company,
			"posting_date": posting_date,
			"due_date": posting_date,
			"items": items,
			"remarks": f"Service Invoice for Service Request {self.name}"
		})
		
		# Apply advance if exists
		if self.advance_amount:
			invoice.is_pos = 0
			invoice.append("advances", {
				"reference_type": "Service Request",
				"reference_name": self.name,
				"advance_amount": self.advance_amount,
				"allocated_amount": min(self.advance_amount, invoice.grand_total)
			})
		
		invoice.insert(ignore_permissions=True)
		invoice.submit()
		
		self._set_optional_field("service_invoice", invoice.name)
		self.db_set("status", "Invoiced", update_modified=True)
		self.db_set("decision", "Invoiced", update_modified=False)
		
		frappe.msgprint(_("Service Invoice {0} created successfully").format(invoice.name))

	def get_service_invoice_items(self):
		items = []

		for service_item in self.service_items:
			items.append({
				"item_code": service_item.service_item,
				"item_name": service_item.item_name,
				"description": service_item.description or service_item.item_name,
				"qty": 1,
				"rate": service_item.actual_cost or service_item.estimated_cost or 0,
				"uom": "Nos"
			})

		for spare_part in self.spare_parts:
			items.append({
				"item_code": spare_part.spare_part_item,
				"item_name": spare_part.item_name,
				"description": spare_part.description or spare_part.item_name,
				"qty": spare_part.qty,
				"rate": spare_part.rate,
				"uom": spare_part.uom
			})

		if items:
			return items

		if self.service_order:
			service_order = frappe.get_doc("Sales Order", self.service_order)
			for row in service_order.items:
				item_row = {
					"item_code": row.item_code,
					"item_name": row.item_name,
					"description": row.description or row.item_name,
					"qty": row.qty or 1,
					"rate": row.rate,
					"uom": row.uom,
				}
				if service_order.docstatus == 1:
					item_row["sales_order"] = service_order.name
					item_row["so_detail"] = row.name
				items.append(item_row)

		return items

	def create_stock_entry(self):
		"""Create Stock Entry to consume spare parts"""
		if self.get("stock_entry"):
			return
		
		if not self.spare_parts:
			return
		
		items = []
		for spare_part in self.spare_parts:
			# Get default warehouse
			item = frappe.get_doc("Item", spare_part.spare_part_item)
			if item.is_stock_item:
				source_warehouse = self.source_warehouse or frappe.db.get_value(
					"Item Default",
					{"parent": spare_part.spare_part_item, "company": self.company},
					"default_warehouse",
				) or item.default_warehouse
				if not source_warehouse:
					frappe.throw(
						_("Warehouse is required to issue spare part {0}").format(spare_part.spare_part_item)
					)
				items.append({
					"item_code": spare_part.spare_part_item,
					"qty": spare_part.qty,
					"uom": spare_part.uom,
					"basic_rate": spare_part.rate,
					"s_warehouse": source_warehouse,
				})
		
		if not items:
			return
		
		# Create material issue stock entry
		stock_entry = frappe.get_doc({
			"doctype": "Stock Entry",
			"stock_entry_type": "Material Issue",
			"company": self.company,
			"posting_date": self.get("actual_completion_date") or today(),
			"items": items,
			"remarks": f"Spare parts consumed for Service Request {self.name}"
		})
		
		stock_entry.insert(ignore_permissions=True)
		stock_entry.submit()
		
		self._set_optional_field("stock_entry", stock_entry.name)
		
		frappe.msgprint(_("Stock Entry {0} created successfully").format(stock_entry.name))

	def _set_optional_field(self, fieldname, value):
		self.set(fieldname, value)
		if self.meta.has_field(fieldname):
			self.db_set(fieldname, value, update_modified=False)

	def validate_contact_details(self):
		"""Validate mobile number and email format using Frappe built-ins"""
		from buyback.utils import validate_indian_phone
		from frappe.utils import validate_email_address

		if self.contact_number:
			self.contact_number = validate_indian_phone(self.contact_number, "Mobile Number")

		if self.alternate_contact:
			self.alternate_contact = validate_indian_phone(self.alternate_contact, "Alternate Contact")

		# Use Frappe's built-in email validator
		if self.email:
			validate_email_address(self.email, throw=True)

	def validate_courier_details(self):
		"""Validate courier details are mandatory if delivery mode is Courier"""
		if self.get("delivery_mode") == "Courier":
			if not self.get("courier_name"):
				frappe.throw(_("Courier Name is mandatory when Delivery Mode is Courier"))
			if not self.get("delivery_address"):
				frappe.msgprint(_("Warning: Delivery Address is not provided"), 
					indicator="orange", alert=True)

	def validate_referral_code(self):
		"""Validate referral code expiry and customer type"""
		if self.referral_code:
			# Check expiry date
			if self.referral_expiry_date:
				if getdate(self.referral_expiry_date) < getdate(today()):
					frappe.throw(_("Referral Code has expired on {0}").format(
						frappe.format(self.referral_expiry_date, {"fieldtype": "Date"})))
			
			# Referral codes typically only for new customers
			if self.customer_type == "REGULAR":
				frappe.msgprint(_("Warning: Referral Code is generally applicable for NEW customers only"), 
					indicator="orange", alert=True)

	def validate_mandatory_fields(self):
		"""Validate mandatory fields based on Delphi requirements - only for submission"""
		# Only enforce these validations on submit
		if self.docstatus == 0:
			return

		# Skip mandatory checks for withdrawn/cancelled SRs — customer took device back
		if self.walkin_status == "Withdrawn" or self.status == "Cancelled":
			return
		
		# Product condition description is mandatory for submission
		if not self.product_condition_desc:
			frappe.throw(_("Product Condition Description is mandatory before submission"))
		
		# Backup info is mandatory for submission
		if not self.backup_info:
			frappe.throw(_("Backup Information is mandatory before submission. Please specify what data was backed up."))
		
		# Fault/Issue description is mandatory
		if not self.issue_description:
			frappe.throw(_("Issue Description is mandatory"))
		
		# State name and code mandatory for GST compliance
		if not self.state_name or not self.state_code:
			error_msg = _("State Name and State Code are mandatory for GST compliance")
			
			# Helpful hint if warehouse selected but no address
			if self.source_warehouse:
				warehouse_doc = frappe.get_doc("Warehouse", self.source_warehouse)
				if not warehouse_doc.address:
					error_msg += f"<br><br><b>Solution:</b> Warehouse '{self.source_warehouse}' has no address linked. Please:<br>"
					error_msg += "1. Go to Warehouse master and link an Address with State details, OR<br>"
					error_msg += "2. Manually enter State Name and State Code in this form"
			
			frappe.throw(error_msg)

	def validate_backdating(self):
		"""Control backdating - require approval if more than 3 days old"""
		from frappe.utils import date_diff
		
		if self.is_new():
			days_diff = date_diff(today(), self.service_date)
			
			if days_diff > 3:
				# Check if user has permission to backdate
				if not frappe.has_permission("Service Request", "write", user=frappe.session.user):
					frappe.throw(_("Service Request Date is more than 3 days old. Backdating requires System Manager approval."))
				
				# Log the backdating
				frappe.msgprint(
					_("Warning: Service Request is being backdated by {0} days").format(days_diff),
					indicator="red",
					alert=True
				)

	def validate_delivery_date(self):
		"""Validate delivery date is not before received date"""
		if self.expected_delivery_date and self.service_date:
			if getdate(self.expected_delivery_date) < getdate(self.service_date):
				frappe.throw(_("Expected Delivery Date cannot be before Service Date"))
	
	def validate_issue_solution_cascade(self):
		"""Validate the Issue → Solution → Spare cascade.
		- Each solution must reference an issue_category present in issue_lines
		- Each spare must reference a solution present in solution_lines
		- Each spare must be a mapped spare for that solution (Solution Spare Mapping)
		"""
		issue_lines = self.get("issue_lines") or []
		solution_lines = self.get("solution_lines") or []
		spare_lines = self.get("spare_lines") or []

		# Nothing to validate if no child rows
		if not issue_lines and not solution_lines and not spare_lines:
			return

		# Collect valid issue categories from issue_lines
		valid_issue_categories = set()
		for row in issue_lines:
			if row.issue_category:
				valid_issue_categories.add(row.issue_category)

		# Validate solution_lines: each must have issue_category in issue_lines
		valid_solutions = set()
		for row in solution_lines:
			if not row.repair_solution:
				continue
			valid_solutions.add(row.repair_solution)
			if not row.issue_category:
				# fetch issue_category from the solution master
				row.issue_category = frappe.db.get_value("Repair Solution", row.repair_solution, "issue_category")
			if row.issue_category and row.issue_category not in valid_issue_categories:
				frappe.throw(
					_("Row {0}: Solution '{1}' belongs to issue category '{2}' which is not in the Issue Lines").format(
						row.idx, row.repair_solution, row.issue_category
					)
				)

		# Validate spare_lines: each must reference a solution in solution_lines
		for row in spare_lines:
			if not row.repair_solution:
				continue
			if row.repair_solution not in valid_solutions:
				frappe.throw(
					_("Row {0}: Spare '{1}' references solution '{2}' which is not in the Solution Lines").format(
						row.idx, row.spare_item or "", row.repair_solution
					)
				)
			# Validate spare is an allowed spare for this solution
			if row.spare_item:
				is_mapped = frappe.db.exists("Solution Spare Mapping", {
					"repair_solution": row.repair_solution,
					"spare_item": row.spare_item,
					"is_active": 1
				})
				if not is_mapped:
					frappe.throw(
						_("Row {0}: Spare '{1}' is not a mapped spare for solution '{2}'. Configure it in Solution Spare Mapping.").format(
							row.idx, row.spare_item, row.repair_solution
						)
					)

			# Calculate amount = qty * rate
			row.amount = flt(row.qty) * flt(row.rate)

	def generate_barcode(self):
		"""Auto-generate barcode/IMEI with prefix based on device category"""
		# Only generate if barcode field is empty and not already generated
		if self.is_barcode_generated or self.serial_no:
			return
		
		if not self.device_item:
			return
		
		# Get item details to determine category
		item = frappe.get_cached_doc("Item", self.device_item)
		item_group = item.item_group or ""
		
		# Determine prefix based on item group
		prefix = self.get_barcode_prefix(item_group)
		
		# Generate barcode: PREFIX/YYMMDD#####
		from frappe.utils import now_datetime
		date_str = now_datetime().strftime("%y%m%d")
		
		# Get next sequence number for this date and prefix
		sequence = self.get_next_barcode_sequence(prefix, date_str)
		
		# Generate barcode
		barcode = f"{prefix}/{date_str}{sequence:05d}"
		
		# Set the barcode to serial_no field (or create a new serial no)
		if not self.serial_no:
			self.serial_no = barcode
			self.is_barcode_generated = 1
			
			# Create Serial No document if it doesn't exist
			self.create_serial_no_document(barcode, item)
	
	def get_barcode_prefix(self, item_group):
		"""Determine barcode prefix based on item group"""
		item_group_lower = item_group.lower()
		
		if any(keyword in item_group_lower for keyword in ["mobile", "phone", "smartphone"]):
			return "MO"
		elif any(keyword in item_group_lower for keyword in ["spare", "part", "component"]):
			return "SP"
		elif any(keyword in item_group_lower for keyword in ["tv", "television", "lcd", "led"]):
			return "TV"
		elif any(keyword in item_group_lower for keyword in ["accessory", "accessories", "cable", "charger", "adapter"]):
			return "AC"
		else:
			# Default to MO for general items
			return "MO"
	
	def get_next_barcode_sequence(self, prefix, date_str):
		"""Get next sequence number for barcode generation.
		Uses an advisory lock to prevent race conditions with concurrent requests.
		"""
		lock_name = f"barcode_seq_{prefix}_{date_str}"
		# GF-7 fix: Acquire advisory lock to prevent duplicate sequence numbers
		frappe.db.sql("SELECT GET_LOCK(%s, 10)", (lock_name,))
		try:
			range_start = f"{prefix}/{date_str}"
			range_end = f"{prefix}/{date_str}\xff"
			last_barcode = frappe.db.sql("""
				SELECT serial_no
				FROM `tabService Request`
				WHERE serial_no >= %s AND serial_no < %s
				ORDER BY serial_no DESC
				LIMIT 1
				FOR UPDATE
			""", (range_start, range_end), as_dict=True)

			if last_barcode and last_barcode[0].serial_no:
				try:
					last_seq_str = last_barcode[0].serial_no.split(date_str)[1]
					last_seq = int(last_seq_str)
					return last_seq + 1
				except (IndexError, ValueError):
					return 1

			return 1
		finally:
			frappe.db.sql("SELECT RELEASE_LOCK(%s)", (lock_name,))
	
	def create_serial_no_document(self, barcode, item):
		"""Create Serial No document for the generated barcode"""
		try:
			if not frappe.db.exists("Serial No", barcode):
				serial_no = frappe.get_doc({
					"doctype": "Serial No",
					"serial_no": barcode,
					"item_code": item.item_code,
					"item_name": item.item_name,
					"description": f"Auto-generated for Service Request",
					"status": "Active",
					"company": self.company or frappe.defaults.get_user_default("Company")
				})
				serial_no.insert(ignore_permissions=True)
				frappe.msgprint(
					_("Barcode {0} generated and Serial No created").format(barcode),
					indicator="green",
					alert=True
				)
		except Exception as e:
			frappe.log_error(message=str(e), title="Serial No Creation Error")
			frappe.msgprint(
				_("Barcode generated but Serial No creation failed: {0}").format(str(e)),
				indicator="orange",
				alert=True
			)

	# ── Repeat Complaint Detection ───────────────────────────────────

	def _detect_repeat_complaint(self):
		"""Detect if same device + same issue category was serviced within last 30 days.

		Sets is_repeat_complaint flag and links previous SR for audit trail.
		"""
		if not self.serial_no or not self.issue_category:
			return

		# Only check on new requests or when not already flagged
		if self.get("is_repeat_complaint"):
			return

		repeat_window_days = 30

		previous = frappe.db.sql("""
			SELECT name, service_date, issue_category, decision
			FROM `tabService Request`
			WHERE serial_no = %s
			  AND issue_category = %s
			  AND name != %s
			  AND service_date >= DATE_SUB(%s, INTERVAL %s DAY)
			  AND decision IN ('Completed', 'Delivered', 'Invoiced')
			ORDER BY service_date DESC
			LIMIT 1
		""", (self.serial_no, self.issue_category, self.name or "",
			  self.service_date or today(), repeat_window_days), as_dict=True)

		if previous:
			self._set_optional_field("is_repeat_complaint", 1)
			self._set_optional_field("previous_service_request", previous[0].name)
			frappe.msgprint(
				_("⚠️ Repeat Complaint Detected — Same device & issue category was serviced on {0} "
				  "(SR: {1}). This may indicate incomplete previous repair.").format(
					previous[0].service_date, previous[0].name),
				title=_("Repeat Complaint"),
				indicator="red",
			)

	# ── Exception Framework: Serial Substitution (#8) ────────────────

	def _validate_serial_substitution(self):
		"""If replacement_serial_no is set, require approval."""
		if not self.replacement_serial_no or not self.original_serial_no:
			return
		if self.replacement_serial_no == self.original_serial_no:
			return

		if not self.substitution_approved_by:
			frappe.throw(
				_("Serial substitution from {0} to {1} requires manager approval.").format(
					frappe.bold(self.original_serial_no),
					frappe.bold(self.replacement_serial_no),
				),
				title=_("Serial Substitution — Approval Required"),
			)

		# Log exception request
		if not self.substitution_exception_request:
			self._create_serial_sub_exception()

	def _create_serial_sub_exception(self):
		"""Create a CH Exception Request for serial substitution."""
		if not frappe.db.exists("CH Exception Type", "Serial Substitution"):
			# GF-2 fix: Warn instead of silently returning
			frappe.msgprint(
				_("CH Exception Type 'Serial Substitution' not found. "
				  "Exception request was not created. Please configure it in CH Item Master."),
				indicator="orange", alert=True,
			)
			return
		try:
			from ch_item_master.ch_item_master.exception_api import raise_exception, approve_exception
			result = raise_exception(
				exception_type="Serial Substitution",
				company=self.company,
				reason=self.substitution_reason or "Serial substitution",
				serial_no=self.original_serial_no,
				reference_doctype="Service Request",
				reference_name=self.name,
				store_warehouse=self.source_warehouse,
				customer=self.customer,
			)
			if result and result.get("name"):
				self.substitution_exception_request = result["name"]
				if self.substitution_approved_by and result.get("status") == "Pending":
					approve_exception(
						exception_name=result["name"],
						approver_user=self.substitution_approved_by,
						channel="Manager PIN",
						remarks=f"Old: {self.original_serial_no} → New: {self.replacement_serial_no}",
					)
		except ImportError:
			# GF-2 fix: Explicit error when ch_item_master not installed
			frappe.throw(
				_("ch_item_master app is required for serial substitution exceptions but is not installed."),
				title=_("Missing App Dependency"),
			)
		except Exception:
			frappe.log_error("Serial substitution exception creation failed")
			frappe.throw(
				_("Failed to create serial substitution exception request. Check error log."),
				title=_("Exception Request Failed"),
			)

	# ── Exception Framework: Service Discount (#10) ──────────────────

	def _validate_service_discount(self):
		"""If service discount is applied, require approval."""
		if not flt(self.service_discount_percent) and not flt(self.service_discount_amount):
			return

		if not self.discount_approved_by:
			frappe.throw(
				_("Service discount of {0}% / ₹{1} requires manager approval.").format(
					self.service_discount_percent or 0,
					self.service_discount_amount or 0,
				),
				title=_("Service Discount — Approval Required"),
			)

		if not self.discount_exception_request:
			self._create_service_discount_exception()

	def _create_service_discount_exception(self):
		"""Create a CH Exception Request for service discount."""
		if not frappe.db.exists("CH Exception Type", "Service Discount"):
			frappe.msgprint(
				_("CH Exception Type 'Service Discount' not found. "
				  "Exception request was not created. Please configure it in CH Item Master."),
				indicator="orange", alert=True,
			)
			return
		try:
			from ch_item_master.ch_item_master.exception_api import raise_exception, approve_exception
			result = raise_exception(
				exception_type="Service Discount",
				company=self.company,
				reason=self.discount_approval_reason or "Service discount",
				requested_value=flt(self.service_discount_amount) or flt(self.estimated_cost) * flt(self.service_discount_percent) / 100,
				original_value=flt(self.estimated_cost),
				reference_doctype="Service Request",
				reference_name=self.name,
				store_warehouse=self.source_warehouse,
				customer=self.customer,
			)
			if result and result.get("name"):
				self.discount_exception_request = result["name"]
				if self.discount_approved_by and result.get("status") == "Pending":
					approve_exception(
						exception_name=result["name"],
						approver_user=self.discount_approved_by,
						channel="Manager PIN",
					)
		except ImportError:
			frappe.throw(
				_("ch_item_master app is required for service discount exceptions but is not installed."),
				title=_("Missing App Dependency"),
			)
		except Exception:
			frappe.log_error("Service discount exception creation failed")
			frappe.throw(
				_("Failed to create service discount exception request. Check error log."),
				title=_("Exception Request Failed"),
			)


# API Methods
@frappe.whitelist()
def get_customer_details(customer):
	"""Get customer details including contact info (whitelisted for client-side calls)"""
	if not customer:
		return {}
	
	result = {}
	frappe.has_permission("Customer", "read", customer, throw=True)
	
	customer_doc = frappe.get_doc("Customer", customer)
	result['customer_name'] = customer_doc.customer_name
	result['gstin'] = customer_doc.gstin if hasattr(customer_doc, 'gstin') else None
	result['pan'] = customer_doc.pan if hasattr(customer_doc, 'pan') else None
	
	# Get primary contact
	contacts = frappe.get_all("Dynamic Link",
		filters={
			"link_doctype": "Customer",
			"link_name": customer,
			"parenttype": "Contact"
		},
		fields=["parent"],
		limit=1)
	
	if contacts:
		contact = frappe.get_doc("Contact", contacts[0].parent)
		result['mobile_no'] = contact.mobile_no
		result['email_id'] = contact.email_id
	
	return result

@frappe.whitelist()
def get_open_requests(name):
	"""Get open service requests for the same customer"""
	doc = frappe.get_doc("Service Request", name)
	doc.check_permission("read")
	
	if not doc.customer:
		return []
	
	# Get other open requests for this customer
	open_requests = frappe.get_all("Service Request",
		filters={
			"customer": doc.customer,
			"name": ["!=", name],
			"status": ["in", ["Open", "In Progress", "Waiting for Parts", "Ready for Delivery"]]
		},
		fields=["name", "service_date", "device_item_name", "status", "advance_amount"],
		order_by="service_date desc",
		limit=10)
	
	return open_requests

@frappe.whitelist()
def generate_barcode_manual(name):
	"""Manually generate barcode for a service request"""
	doc = frappe.get_doc("Service Request", name)
	doc.check_permission("write")
	
	# Temporarily reset the flag to allow regeneration
	doc.is_barcode_generated = 0
	doc.serial_no = None
	
	# Generate new barcode
	doc.generate_barcode()
	doc.save(ignore_permissions=True)
	
	return doc.serial_no

@frappe.whitelist()
def accept_service_request(service_request):
	"""Accept Service Request and create Service Order
	
	This method handles accepting submitted Service Requests
	"""
	frappe.only_for(["Sales Manager", "System Manager", "Service Manager"])
	doc = frappe.get_doc("Service Request", service_request)
	doc.check_permission("write")
	
	# Check if already accepted
	if doc.decision == "Accepted":
		if doc.service_order:
			frappe.msgprint(_("Service Request already accepted. Service Order: {0}").format(doc.service_order))
			return doc.service_order
	
	# Update decision using db_set to work with submitted docs
	doc.db_set("decision", "Accepted", update_modified=True)
	doc.db_set("accepted_by", frappe.session.user, update_modified=False)
	doc.db_set("accepted_datetime", frappe.utils.now(), update_modified=False)
	doc.db_set("walkin_status", "Accepted", update_modified=False)  # Customer left device
	
	# Create Service Order
	doc.reload()
	doc.create_service_order()
	
	# Update status
	doc.db_set("status", "In Service", update_modified=False)
	
	return doc.service_order

@frappe.whitelist()
def reject_service_request(service_request, rejection_reason):
	"""Reject Service Request
	
	This method handles rejecting submitted Service Requests
	"""
	frappe.only_for(["Sales Manager", "System Manager", "Service Manager"])
	doc = frappe.get_doc("Service Request", service_request)
	doc.check_permission("write")
	
	# Update decision using db_set to work with submitted docs
	doc.db_set("decision", "Rejected", update_modified=True)
	doc.db_set("rejection_reason", rejection_reason, update_modified=False)
	doc.db_set("status", "Rejected", update_modified=False)
	doc.db_set("walkin_status", None, update_modified=False)  # Clear walk-in status
	
	return True


def complete_service_request(service_request, completion_date=None):
	"""Mark a Service Request completed and create monetization artifacts."""
	doc = frappe.get_doc("Service Request", service_request)
	doc.check_permission("write")
	updates = {}

	if doc.status != "Completed":
		updates["status"] = "Completed"

	if doc.meta.has_field("decision") and doc.decision != "Completed":
		updates["decision"] = "Completed"

	if completion_date and doc.meta.has_field("actual_completion_date") and not doc.get("actual_completion_date"):
		updates["actual_completion_date"] = completion_date

	if updates:
		frappe.db.set_value("Service Request", doc.name, updates, update_modified=True)
		doc.reload()

	doc.ensure_completion_artifacts()
	return doc.name

@frappe.whitelist()
def get_warehouse_state(warehouse):
	"""Get state details from warehouse address
	
	Returns state_name and state_code for GST compliance
	"""
	if not warehouse:
		return {}
	
	try:
		warehouse_doc = frappe.get_doc("Warehouse", warehouse)
		
		# Try to get from warehouse address
		if warehouse_doc.address:
			address = frappe.get_doc("Address", warehouse_doc.address)
			return {
				"state_name": address.state or "",
				"state_code": address.get("gst_state_number") or ""
			}
		
		# Fallback: Try company address
		if warehouse_doc.company:
			company_addresses = frappe.get_all("Dynamic Link",
				filters={
					"link_doctype": "Company",
					"link_name": warehouse_doc.company,
					"parenttype": "Address"
				},
				fields=["parent"],
				limit=1)
			
			if company_addresses:
				address = frappe.get_doc("Address", company_addresses[0].parent)
				return {
					"state_name": address.state or "",
					"state_code": address.get("gst_state_number") or ""
				}
	except Exception as e:
		frappe.log_error(f"Error fetching warehouse state: {str(e)}")
	
	return {}


def flag_unclaimed_devices(days_threshold=15):
	"""Daily scheduler: Flag devices not picked up after completion.

	Service Requests with decision in (Completed, Invoiced) and no delivery
	date for more than *days_threshold* days are marked unclaimed.
	"""
	from frappe.utils import add_days, today

	cutoff = add_days(today(), -days_threshold)

	unclaimed = frappe.db.get_all(
		"Service Request",
		filters={
			"decision": ["in", ["Completed", "Invoiced"]],
			"unclaimed_flag": 0,
			"docstatus": 1,
			"modified": ["<=", cutoff],
		},
		pluck="name",
	)

	for name in unclaimed:
		frappe.db.set_value("Service Request", name, {
			"unclaimed_flag": 1,
			"unclaimed_date": today(),
		}, update_modified=False)

	if unclaimed:
		frappe.db.commit()
		frappe.logger("gofix").info(f"Flagged {len(unclaimed)} unclaimed devices")
