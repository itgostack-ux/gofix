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
		self.sync_decision_to_status()

	def before_save(self):
		"""Generate barcode if not exists and fetch warehouse details"""
		self.generate_barcode()
		# Fetch warehouse state details if missing
		if self.source_warehouse and (not self.state_name or not self.state_code):
			self.fetch_warehouse_details()
		# Sync walk-in status with decision
		self.sync_walkin_status()
	
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
			if self.expected_completion_date and self.expected_completion_time:
				expected_dt = get_datetime(f"{self.expected_completion_date} {self.expected_completion_time}")
				if expected_dt < get_datetime(self.received_datetime):
					frappe.throw(_("Expected Completion Date & Time must be after Received Date & Time"))
		
		# Validate actual completion date
		if self.get("actual_completion_date") and self.service_date:
			if getdate(self.actual_completion_date) < getdate(self.service_date):
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
		if self.status == "Completed":
			self.create_service_invoice()
			if self.spare_parts:
				self.create_stock_entry()

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
		if self.service_invoice:
			return
		
		if self.status != "Completed":
			frappe.throw(_("Service Invoice can only be created for Completed requests"))
		
		items = []
		
		# Add service items
		for service_item in self.service_items:
			items.append({
				"item_code": service_item.service_item,
				"item_name": service_item.item_name,
				"description": service_item.description or service_item.item_name,
				"qty": 1,
				"rate": service_item.actual_cost or service_item.estimated_cost or 0,
				"uom": "Nos"
			})
		
		# Add spare parts
		for spare_part in self.spare_parts:
			items.append({
				"item_code": spare_part.spare_part_item,
				"item_name": spare_part.item_name,
				"description": spare_part.description or spare_part.item_name,
				"qty": spare_part.qty,
				"rate": spare_part.rate,
				"uom": spare_part.uom
			})
		
		if not items:
			frappe.throw(_("No service items or spare parts to invoice"))
		
		# Create invoice
		invoice = frappe.get_doc({
			"doctype": "Sales Invoice",
			"customer": self.customer,
			"company": self.company,
			"posting_date": self.actual_completion_date or today(),
			"due_date": self.actual_completion_date or today(),
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
		
		invoice.insert()
		invoice.submit()
		
		self.service_invoice = invoice.name
		self.db_set("service_invoice", invoice.name)
		
		frappe.msgprint(_("Service Invoice {0} created successfully").format(invoice.name))

	def create_stock_entry(self):
		"""Create Stock Entry to consume spare parts"""
		if self.stock_entry:
			return
		
		if not self.spare_parts:
			return
		
		items = []
		for spare_part in self.spare_parts:
			# Get default warehouse
			item = frappe.get_doc("Item", spare_part.spare_part_item)
			if item.is_stock_item:
				items.append({
					"item_code": spare_part.spare_part_item,
					"qty": spare_part.qty,
					"uom": spare_part.uom,
					"basic_rate": spare_part.rate
				})
		
		if not items:
			return
		
		# Create material issue stock entry
		stock_entry = frappe.get_doc({
			"doctype": "Stock Entry",
			"stock_entry_type": "Material Issue",
			"company": self.company,
			"posting_date": self.actual_completion_date or today(),
			"items": items,
			"remarks": f"Spare parts consumed for Service Request {self.name}"
		})
		
		stock_entry.insert()
		stock_entry.submit()
		
		self.stock_entry = stock_entry.name
		self.db_set("stock_entry", stock_entry.name)
		
		frappe.msgprint(_("Stock Entry {0} created successfully").format(stock_entry.name))

	def validate_contact_details(self):
		"""Validate mobile number and email format using Frappe built-ins"""
		from frappe.utils import validate_email_address
		
		# Validate mobile number (10 digits)
		if self.contact_number:
			import re
			mobile = re.sub(r'\D', '', self.contact_number)
			if len(mobile) != 10:
				frappe.throw(_("Mobile Number must be exactly 10 digits"))
		
		# Use Frappe's built-in email validator
		if self.email:
			validate_email_address(self.email, throw=True)

	def validate_courier_details(self):
		"""Validate courier details are mandatory if delivery mode is Courier"""
		if self.delivery_mode == "Courier":
			if not self.courier_name:
				frappe.throw(_("Courier Name is mandatory when Delivery Mode is Courier"))
			if not self.delivery_address:
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
		"""Get next sequence number for barcode generation"""
		# Find the last barcode with this prefix and date using range scan (B-tree friendly)
		range_start = f"{prefix}/{date_str}"
		range_end = f"{prefix}/{date_str}\xff"  # \xff sorts after all digit chars
		last_barcode = frappe.db.sql("""
			SELECT serial_no
			FROM `tabService Request`
			WHERE serial_no >= %s AND serial_no < %s
			ORDER BY serial_no DESC
			LIMIT 1
		""", (range_start, range_end), as_dict=True)
		
		if last_barcode and last_barcode[0].serial_no:
			# Extract sequence number from last barcode
			try:
				last_seq_str = last_barcode[0].serial_no.split(date_str)[1]
				last_seq = int(last_seq_str)
				return last_seq + 1
			except (IndexError, ValueError):
				return 1
		
		return 1
	
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


# API Methods
@frappe.whitelist()
def get_customer_details(customer):
	"""Get customer details including contact info (whitelisted for client-side calls)"""
	if not customer:
		return {}
	
	result = {}
	
	# Get customer with ignore_permissions since we're just reading data
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
	
	# Update decision using db_set to work with submitted docs
	doc.db_set("decision", "Rejected", update_modified=True)
	doc.db_set("rejection_reason", rejection_reason, update_modified=False)
	doc.db_set("status", "Rejected", update_modified=False)
	doc.db_set("walkin_status", None, update_modified=False)  # Clear walk-in status
	
	return True

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
