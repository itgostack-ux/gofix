# Copyright (c) 2024, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from frappe import _


class SparePartsUsage(Document):
	def validate(self):
		"""Validate spare parts usage"""
		self.validate_service_request()
		self.validate_barcode()
		self.set_line_seq_no()
		self.fetch_item_details()
	
	def validate_service_request(self):
		"""Validate that service request exists and is open"""
		if not self.service_request:
			frappe.throw(_("Service Request is mandatory"))
		
		service_request = frappe.get_doc("Service Request", self.service_request)
		if service_request.service_status == "Closed":
			frappe.throw(_("Cannot add spare parts to a closed Service Request"))
	
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
			frappe.throw(_("Barcode {0} is already used in this service request").format(self.barcode_value))
		
		# Check if barcode exists in stock and is available
		# This would integrate with your barcode master system
		# For now, we'll do a simple check
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
	
	def on_submit(self):
		"""Update stock and create stock entry"""
		self.create_stock_entry()
		self.update_spare_parts_count()
	
	def create_stock_entry(self):
		"""Create stock entry for spare part consumption"""
		if self.status != "Active":
			return
		
		# Create Stock Entry for consumption
		stock_entry = frappe.new_doc("Stock Entry")
		stock_entry.stock_entry_type = "Material Issue"
		stock_entry.purpose = "Material Issue"
		stock_entry.company = frappe.defaults.get_user_default("Company")
		
		stock_entry.append("items", {
			"item_code": self.spare_part_item,
			"qty": self.qty_used,
			"uom": self.uom,
			"basic_rate": self.purchase_cost,
			"s_warehouse": frappe.db.get_value("Item", self.spare_part_item, "default_warehouse"),
			"serial_no": self.barcode_value if self.barcode_value else None
		})
		
		try:
			stock_entry.insert(ignore_permissions=True)
			stock_entry.submit()
			
			frappe.msgprint(_("Stock Entry {0} created").format(stock_entry.name))
		except Exception as e:
			frappe.log_error(message=str(e), title="Spare Parts Stock Entry Error")
			frappe.msgprint(_("Could not create stock entry: {0}").format(str(e)), alert=True)
	
	def update_spare_parts_count(self):
		"""Update spare parts count in service request"""
		total_count = frappe.db.count("Spare Parts Usage", {
			"service_request": self.service_request
		})
		
		billable_count = frappe.db.count("Spare Parts Usage", {
			"service_request": self.service_request,
			"deleted": 0,
			"status": "Active"
		})
		
		frappe.db.set_value("Service Request", self.service_request, {
			"total_spares_used_count": total_count,
			"billable_spares_count": billable_count
		}, update_modified=False)
	
	def move_to_main_stock(self, reason):
		"""Move spare part back to main stock"""
		if self.status != "Active":
			frappe.throw(_("Can only move active spare parts"))
		
		self.status = "Moved to Main Stock"
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
			frappe.throw(_("Can only move active spare parts"))
		
		self.status = "Moved to Dispose Stock"
		self.deleted = 1
		self.narration = "Moved to Dispose Stock"
		self.reason_desc = reason
		self.moved_to_stock_type = "Dispose Stock"
		
		self.save(ignore_permissions=True)
		self.update_spare_parts_count()
		
		frappe.msgprint(_("Spare part moved to Dispose Stock"))
	
	def create_return_stock_entry(self):
		"""Create stock entry for returning spare to warehouse"""
		stock_entry = frappe.new_doc("Stock Entry")
		stock_entry.stock_entry_type = "Material Receipt"
		stock_entry.purpose = "Material Receipt"
		stock_entry.company = frappe.defaults.get_user_default("Company")
		
		stock_entry.append("items", {
			"item_code": self.spare_part_item,
			"qty": self.qty_used,
			"uom": self.uom,
			"basic_rate": self.purchase_cost,
			"t_warehouse": frappe.db.get_value("Item", self.spare_part_item, "default_warehouse"),
			"serial_no": self.barcode_value if self.barcode_value else None
		})
		
		try:
			stock_entry.insert(ignore_permissions=True)
			stock_entry.submit()
		except Exception as e:
			frappe.log_error(message=str(e), title="Spare Parts Return Stock Entry Error")


# API Methods
@frappe.whitelist()
def move_to_main_stock(name, reason):
	"""Whitelisted method to move spare part to main stock"""
	frappe.only_for(["Sales Manager", "System Manager", "Service Manager", "Sales User"])
	doc = frappe.get_doc("Spare Parts Usage", name)
	doc.move_to_main_stock(reason)
	return {"message": "Moved to Main Stock"}


@frappe.whitelist()
def move_to_dispose_stock(name, reason):
	"""Whitelisted method to move spare part to dispose stock"""
	frappe.only_for(["Sales Manager", "System Manager", "Service Manager"])
	doc = frappe.get_doc("Spare Parts Usage", name)
	doc.move_to_dispose_stock(reason)
	return {"message": "Moved to Dispose Stock"}
